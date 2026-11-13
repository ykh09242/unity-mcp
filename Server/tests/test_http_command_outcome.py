"""HTTP command failures must preserve whether execution may have occurred."""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from starlette.websockets import WebSocketState

from core.config import config
from transport import plugin_hub as hub_module
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_transport import send_with_unity_instance
from tests.transport_socket import JsonSocket
from tests.timing import await_completion, await_signal


@pytest_asyncio.fixture
async def command_hub(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "0")
    monkeypatch.setenv("UNITY_MCP_SESSION_READY_WAIT_SECONDS", "0")
    registry = PluginRegistry()
    PluginHub.configure(registry)
    await registry.register("session", "Fixture", "hash", "6000.3")
    endpoint = PluginHub.__new__(PluginHub)
    sent = []

    async def send_json(message):
        sent.append(message)
        if message["type"] == "execute":
            await endpoint._handle_command_result(
                websocket, CommandResultMessage(id=message["id"], result={"success": True})
            )

    async def close(**_kwargs):
        return None

    websocket = JsonSocket(
        state=SimpleNamespace(
            plugin_registered=True,
            plugin_session_id="session",
            plugin_generation="generation",
            plugin_features=frozenset({PluginHub.CANCELLATION_CAPABILITY}),
            user_id=None,
        ),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
        send_json=send_json,
        close=close,
    )
    PluginHub._connections["session"] = websocket
    yield SimpleNamespace(registry=registry, endpoint=endpoint, websocket=websocket, sent=sent)
    await PluginHub.shutdown()


async def dispatch(command="manage_scene"):
    return await send_with_unity_instance(None, "Fixture@hash", command, {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("continuation", "first_written", "recovery"),
    [
        (False, False, None),
        (False, True, None),
        (True, False, None),
        (True, False, "cold"),
        (True, True, "cold"),
        (True, True, "disconnect"),
        (True, True, "capacity"),
    ],
)
async def test_disconnect_at_write_boundary_uses_actual_dispatch_evidence(
    command_hub, monkeypatch, continuation, first_written, recovery
):
    from copy import copy
    from transport.command_dispatch import CommandDispatch
    from transport.command_resume import CAPABILITY
    from transport.native_arbitration import CAPABILITY as ARBITRATION

    original = command_hub.websocket
    if continuation:
        original.state.plugin_features |= {ARBITRATION, CAPABILITY}
        original.state.plugin_resume_generation = "e" * 32
        original.state.plugin_arbitration_generation = "a" * 32
    before_write, after_write = asyncio.Event(), asyncio.Event()
    release = asyncio.Event()
    authorize_write = CommandDispatch.authorize_write

    async def authorize(writer):
        if not first_written or writer.session_id == "restored":
            before_write.set()
            await release.wait()
        return await authorize_write(writer)

    async def observe(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            after_write.set()
            await release.wait()

    async def reconnect(session_id, editor_generation):
        socket = copy(original)
        socket.state = copy(original.state)
        socket.state.plugin_session_id = socket.state.plugin_generation = session_id
        socket.state.plugin_registered = True
        socket.state.plugin_resume_generation = editor_generation
        await command_hub.registry.register(session_id, "Fixture", "hash", "6000.3")
        PluginHub._connections[session_id] = socket
        return socket

    monkeypatch.setattr(CommandDispatch, "authorize_write", authorize)
    original.send_json = observe
    command = asyncio.create_task(dispatch())
    try:
        await await_signal(
            after_write if first_written else before_write,
            "first write boundary",
            producers=(command,),
        )
        await command_hub.endpoint.on_disconnect(original, 1006)
        if recovery == "capacity":
            monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
            await reconnect("restored", "e" * 32)
        if recovery == "disconnect":
            restored = await reconnect("restored", "e" * 32)
            await await_signal(
                before_write, "continuation admitted before write", producers=(command,)
            )
            await command_hub.endpoint.on_disconnect(restored, 1006)
        if recovery in {"cold", "disconnect"}:
            # End recovery promptly through the real cold-Editor generation fence.
            await reconnect("cold", "f" * 32)
        result = await await_completion(command, "disconnected command outcome")
        executes = [message for message in command_hub.sent if message["type"] == "execute"]
        assert len(executes) == int(first_written)
        if first_written:
            assert result["hint"] == "inspect_state_before_retry"
            assert result["data"]["reason"] == "outcome_unknown"
            assert result["data"]["session_id"] == "session"
            assert result["data"]["command_id"] == executes[0]["id"]
        else:
            assert result["hint"] == "retry"
            assert result["data"]["reason"] == "command_not_dispatched"
            assert command_hub.sent == []
        assert PluginHub._pending == {}
    finally:
        release.set()
        command.cancel()
        await asyncio.gather(command, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnect", "send_error", "send_cancelled", "timeout"])
async def test_lost_reply_reports_unknown_execution_without_replay(
    command_hub, monkeypatch, failure
):
    applied = []
    scopes = []

    if failure == "timeout":

        def capture_deadline(when):
            scope = asyncio.timeout_at(when)
            scopes.append(scope)
            return scope

        async def expire_reply_wait(future, timeout):
            pending = PluginHub._pending.get(applied[0], {}) if applied else {}
            if future is pending.get("future"):
                # Expire the real total deadline only after the write completes.
                # A short setup budget can instead refuse before any dispatch.
                scopes[0].reschedule(asyncio.get_running_loop().time())
            return await asyncio.wait_for(future, timeout=timeout)

        local_asyncio = SimpleNamespace(**vars(asyncio))
        local_asyncio.timeout_at = capture_deadline
        local_asyncio.wait_for = expire_reply_wait
        monkeypatch.setattr(hub_module, "asyncio", local_asyncio)

    async def lose_reply(message):
        command_hub.sent.append(message)
        if message["type"] != "execute":
            return
        applied.append(message["id"])
        if failure == "disconnect":
            await command_hub.endpoint.on_disconnect(command_hub.websocket, 1006)
        elif failure == "send_error":
            raise OSError("connection lost during write")
        elif failure == "send_cancelled":
            raise asyncio.CancelledError

    command_hub.websocket.send_json = lose_reply
    result = await dispatch()
    assert result["success"] is False
    assert result["hint"] == "inspect_state_before_retry"
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["data"]["command"] == "manage_scene"
    assert result["data"]["command_id"] == applied[0]
    assert result["data"]["session_id"] == "session"
    assert "inspect" in result["error"].lower()
    assert len(applied) == 1
    assert PluginHub._pending == {}
    if failure == "timeout":
        assert scopes[0].expired()


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["missing_session", "capacity"])
async def test_refusal_before_dispatch_remains_safe_to_retry(command_hub, monkeypatch, refusal):
    if refusal == "missing_session":
        await command_hub.registry.unregister("session")
    else:
        monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
    result = await dispatch()
    assert result["success"] is False
    assert result["hint"] == "retry"
    assert result["data"]["reason"] == (
        "no_unity_session" if refusal == "missing_session" else "command_capacity"
    )
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_confirmed_unity_failure_is_preserved(command_hub):
    async def reject(message):
        command_hub.sent.append(message)
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(
                id=message["id"],
                result={
                    "success": False,
                    "error": "Object is locked",
                    "data": {"reason": "locked"},
                },
            ),
        )

    command_hub.websocket.send_json = reject
    assert await dispatch() == {
        "success": False,
        "error": "Object is locked",
        "data": {"reason": "locked"},
    }


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_and_cancels_only_original_command(command_hub):
    entered = asyncio.Event()

    async def blocked_send(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            entered.set()
            await asyncio.Event().wait()

    command_hub.websocket.send_json = blocked_send
    task = asyncio.create_task(dispatch())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [message["type"] for message in command_hub.sent] == ["execute", "cancel"]
    assert command_hub.sent[0]["id"] == command_hub.sent[1]["id"]
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_unclassified_http_failure_does_not_recommend_blind_retry(command_hub, monkeypatch):
    async def failed_boundary(*_args, **_kwargs):
        raise RuntimeError("unclassified transport failure")

    monkeypatch.setattr(PluginHub, "send_command_for_instance", failed_boundary)
    result = await dispatch()
    assert result["hint"] == "inspect_state_before_retry"
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["data"]["unity_instance"] == "Fixture@hash"


@pytest.mark.asyncio
async def test_disconnect_before_admission_remains_safe_to_retry(command_hub, monkeypatch):
    lookup = PluginHub._get_connection

    async def disconnect_after_lookup(session_id):
        websocket = await lookup(session_id)
        await command_hub.endpoint.on_disconnect(websocket, 1006)
        return websocket

    # Resolve/liveness succeed; the actual command lookup loses its route.
    monkeypatch.setattr(
        PluginHub, "_ensure_live_connection", lambda _session: asyncio.sleep(0, True)
    )
    monkeypatch.setattr(PluginHub, "_get_connection", disconnect_after_lookup)
    result = await dispatch()
    assert result["hint"] == "retry"
    assert result["data"]["reason"] == "command_not_dispatched"
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_shutdown_between_lookup_and_admission_remains_safe_to_retry(
    command_hub, monkeypatch
):
    get_session = command_hub.registry.get_session

    async def shutdown_after_lookup(session_id):
        session = await get_session(session_id)
        await PluginHub.shutdown()
        return session

    monkeypatch.setattr(command_hub.registry, "get_session", shutdown_after_lookup)
    result = await dispatch()
    assert result["hint"] == "retry"
    assert result["data"]["reason"] == "command_not_dispatched"
    assert command_hub.sent == []
