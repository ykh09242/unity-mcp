"""Ordinary WebSocket JSON decoding is bounded before it can occupy a worker."""

import asyncio
import json
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from models.response_limits import response_size
from tests.test_plugin_json_decoder import COMMAND_ID, owned_hub  # noqa: F401
from tests.timing import await_signal, watchdog_timeout
from transport import plugin_hub as hub_module
from transport.models import PongMessage
from transport.plugin_registry import PluginRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("kind", ["valid", "bytes", "depth", "nodes"])
async def test_standalone_large_decode_preserves_preparse_limits(
    owned_hub, monkeypatch, binary, kind
):
    hub, socket = owned_hub
    cls = type(hub)
    monkeypatch.setattr(cls, "_lock", None)
    cls._connections.clear()
    cls._admitted.clear()
    expected = {"text": "x" * 70000}
    raw = json.dumps(expected)
    if kind == "bytes":
        monkeypatch.setattr(cls, "MAX_RAW_MESSAGE_BYTES", len(raw) - 1)
    elif kind == "depth":
        raw = '{"text":"' + "x" * 70000 + '","nested":' + "[" * 70 + "0" + "]" * 70 + "}"
    elif kind == "nodes":
        monkeypatch.setattr(cls, "MAX_RESULT_NODES", 4)
        raw = json.dumps({**expected, "nodes": [1, 2, 3, 4]})
    loop_thread = get_ident()
    scan_threads, decoder_threads = [], []
    scan, decode = hub_module.bounded_json_text, hub_module.decode_json

    def checked_scan(*args, **kwargs):
        scan_threads.append(get_ident())
        assert get_ident() != loop_thread
        return scan(*args, **kwargs)

    def checked_decode(*args, **kwargs):
        decoder_threads.append(get_ident())
        assert kind == "valid", "Rejected graph reached the JSON decoder"
        assert get_ident() != loop_thread
        return decode(*args, **kwargs)

    monkeypatch.setattr(hub_module, "bounded_json_text", checked_scan)
    monkeypatch.setattr(hub_module, "decode_json", checked_decode)
    result = await hub.decode(socket, {"bytes": raw.encode()} if binary else {"text": raw})
    if kind == "valid":
        assert result == expected
        assert len(decoder_threads) == 1
        socket.close.assert_not_awaited()
    else:
        assert result is None
        assert not decoder_threads
        socket.close.assert_awaited_once_with(
            code=1009, reason="Plugin message exceeds supported limits"
        )
    assert len(scan_threads) == (0 if kind == "bytes" else 1)
    assert cls._retained_results.total_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize(
    "ending", ["deliver", "cancel", "disconnect", "reconnect", "reconfigure", "failure"]
)
async def test_ordinary_ingress_keeps_its_charge_and_fences_decode(
    owned_hub, monkeypatch, binary, registered, ending
):
    hub, socket = owned_hub
    cls = type(hub)
    if not registered:
        cls._connections.pop("owned")
        cls._admitted[id(socket)] = (socket, None)
    neighbor = SimpleNamespace(state=SimpleNamespace(plugin_generation="neighbor"))
    cls._connections["neighbor"] = neighbor
    cls._last_pong["neighbor"] = 0
    loop, loop_thread = asyncio.get_running_loop(), get_ident()
    entered, release = asyncio.Event(), Event()
    native = hub_module.bounded_json_text
    expected = {"type": "command_result", "id": COMMAND_ID, "result": {"text": "x" * 96000}}
    wire = json.dumps(expected)
    message = {"bytes": wire.encode()} if binary else {"text": wire}

    def held(*args, **kwargs):
        assert get_ident() != loop_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout())
        return None if ending == "failure" else native(*args, **kwargs)

    monkeypatch.setattr(hub_module, "bounded_json_text", held)
    task = asyncio.create_task(hub.decode(socket, message))
    try:
        await await_signal(entered, "ordinary ingress worker", producers=(task,))
        charged = cls._retained_results.total_bytes
        assert charged > cls.MAX_RESULT_RETAINED_BYTES
        await hub._handle_pong(neighbor, PongMessage(session_id="neighbor"))
        assert cls._last_pong["neighbor"] > 0
        if ending == "cancel":
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        elif ending == "disconnect":
            cls._connections.pop("owned", None)
            cls._admitted.pop(id(socket), None)
        elif ending == "reconnect":
            if registered:
                socket.state.plugin_generation = "replacement"
            else:
                cls._admitted[id(socket)] = (socket, None)
        elif ending == "reconfigure":
            cls.configure(PluginRegistry())
        await asyncio.sleep(0)
        assert not task.done()
        assert cls._retained_results.total_bytes == charged
        release.set()
        results = await asyncio.gather(task, return_exceptions=True)
        if ending == "deliver":
            assert results == [expected]
        elif ending == "cancel":
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert results == [None]
        if ending == "failure":
            socket.close.assert_awaited_once_with(
                code=1009, reason="Plugin message exceeds supported limits"
            )
        assert cls._retained_results.total_bytes == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_ingress_pressure_rejects_before_decoder_submission(owned_hub, monkeypatch):
    hub, socket = owned_hub
    monkeypatch.setattr(type(hub), "MAX_RETAINED_RESULT_BYTES", 100)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Over-capacity ingress reached a decoder")

    monkeypatch.setattr(hub_module, "bounded_json_text", forbidden)
    assert await hub.decode(socket, {"text": json.dumps({"text": "x" * 96000})}) is None
    socket.close.assert_awaited_once_with(code=1013, reason="Plugin message capacity reached")
    assert type(hub)._retained_results.total_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True])
async def test_pending_replacement_during_ingress_cannot_receive_old_frame(
    owned_hub, monkeypatch, malformed
):
    hub, socket = owned_hub
    socket.state.plugin_registered = True
    cls = type(hub)
    loop = asyncio.get_running_loop()
    original, replacement = loop.create_future(), loop.create_future()
    cls._pending[COMMAND_ID] = {"future": original, "session_id": "owned", "user_id": None}
    scope = hub_module._IngressScope()
    token = hub_module._ingress_scope.set(scope)
    entered, release = asyncio.Event(), Event()
    native = hub_module.bounded_json_text

    def held(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout())
        return native(*args, **kwargs)

    monkeypatch.setattr(hub_module, "bounded_json_text", held)
    wire = json.dumps(
        {
            "type": "command_result",
            "id": COMMAND_ID,
            "result": None if malformed else {},
            "padding": "x" * 96000,
        }
    )
    task = asyncio.create_task(hub.decode(socket, {"text": wire}))
    try:
        await await_signal(entered, "pending result decode", producers=(task,))
        cls._pending[COMMAND_ID] = {
            "future": replacement,
            "session_id": "owned",
            "user_id": None,
            "result_generation": "owned",
            "result_socket": socket,
        }
        release.set()
        decoded = await task
        await hub.on_receive(socket, decoded)
        assert not original.done() and not replacement.done()
        assert cls._retained_results.total_bytes > 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if scope.work is not None:
            scope.work.release()
        hub_module._ingress_scope.reset(token)
        original.cancel()
        replacement.cancel()
    assert cls._retained_results.total_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("binary", [False, True])
async def test_dispatch_transfers_graph_charge_but_retains_raw_frame_until_handler_exit(
    owned_hub, binary
):
    hub, _ = owned_hub
    cls = type(hub)
    cls._connections.clear()
    queue = asyncio.Queue()
    handled, finish_handler, idle = asyncio.Event(), asyncio.Event(), asyncio.Event()
    future = asyncio.get_running_loop().create_future()
    cls._pending[COMMAND_ID] = {"future": future, "session_id": "owned", "user_id": None}
    expected = {"text": "x" * 96000}
    wire = json.dumps({"type": "command_result", "id": COMMAND_ID, "result": expected})
    delivered = []
    receives = 0

    async def receive():
        nonlocal receives
        receives += 1
        if receives == 3:
            idle.set()
        return await queue.get()

    async def send(message):
        delivered.append(message)

    async def connect(socket):
        await socket.accept()
        socket.state.plugin_registered = True
        socket.state.plugin_session_id = "owned"
        socket.state.plugin_generation = "owned"
        cls._connections["owned"] = socket

    original_receive = hub.on_receive

    async def on_receive(socket, data):
        before = cls._retained_results.total_bytes
        await original_receive(socket, data)
        assert future.result() == expected
        assert response_size(expected) < cls._retained_results.total_bytes < before
        handled.set()
        await finish_handler.wait()

    hub.scope = {"type": "websocket", "path": "/hub", "headers": [], "query_string": b""}
    hub.receive, hub.send, hub.on_connect, hub.on_receive = receive, send, connect, on_receive
    await queue.put({"type": "websocket.connect"})
    await queue.put(
        {"type": "websocket.receive", **({"bytes": wire.encode()} if binary else {"text": wire})}
    )
    task = asyncio.create_task(hub.dispatch())
    try:
        await await_signal(handled, "decoded frame handled", producers=(task,))
        charge = cls._retained_results.total_bytes
        assert charge > response_size(expected)
        finish_handler.set()
        await await_signal(idle, "next receive after frame cleanup", producers=(task,))
        assert cls._retained_results.total_bytes == response_size(expected)
        cls._retained_results.pop(COMMAND_ID)
        await queue.put({"type": "websocket.disconnect", "code": 1000})
        await task
        assert cls._retained_results.total_bytes == 0
    finally:
        finish_handler.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        future.cancel()
