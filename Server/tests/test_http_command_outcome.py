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

    websocket = SimpleNamespace(
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
