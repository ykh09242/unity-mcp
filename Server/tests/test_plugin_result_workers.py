"""Transport response workers retain capacity while the owner loop stays responsive."""

import asyncio
import json
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from tests.test_plugin_json_decoder import COMMAND_ID, owned_hub  # noqa: F401
from tests.test_large_result_assembler import frames
from tests.test_operation_scheduler import settled
from tests.timing import await_signal, watchdog_timeout
from transport import plugin_hub as hub_module
from transport.models import CommandResultMessage, PongMessage
from transport.large_result_assembler import LargeResultProtocolError
from transport.plugin_registry import PluginRegistry
from services.execution_receipt_identity import ReceiptUnavailable


class HeldReceipt:
    def __init__(self, *, fail=False, fail_pin=False):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []
        self.pins = 0
        self.unknown = False
        self.fail = fail
        self.fail_pin = fail_pin

    def acquire_worker(self):
        if self.fail_pin:
            raise ReceiptUnavailable("private pin failure detail")
        self.pins += 1

        def close():
            self.pins -= 1

        return SimpleNamespace(close=close)

    def mark_unknown(self):
        self.unknown = True

    async def observe_async(self, **values):
        self.calls.append(values)
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise ReceiptUnavailable("private storage failure detail")


async def dispatch_peer(hub):
    """Use production dispatch/decode/on_receive; only replace the ASGI peer."""
    cls = type(hub)
    cls._connections.clear()
    queue = asyncio.Queue()
    connected, pong = asyncio.Event(), asyncio.Event()
    peer = SimpleNamespace(queue=queue, pong=pong, socket=None, messages=[])

    async def receive():
        return await queue.get()

    async def send(message):
        peer.messages.append(message)
        if message["type"] == "websocket.send":
            payload = json.loads(message["text"])
            if payload["type"] == "pong":
                pong.set()
            elif payload["type"] == "ping":
                await queue.put(
                    {
                        "type": "websocket.receive",
                        "text": json.dumps(
                            {
                                "type": "pong",
                                "session_id": "owned",
                                "challenge": payload["challenge"],
                            }
                        ),
                    }
                )

    async def connect(socket):
        await socket.accept()
        socket.state.plugin_registered = True
        socket.state.plugin_session_id = "owned"
        socket.state.plugin_generation = "owned"
        socket.state.plugin_features = {
            hub_module.HEARTBEAT_CAPABILITY,
            hub_module.LARGE_RESULT_CAPABILITY,
        }
        socket.state.plugin_heartbeat_challenge = None
        cls._connections["owned"] = socket
        cls._last_pong["owned"] = hub_module.time.monotonic()
        peer.socket = socket
        connected.set()

    hub.scope = {"type": "websocket", "path": "/hub", "headers": [], "query_string": b""}
    hub.receive, hub.send, hub.on_connect = receive, send, connect
    await queue.put({"type": "websocket.connect"})
    peer.task = asyncio.create_task(hub.dispatch())
    await await_signal(connected, "production dispatch connected", producers=(peer.task,))
    return peer


async def peer_frame(peer, payload):
    await peer.queue.put({"type": "websocket.receive", "text": json.dumps(payload)})


async def peer_result(peer, result, *, kind="small", command_id=COMMAND_ID):
    message = {"type": "command_result", "id": command_id, "result": result}
    if kind != "large":
        await peer_frame(peer, message)
        return
    raw = json.dumps(message).encode()
    chunks = frames(raw, command_id)
    await peer_frame(
        peer,
        {
            "type": "result_start",
            "id": command_id,
            "total_bytes": len(raw),
            "chunk_count": len(chunks),
        },
    )
    for frame in chunks:
        await peer.queue.put({"type": "websocket.receive", "bytes": frame})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["small", "ingress", "large"])
async def test_dispatch_handles_same_socket_control_while_receipt_is_held(owned_hub, kind):
    hub, _ = owned_hub
    cls = type(hub)
    receipt = HeldReceipt()
    future = asyncio.get_running_loop().create_future()
    cls._pending[COMMAND_ID] = {
        "future": future,
        "session_id": "owned",
        "user_id": None,
        "receipt_owner": receipt,
    }
    peer = await dispatch_peer(hub)
    result = (
        {"ok": True} if kind == "small" else {"text": "x" * (300000 if kind == "large" else 96000)}
    )
    try:
        await peer_result(peer, result, kind=kind)
        await await_signal(receipt.entered, "receipt persistence entered", producers=(peer.task,))
        if kind == "large":
            await peer_result(peer, result, kind=kind)
        await peer_frame(peer, {"type": "ping", "challenge": "a" * 32})
        await asyncio.wait_for(peer.pong.wait(), 0.5)
        assert not future.done()
        assert cls._connections["owned"] is peer.socket
        assert cls._retained_results.total_bytes > 0
        assert len(receipt.calls) == 1
        assert cls._large_results.retained_bytes == 0
        receipt.release.set()
        assert await asyncio.wait_for(asyncio.shield(future), watchdog_timeout()) == result
    finally:
        receipt.release.set()
        await settled()
        peer.task.cancel()
        await asyncio.gather(peer.task, return_exceptions=True)
        future.cancel()
        cls._retained_results.pop(COMMAND_ID, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending", ["cancel", "disconnect", "reconnect", "replacement", "reconfigure", "shutdown"]
)
@pytest.mark.parametrize("fail", [False, True])
async def test_dispatch_result_keeps_actual_owners_and_fences_late_delivery(
    owned_hub, ending, fail
):
    hub, _ = owned_hub
    cls = type(hub)
    receipt = HeldReceipt(fail=fail)
    future = asyncio.get_running_loop().create_future()
    entry = {"future": future, "session_id": "owned", "user_id": None, "receipt_owner": receipt}
    cls._pending[COMMAND_ID] = entry
    peer = await dispatch_peer(hub)
    shutdown = None
    replacement = None
    try:
        await peer_result(peer, {"text": "x" * 96000})
        await await_signal(receipt.entered, "held accepted result", producers=(peer.task,))
        charge = cls._retained_results.total_bytes
        assert charge > 0 and receipt.pins == 1
        worker = next(task for task, work in cls._result_tasks.items() if work.entry is entry)
        # A duplicate must never observe twice or replace the first accepted graph.
        await peer_result(peer, {"ok": "duplicate"})
        await peer_frame(peer, {"type": "ping", "challenge": "b" * 32})
        await await_signal(peer.pong, "duplicate passed by dispatch", producers=(peer.task,))
        assert len(receipt.calls) == 1
        if ending == "cancel":
            cls._pending.pop(COMMAND_ID)
            future.cancel()
            peer.task.cancel()
        elif ending == "disconnect":
            await peer.queue.put({"type": "websocket.disconnect", "code": 1000})
        elif ending == "reconnect":
            peer.socket.state.plugin_generation = "replacement"
        elif ending == "replacement":
            replacement = asyncio.get_running_loop().create_future()
            cls._pending[COMMAND_ID] = {**entry, "future": replacement}
        elif ending == "reconfigure":
            cls.configure(PluginRegistry())
        else:
            shutdown = asyncio.create_task(cls.shutdown())
        await settled()
        assert not worker.done()
        assert receipt.pins == 1
        assert cls._retained_results.total_bytes == charge
        if shutdown is not None:
            assert not shutdown.done(), "Shutdown abandoned accepted persistence"
        receipt.release.set()
        await asyncio.wait_for(asyncio.shield(worker), watchdog_timeout())
        if shutdown is not None:
            await asyncio.wait_for(shutdown, watchdog_timeout())
        assert cls._retained_results.total_bytes == 0
        assert receipt.pins == 0
        assert not receipt.unknown, "A stale result rewrote the receipt owner"
        if replacement is not None:
            assert not replacement.done()
        if future.done() and not future.cancelled():
            assert isinstance(future.exception(), hub_module.PluginDisconnectedError)
        else:
            assert not future.done() or future.cancelled()
    finally:
        receipt.release.set()
        await settled()
        peer.task.cancel()
        await asyncio.gather(peer.task, return_exceptions=True)
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        if future.done() and not future.cancelled():
            future.exception()
        future.cancel()
        if replacement is not None:
            replacement.cancel()
        if cls._large_results is None:
            cls.configure(PluginRegistry())


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved", [False, True])
@pytest.mark.parametrize(
    "limit",
    [
        "MAX_PENDING_COMMANDS",
        "MAX_PENDING_PER_USER",
        "MAX_PENDING_PER_SESSION",
        "MAX_RETAINED_RESULT_BYTES",
        "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
    ],
)
async def test_dispatch_result_capacity_counts_detached_work_until_actual_exit(
    owned_hub, monkeypatch, limit, reserved
):
    hub, _ = owned_hub
    cls = type(hub)
    if reserved:
        limit = limit.replace("MAX_PENDING", "MAX_CONTROL")
    monkeypatch.setattr(
        cls, limit, hub_module.response_size({"ok": True}) if "BYTES" in limit else 1
    )
    receipt = HeldReceipt()
    loop = asyncio.get_running_loop()
    original, refused = loop.create_future(), loop.create_future()
    entry = {
        "future": original,
        "session_id": "owned",
        "user_id": None,
        "receipt_owner": receipt,
        "control_reserve": reserved,
    }
    cls._pending[COMMAND_ID] = entry
    peer = await dispatch_peer(hub)
    try:
        await peer_result(peer, {"ok": True})
        await await_signal(receipt.entered, "capacity owner entered", producers=(peer.task,))
        worker = next(task for task, work in cls._result_tasks.items() if work.entry is entry)
        # A caller stopping its wait does not return task or graph capacity.
        cls._pending.pop(COMMAND_ID)
        original.cancel()
        cls._pending["second"] = {
            "future": refused,
            "session_id": "owned",
            "user_id": None,
            "control_reserve": reserved,
        }
        await peer_result(peer, {"ok": True}, command_id="second")
        result = await asyncio.wait_for(asyncio.shield(refused), watchdog_timeout())
        assert result["data"]["reason"] == "result_capacity"
        assert not worker.done() and receipt.pins == 1
        assert len([w for w in cls._result_tasks.values() if w.lock is cls._lock]) == 1
        receipt.release.set()
        await worker
        assert cls._retained_results.total_bytes == 0
    finally:
        receipt.release.set()
        await settled()
        peer.task.cancel()
        await asyncio.gather(peer.task, return_exceptions=True)
        original.cancel()
        refused.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit", ["MAX_PENDING_COMMANDS", "MAX_PENDING_PER_USER", "MAX_PENDING_PER_SESSION"]
)
async def test_dispatch_control_result_preserves_reserved_headroom(owned_hub, monkeypatch, limit):
    hub, _ = owned_hub
    cls = type(hub)
    monkeypatch.setattr(cls, limit, 2)
    receipt = HeldReceipt()
    loop = asyncio.get_running_loop()
    peer = await dispatch_peer(hub)
    futures = []
    control_owner = None
    delivery = hub_module.ResponseOwner()
    try:
        for index in range(2):
            command_id = str(index)
            assert cls._pending_reservation("owned", None) is False
            future = loop.create_future()
            futures.append(future)
            cls._pending[command_id] = {
                "future": future,
                "session_id": "owned",
                "user_id": None,
                "receipt_owner": receipt,
            }
            await peer_result(peer, {"ok": True}, command_id=command_id)
        await peer_frame(peer, {"type": "ping", "challenge": "e" * 32})
        await await_signal(peer.pong, "ordinary results accepted", producers=(peer.task,))
        await settled()
        assert len(receipt.calls) == receipt.pins == 2
        ordinary_charge = cls._retained_results.total_bytes
        assert cls._pending_reservation("owned", None) is None
        assert cls._pending_reservation("owned", None, control=True) is True
        record = {
            "session_id": "owned",
            "user_id": None,
            "payload_bytes": 1,
            "control_reserve": True,
        }
        control_owner = hub_module._ControlReservation(cls._control_reservations, "control", record)
        control = loop.create_future()
        futures.append(control)
        cls._pending["control"] = {
            **record,
            "future": control,
            "control_owner": control_owner,
            "response_owner": delivery,
        }
        expected = {"ok": "cancelled"}
        await peer_result(peer, expected, command_id="control")
        assert await asyncio.wait_for(asyncio.shield(control), watchdog_timeout()) == expected
        assert not any(future.done() for future in futures[:2])
        cls._pending.pop("control")
        control_owner.close()
        assert "control" in cls._control_reservations
        assert cls._retained_results.total_bytes == ordinary_charge + hub_module.response_size(
            expected
        )
        delivery.release()
        await settled()
        assert "control" not in cls._control_reservations
        assert cls._retained_results.total_bytes == ordinary_charge
    finally:
        receipt.release.set()
        await settled()
        peer.task.cancel()
        await asyncio.gather(peer.task, return_exceptions=True)
        delivery.release()
        if control_owner is not None:
            control_owner.close()
        for index, future in enumerate(futures):
            future.cancel()
            cls._retained_results.pop(str(index), None)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_dispatch_reserved_control_worker_uses_control_result_ceiling(
    owned_hub, monkeypatch, cancel
):
    hub, _ = owned_hub
    cls = type(hub)
    monkeypatch.setattr(cls, "MAX_PENDING_COMMANDS", 1)
    peer = await dispatch_peer(hub)
    ordinary = asyncio.get_running_loop().create_future()
    cls._pending["ordinary"] = {"future": ordinary, "session_id": "owned", "user_id": None}
    expected = {"values": list(range(300))}
    assert hub_module.needs_response_worker(expected)
    assert len(json.dumps(expected)) < 64 * 1024
    assert hub_module.response_size(expected) < cls.MAX_CONTROL_RESULT_BYTES
    assert cls._pending_reservation("owned", None, control=True) is True
    record = {
        "session_id": "owned",
        "user_id": None,
        "payload_bytes": 1,
        "control_reserve": True,
    }
    control_owner = hub_module._ControlReservation(cls._control_reservations, COMMAND_ID, record)
    delivery = hub_module.ResponseOwner()
    future = asyncio.get_running_loop().create_future()
    cls._pending[COMMAND_ID] = {
        **record,
        "future": future,
        "control_owner": control_owner,
        "response_owner": delivery,
    }
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    native = hub_module.response_size

    def held(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout())
        return native(*args, **kwargs)

    monkeypatch.setattr(hub_module, "response_size", held)
    worker = None
    try:
        await peer_result(peer, expected)
        await await_signal(
            entered, "reserved control worker entered", producers=(peer.task, future)
        )
        worker = next(
            task for task, work in cls._result_tasks.items() if work.command_id == COMMAND_ID
        )
        assert cls._retained_results.total_bytes == cls.MAX_CONTROL_RESULT_BYTES
        if cancel:
            cls._pending.pop(COMMAND_ID)
            future.cancel()
            control_owner.close()
            await settled()
            assert COMMAND_ID in cls._control_reservations
            assert cls._retained_results.total_bytes == cls.MAX_CONTROL_RESULT_BYTES
            assert cls._pending_reservation("owned", None, control=True) is None
        release.set()
        await worker
        if cancel:
            assert cls._retained_results.total_bytes == 0
            assert COMMAND_ID not in cls._control_reservations
        else:
            assert future.result() == expected
            assert cls._retained_results.total_bytes == native(expected)
    finally:
        release.set()
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
        peer.task.cancel()
        await asyncio.gather(peer.task, return_exceptions=True)
        future.cancel()
        if ordinary.done() and not ordinary.cancelled():
            ordinary.exception()
        ordinary.cancel()
        delivery.release()
        control_owner.close()


@pytest.mark.asyncio
async def test_receipt_failure_settles_current_result_with_unknown_outcome(owned_hub):
    hub, socket = owned_hub
    socket.state.plugin_registered = True
    receipt = HeldReceipt(fail=True)
    receipt.release.set()
    future = asyncio.get_running_loop().create_future()
    type(hub)._pending[COMMAND_ID] = {
        "future": future,
        "session_id": "owned",
        "user_id": None,
        "receipt_owner": receipt,
        "command_type": "manage_gameobject",
    }
    try:
        await hub.on_receive(
            socket, {"type": "command_result", "id": COMMAND_ID, "result": {"ok": True}}
        )
        assert future.done(), "Persistence failure left the command waiting for its timeout"
        result = future.result()
        assert result["success"] is False
        assert result["data"]["reason"] == "outcome_unknown"
        assert result["data"]["cause"] == "result_persistence_failed"
        assert "not_dispatched" not in json.dumps(result)
        assert "private storage" not in json.dumps(result)
        assert receipt.unknown
    finally:
        future.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_pin", [False, True])
@pytest.mark.parametrize("kind", ["small", "large"])
async def test_dispatch_persistence_failure_is_prompt_and_sanitized(owned_hub, fail_pin, kind):
    hub, _ = owned_hub
    cls = type(hub)
    receipt = HeldReceipt(fail=True, fail_pin=fail_pin)
    receipt.release.set()
    future = asyncio.get_running_loop().create_future()
    cls._pending[COMMAND_ID] = {
        "future": future,
        "session_id": "owned",
        "user_id": None,
        "receipt_owner": receipt,
        "command_type": "manage_gameobject",
    }
    peer = await dispatch_peer(hub)
    try:
        await peer_result(peer, {"text": "x" * 300000} if kind == "large" else {}, kind=kind)
        result = await asyncio.wait_for(asyncio.shield(future), watchdog_timeout())
        assert result["data"]["reason"] == "outcome_unknown"
        assert result["data"]["cause"] == "result_persistence_failed"
        assert result["hint"] == "inspect_state_before_retry"
        assert "private" not in json.dumps(result)
        assert "not_dispatched" not in json.dumps(result)
        await settled()
        assert receipt.pins == 0
        assert cls._retained_results.total_bytes == cls._raw_results.total_bytes == 0
    finally:
        peer.task.cancel()
        await asyncio.gather(peer.task, return_exceptions=True)
        future.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ingress", "large"])
async def test_dispatch_keeps_control_responsive_during_result_preparation(
    owned_hub, monkeypatch, kind
):
    hub, _ = owned_hub
    cls = type(hub)
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    future = loop.create_future()
    cls._pending[COMMAND_ID] = {"future": future, "session_id": "owned", "user_id": None}
    native = hub_module.response_size if kind == "ingress" else cls._decode_large_result_outcome

    def held(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout())
        return native(*args, **kwargs)

    if kind == "ingress":
        monkeypatch.setattr(hub_module, "response_size", held)
    else:
        monkeypatch.setattr(cls, "_decode_large_result_outcome", held)
    peer = await dispatch_peer(hub)
    try:
        expected = {"text": "x" * 300000}
        await peer_result(peer, expected, kind=kind)
        await await_signal(entered, "result preparation worker", producers=(peer.task,))
        await peer_frame(peer, {"type": "ping", "challenge": "c" * 32})
        await await_signal(peer.pong, "control passed held preparation", producers=(peer.task,))
        assert not future.done()
        release.set()
        assert await asyncio.wait_for(asyncio.shield(future), watchdog_timeout()) == expected
    finally:
        release.set()
        await settled()
        peer.task.cancel()
        await asyncio.gather(peer.task, return_exceptions=True)
        future.cancel()
        cls._retained_results.pop(COMMAND_ID, None)


@pytest.mark.asyncio
async def test_held_results_accept_same_socket_heartbeat_pongs_and_small_concurrency(
    owned_hub, monkeypatch
):
    hub, _ = owned_hub
    cls = type(hub)
    receipt = HeldReceipt()
    peer = await dispatch_peer(hub)
    futures = []
    ping = None
    try:
        for index in range(4):
            command_id = str(index)
            future = asyncio.get_running_loop().create_future()
            futures.append(future)
            cls._pending[command_id] = {
                "future": future,
                "session_id": "owned",
                "user_id": None,
                "receipt_owner": receipt,
            }
            await peer_result(peer, {"ok": True}, command_id=command_id)
        await peer_frame(peer, {"type": "ping", "challenge": "d" * 32})
        await await_signal(peer.pong, "all small results accepted", producers=(peer.task,))
        await settled()
        assert len(receipt.calls) == 4
        assert receipt.pins == 4
        assert cls._retained_results.total_bytes == 4 * hub_module.response_size({"ok": True})
        touched = asyncio.Event()
        original_touch = cls._registry.touch

        async def touch(session_id):
            await original_touch(session_id)
            touched.set()

        monkeypatch.setattr(cls._registry, "touch", touch)
        monkeypatch.setattr(cls, "PING_INTERVAL", 0.01)
        ping = asyncio.create_task(cls._ping_loop("owned", peer.socket))
        await await_signal(touched, "immediate peer pong consumed", producers=(peer.task, ping))
        assert peer.socket.state.plugin_heartbeat_challenge is None
        assert cls._connections["owned"] is peer.socket
        assert not any(future.done() for future in futures)
        receipt.release.set()
        assert await asyncio.gather(*futures) == [{"ok": True}] * 4
    finally:
        receipt.release.set()
        await settled()
        if ping is not None:
            ping.cancel()
            await ping
        peer.task.cancel()
        await asyncio.gather(peer.task, return_exceptions=True)
        for index, future in enumerate(futures):
            future.cancel()
            cls._retained_results.pop(str(index), None)


@pytest.mark.asyncio
@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize(
    "ending",
    [
        "deliver",
        "cancel",
        "disconnect",
        "reconnect",
        "reconfigure",
        "replacement",
        "capacity_replacement",
        "failure",
    ],
)
async def test_result_worker_holds_budget_until_exit_and_fences_delivery(
    owned_hub, monkeypatch, large, ending
):
    hub, socket = owned_hub
    cls = type(hub)
    loop = asyncio.get_running_loop()
    owner_thread = get_ident()
    entered, release = asyncio.Event(), Event()
    future = loop.create_future()
    entry = {"future": future, "session_id": "owned", "user_id": None}
    cls._pending[COMMAND_ID] = entry
    neighbor = SimpleNamespace(state=SimpleNamespace(plugin_generation="neighbor"))
    cls._connections["neighbor"] = neighbor
    cls._last_pong["neighbor"] = 0
    expected = (
        {"text": "x" * (256 * 1024)} if large else {str(index): "x" * 60000 for index in range(100)}
    )
    native = hub_module.bounded_json_text if large else hub_module.response_size

    def held(*args, **kwargs):
        assert get_ident() != owner_thread, "Expensive result work ran on the owner loop"
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout()), "Test did not release the worker"
        if ending == "failure":
            raise ValueError("synthetic result failure")
        if large and ending == "capacity_replacement":
            raise LargeResultProtocolError("result_capacity", COMMAND_ID)
        return native(*args, **kwargs)

    monkeypatch.setattr(hub_module, "bounded_json_text" if large else "response_size", held)

    async def receive():
        if not large:
            await hub._handle_command_result(
                socket, CommandResultMessage(id=COMMAND_ID, result=expected)
            )
            return
        wire = json.dumps({"type": "command_result", "id": COMMAND_ID, "result": expected}).encode()
        chunks = frames(wire, COMMAND_ID)
        await hub._handle_large_result(
            socket,
            {
                "type": "result_start",
                "id": COMMAND_ID,
                "total_bytes": len(wire),
                "chunk_count": len(chunks),
            },
        )
        for frame in chunks:
            await hub._handle_large_result(socket, frame)

    task = asyncio.create_task(receive())
    try:
        await await_signal(entered, "result worker started", producers=(task,))
        charge = cls._retained_results.total_bytes + cls._raw_results.total_bytes
        assert charge > 0
        await hub._handle_pong(neighbor, PongMessage(session_id="neighbor"))
        assert cls._last_pong["neighbor"] > 0
        async with cls._lock:
            if ending == "cancel":
                cls._pending.pop(COMMAND_ID)
                future.cancel()
                cls._large_results.discard("owned", COMMAND_ID)
                task.cancel()
            elif ending == "reconnect":
                socket.state.plugin_generation = "replacement"
                cls._large_results.discard_owner("owned")
            elif ending == "disconnect":
                cls._connections.pop("owned")
                cls._large_results.discard_owner("owned")
            elif ending == "reconfigure":
                cls.configure(PluginRegistry())
            elif ending in {"replacement", "capacity_replacement"}:
                cls._pending[COMMAND_ID] = {**entry, "future": loop.create_future()}
        await asyncio.sleep(0)
        if ending == "cancel":
            task.cancel()
            await asyncio.sleep(0)
        assert cls._retained_results.total_bytes + cls._raw_results.total_bytes == charge
        assert not task.done()
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        if ending == "deliver":
            assert result == [None]
            assert future.result() == expected
            cls._retained_results.pop(COMMAND_ID)
        else:
            assert not future.done() or future.cancelled()
            if ending == "failure":
                if large:
                    socket.close.assert_awaited_once()
                else:
                    assert isinstance(result[0], ValueError)
            if ending in {"replacement", "capacity_replacement"}:
                assert not cls._pending[COMMAND_ID]["future"].done()
        assert cls._retained_results.total_bytes + cls._raw_results.total_bytes == 0
        assert cls._large_results.retained_bytes == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        future.cancel()


@pytest.mark.asyncio
async def test_queued_response_cancellation_releases_only_its_reserved_bytes(
    owned_hub, monkeypatch
):
    hub, socket = owned_hub
    cls = type(hub)
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    started = []
    native = hub_module.response_size

    def held(value, **kwargs):
        def observe():
            started.append(value["id"])
            if len(started) == 2:
                entered.set()

        loop.call_soon_threadsafe(observe)
        assert release.wait(watchdog_timeout())
        return native(value, **kwargs)

    monkeypatch.setattr(hub_module, "response_size", held)
    tasks = []
    futures = []
    try:
        for index in range(3):
            session = f"session-{index}"
            cls._connections[session] = socket
            future = loop.create_future()
            futures.append(future)
            cls._pending[str(index)] = {"future": future, "session_id": session, "user_id": None}
            tasks.append(
                asyncio.create_task(
                    hub._handle_command_result(
                        socket,
                        CommandResultMessage(
                            id=str(index), result={"id": index, "text": "x" * (256 * 1024)}
                        ),
                    )
                )
            )
        await await_signal(entered, "two response workers", producers=tasks)
        await settled()
        assert len(started) == 2
        assert cls._retained_results.total_bytes == 3 * cls.MAX_RESULT_RETAINED_BYTES
        tasks[2].cancel()
        await asyncio.gather(tasks[2], return_exceptions=True)
        assert cls._retained_results.total_bytes == 2 * cls.MAX_RESULT_RETAINED_BYTES
        assert len(started) == 2
        release.set()
        await asyncio.gather(*tasks[:2])
        for index in range(2):
            assert futures[index].result()["id"] == index
            cls._retained_results.pop(str(index))
        assert cls._retained_results.total_bytes == 0
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for future in futures:
            future.cancel()
