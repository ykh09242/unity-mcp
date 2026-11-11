"""Payload CPU work yields without releasing live buffers or dispatch fences."""

import asyncio
import gzip
import json
from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocket, WebSocketState

from tests.test_http_command_outcome import command_hub  # noqa: F401
from tests.test_plugin_json_decoder import COMMAND_ID, owned_hub  # noqa: F401
from tests.test_large_result_assembler import frames
from tests.timing import await_signal, watchdog_timeout
from tests.transport_socket import JsonSocket
from transport import payload_processing, plugin_hub as hub_module
from transport.models import ExecuteCommandMessage
from transport.plugin_hub import PluginHub
from transport.large_result_assembler import COMPRESSION_CAPABILITY
from transport.result_gzip import GzipResultDecoder


def hold_worker(monkeypatch, owner, name):
    original = getattr(owner, name)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release, exited = Event(), Event()
    threads = []

    def held(*args, **kwargs):
        threads.append(get_ident())
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(watchdog_timeout()), "test did not release payload worker"
            return original(*args, **kwargs)
        finally:
            exited.set()

    monkeypatch.setattr(owner, name, held)
    return entered, release, exited, threads


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["model", "json"])
async def test_large_numeric_payload_serializes_off_owner_thread(command_hub, monkeypatch, stage):
    params = {str(index): (1 << 13999) - 1 for index in range(100)}
    charge = PluginHub._command_payload_size("manage_scene", params)
    assert charge is not None and charge > 64 * 1024
    owner, name = (
        (ExecuteCommandMessage, "model_dump")
        if stage == "model"
        else (hub_module, "encode_command")
    )
    original = getattr(owner, name)
    threads = []

    def record_thread(*args, **kwargs):
        threads.append(get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, record_thread)
    result = await PluginHub.send_command("session", "manage_scene", params)
    assert result["success"] is True
    assert command_hub.sent[0]["params"] == params
    assert threads and get_ident() not in threads, (
        "large numeric serialization ran on the owner loop"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["model", "json"])
@pytest.mark.parametrize("ending", ["cancel", "generation", "deadline", "shutdown"])
async def test_outbound_cpu_yields_and_fences_real_socket(command_hub, monkeypatch, stage, ending):
    sent = []

    async def send(message):
        sent.append(message)

    socket = WebSocket({"type": "websocket"}, receive=None, send=send)
    socket.client_state = socket.application_state = WebSocketState.CONNECTED
    socket.state.plugin_registered = True
    socket.state.plugin_session_id = "session"
    socket.state.plugin_generation = "generation"
    PluginHub._connections["session"] = socket
    if stage == "model":
        held = hold_worker(monkeypatch, ExecuteCommandMessage, "model_dump")
    else:
        held = hold_worker(monkeypatch, json, "dumps")
    entered, release, exited, threads = held
    from transport.command_budget import _CommandBudget

    budget = _CommandBudget(asyncio.get_running_loop().time() + 10)
    call = asyncio.create_task(
        PluginHub.send_command("session", "manage_scene", {"text": "x" * 96000}, _budget=budget)
    )
    try:
        await await_signal(entered, "outbound CPU entered", producers=(call,))
        assert not exited.is_set(), "the event loop must run while serialization is held"
        assert get_ident() not in threads
        assert PluginHub._pending_usage("session", None)[3] > 0
        if ending == "cancel":
            call.cancel()
            await asyncio.sleep(0)
            assert not call.done()
            assert PluginHub._pending_usage("session", None)[3] > 0
        elif ending == "generation":
            socket.state.plugin_generation = "replacement"
        elif ending == "shutdown":
            await PluginHub.shutdown()
            assert PluginHub._pending_usage("session", None)[3] > 0
        else:
            budget.expires_at = asyncio.get_running_loop().time() - 1
        release.set()
        await asyncio.wait_for(asyncio.gather(call, return_exceptions=True), watchdog_timeout())
        assert not any(message["type"] == "websocket.send" for message in sent)
        assert PluginHub._pending_usage("session", None)[3] == 0
    finally:
        release.set()
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "replacement", "deadline", "reconfigure"])
async def test_gzip_cpu_yields_and_retains_physical_worker_charge(owned_hub, monkeypatch, ending):
    hub, socket = owned_hub
    cls = type(hub)
    socket.state.plugin_features.add(COMPRESSION_CAPABILITY)
    future = asyncio.get_running_loop().create_future()
    entry = {"future": future, "session_id": "owned", "user_id": None}
    cls._pending[COMMAND_ID] = entry
    raw = json.dumps(
        {"type": "command_result", "id": COMMAND_ID, "result": {"text": "x" * 1100000}}
    ).encode()
    wire = gzip.compress(raw, mtime=0)
    chunks = frames(wire, COMMAND_ID)
    await hub._handle_large_result(
        socket,
        {
            "type": "result_start",
            "id": COMMAND_ID,
            "total_bytes": len(wire),
            "chunk_count": len(chunks),
            "encoding": "gzip",
            "decoded_bytes": len(raw),
        },
    )
    assembler = cls._large_results
    held = hold_worker(monkeypatch, GzipResultDecoder, "feed")
    entered, release, exited, threads = held
    task = asyncio.create_task(hub._handle_large_result(socket, chunks[0]))
    replacement = None
    try:
        await await_signal(entered, "gzip CPU entered", producers=(task,))
        assert not exited.is_set(), "the event loop must run while inflation is held"
        assert get_ident() not in threads
        async with cls._lock:
            assert cls._raw_results.total_bytes + cls._retained_results.total_bytes > 0
        if ending == "cancel":
            task.cancel()
        elif ending == "replacement":
            replacement = asyncio.get_running_loop().create_future()
            cls._pending[COMMAND_ID] = {"future": replacement, "session_id": "owned"}
        elif ending == "deadline":
            assembler._clock = lambda: float("inf")
        else:
            from transport.plugin_registry import PluginRegistry

            cls.configure(PluginRegistry())
        if ending == "cancel":
            assembler.discard_owner("owned")
        await asyncio.sleep(0)
        assert assembler.retained_bytes == len(raw)
        assert cls._raw_results.total_bytes + cls._retained_results.total_bytes > 0
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), watchdog_timeout())
        assert assembler.retained_bytes == 0
        assert cls._raw_results.total_bytes + cls._retained_results.total_bytes == 0
        if ending == "deadline":
            assert future.result()["data"]["reason"] == "result_transfer_timeout"
        else:
            assert not future.done()
        assert replacement is None or not replacement.done()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        future.cancel()
        if replacement is not None:
            replacement.cancel()


@pytest.mark.asyncio
async def test_outbound_workers_bound_queued_graphs_and_cancel_before_start(
    command_hub, monkeypatch
):
    original = payload_processing.encode_command
    entered = asyncio.Queue()
    release = Event()
    loop = asyncio.get_running_loop()
    started = []

    def held(payload):
        started.append(payload["id"])
        loop.call_soon_threadsafe(entered.put_nowait, payload["id"])
        assert release.wait(watchdog_timeout())
        return original(payload)

    monkeypatch.setattr(hub_module, "encode_command", held)
    calls = []
    try:
        for index in range(3):
            session_id = f"payload-{index}"
            await command_hub.registry.register(session_id, session_id, session_id, "6000.3")

            async def send_json(_message):
                return None

            socket = JsonSocket(
                state=SimpleNamespace(
                    plugin_generation=session_id,
                    plugin_registered=True,
                    plugin_session_id=session_id,
                ),
                client_state=WebSocketState.CONNECTED,
                application_state=WebSocketState.CONNECTED,
                send_json=send_json,
                close=command_hub.websocket.close,
            )
            PluginHub._connections[session_id] = socket
            calls.append(
                asyncio.create_task(
                    PluginHub.send_command(session_id, "manage_scene", {"text": "x" * 96000})
                )
            )
            if index < 2:
                await asyncio.wait_for(entered.get(), watchdog_timeout())
        async with asyncio.timeout(watchdog_timeout()):
            while len(PluginHub._outbound_payloads) != 3:
                await asyncio.sleep(0)
        assert len(PluginHub._outbound_payloads) == 3
        assert len(started) == 2
        calls[2].cancel()
        await asyncio.gather(calls[2], return_exceptions=True)
        assert len(PluginHub._outbound_payloads) == 2
        for call in calls[:2]:
            call.cancel()
        await asyncio.sleep(0)
        assert not any(call.done() for call in calls[:2])
        assert len(PluginHub._outbound_payloads) == 2
        release.set()
        await asyncio.gather(*calls, return_exceptions=True)
        assert len(started) == 2
        assert not PluginHub._outbound_payloads
    finally:
        release.set()
        for call in calls:
            call.cancel()
        await asyncio.gather(*calls, return_exceptions=True)


@pytest.mark.asyncio
async def test_expired_large_decode_cannot_publish_success(owned_hub, monkeypatch):
    hub, socket = owned_hub
    cls = type(hub)
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    entry = {"future": future, "session_id": "owned", "result_deadline": loop.time() + 60}
    cls._pending[COMMAND_ID] = entry
    raw = json.dumps(
        {"type": "command_result", "id": COMMAND_ID, "result": {"text": "x" * 300000}}
    ).encode()
    chunks = frames(raw, COMMAND_ID)
    await hub._handle_large_result(
        socket,
        {
            "type": "result_start",
            "id": COMMAND_ID,
            "total_bytes": len(raw),
            "chunk_count": len(chunks),
        },
    )
    for chunk in chunks[:-1]:
        await hub._handle_large_result(socket, chunk)
    entered, release, _, _ = hold_worker(monkeypatch, payload_processing, "bounded_json_text")
    task = asyncio.create_task(hub._handle_large_result(socket, chunks[-1]))
    try:
        await await_signal(entered, "large result decode entered", producers=(task,))
        entry["result_deadline"] = loop.time() - 1
        release.set()
        await asyncio.wait_for(task, watchdog_timeout())
        assert not future.done(), "an expired decoded result cannot complete the operation"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        cls._retained_results.pop(COMMAND_ID, None)
        future.cancel()
