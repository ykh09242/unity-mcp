"""Custom polling retains only the response actually returned to the client."""

import asyncio
import gc
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketState

from core.config import config
from models.models import ToolDefinitionModel
from models.response_limits import ResponseOwner, response_owner, response_size
from models.unity_response import normalize_unity_response
import services.custom_tool_service as module
from services.custom_tool_service import CustomToolService
from transport.charge_ledger import ChargeLedger
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub


class TrackedPayload(str):
    """Weakly observable large JSON value without changing its wire contents."""


@pytest.fixture
def polling(monkeypatch):
    class OwnedHub(PluginHub):
        _lock = asyncio.Lock()
        _registry = SimpleNamespace(
            get_session=AsyncMock(return_value=SimpleNamespace(user_id="alice"))
        )
        _connections = {}
        _pending = {}
        _retained_results = ChargeLedger()
        _raw_results = ChargeLedger()
        _large_results = None

    endpoint = OwnedHub.__new__(OwnedHub)
    websocket = SimpleNamespace(
        state=SimpleNamespace(plugin_generation="owned"),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
    )
    OwnedHub._connections["session"] = websocket
    state = SimpleNamespace(
        hub=OwnedHub,
        calls=0,
        status="complete",
        blocked_at=None,
        blocked=asyncio.Event(),
        payload_refs=[],
        ledgers=[],
        observations=[],
        replace_at=None,
        inspect_payloads=False,
        fail_at=None,
        retained_payloads=[],
    )

    async def send_json(message):
        state.calls += 1
        state.observations.append(len(OwnedHub._retained_results))
        if state.inspect_payloads:
            gc.collect()
            state.retained_payloads.append(sum(ref() is not None for ref in state.payload_refs))
        if state.calls == state.blocked_at:
            state.blocked.set()
            await asyncio.Event().wait()
        blob = TrackedPayload("x" * 131_072)
        state.payload_refs.append(weakref.ref(blob))
        final = state.calls == 12
        response = {
            "_mcp_status": state.status if final else "pending",
            "_mcp_poll_interval": 0.1,
            "success": state.status != "error" if final else True,
            "error": "Build failed" if final and state.status == "error" else None,
            "data": {"job_id": "job-1", "value": state.calls, "blob": blob},
        }
        await endpoint._handle_command_result(
            websocket, CommandResultMessage(id=message["id"], result=response)
        )

    websocket.send_json = send_json

    async def dispatch(_send, _target, name, params, **kwargs):
        assert kwargs == {"user_id": "alice"}
        if state.calls + 1 == state.replace_at:
            state.ledgers.append(OwnedHub._retained_results)
            OwnedHub._retained_results = ChargeLedger()
        result = normalize_unity_response(await OwnedHub.send_command("session", name, params))
        if state.calls == state.fail_at:
            raise RuntimeError("transient status failure after acquisition")
        return result

    async def sleep(_delay):
        # Preserve scheduling without waiting through twelve production intervals.
        await asyncio.sleep(0)

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(module, "send_with_unity_instance", dispatch)
    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(sleep=sleep, wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError),
    )
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(SimpleNamespace(custom_route=lambda *_a, **_k: lambda fn: fn))
    monkeypatch.setattr(
        service,
        "get_tool_definition",
        AsyncMock(
            return_value=ToolDefinitionModel(
                name="build", requires_polling=True, max_poll_seconds=30
            )
        ),
    )
    state.service = service
    yield state
    for entry in OwnedHub._pending.values():
        entry["future"].cancel()


async def execute(state):
    return await state.service.execute_tool(
        "project", "build", "Project@hash", {"action": "start"}, user_id="alice"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["complete", "error"])
async def test_many_custom_polls_finish_under_two_response_budget(polling, monkeypatch, status):
    polling.status = status
    charge = response_size({"data": "x" * 131_072})
    # Two live results during replacement are admitted; historical responses are not.
    for name in (
        "MAX_RETAINED_RESULT_BYTES",
        "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
    ):
        monkeypatch.setattr(polling.hub, name, 3 * charge)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await execute(polling)
        assert result.data["value"] == 12
        assert result.success is (status == "complete")
        assert result.error == ("Build failed" if status == "error" else None)
        assert max(polling.observations) <= 1
        assert len(polling.hub._retained_results) == len(owner.entries) == 1
        gc.collect()
        assert all(ref() is None for ref in polling.payload_refs[:-1])
        assert polling.payload_refs[-1]() is not None
    finally:
        response_owner.reset(token)
        owner.release()
    assert not polling.hub._retained_results


@pytest.mark.asyncio
async def test_cancelled_custom_poll_releases_results_before_parent_delivery(polling):
    polling.blocked_at = 3
    owner = ResponseOwner()
    token = response_owner.set(owner)
    task = asyncio.create_task(execute(polling))
    try:
        await asyncio.wait_for(polling.blocked.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not owner.entries and not owner.released
        assert not polling.hub._retained_results
        assert not polling.hub._pending
        assert polling.service._active_polls == 0
        gc.collect()
        assert all(ref() is None for ref in polling.payload_refs)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_custom_poll_releases_old_ledger_after_generation_replacement(polling):
    polling.replace_at = 3
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await execute(polling)
        assert result.data.get("value") == 12, result.data
        assert all(not ledger for ledger in polling.ledgers)
        assert len(polling.hub._retained_results) == len(owner.entries) == 1
    finally:
        response_owner.reset(token)
        owner.release()
    assert not polling.hub._retained_results


@pytest.mark.asyncio
async def test_initial_large_response_is_collectable_during_later_polls(polling):
    polling.inspect_payloads = True
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert (await execute(polling)).data["value"] == 12
        # At each next dispatch only the immediately preceding result is live.
        assert polling.retained_payloads == [0] + [1] * 11
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_custom_poll_timeout_delivers_only_last_successful_observation(polling, monkeypatch):
    now = [0.0]
    polling.blocked_at = 3
    polling.service.get_tool_definition.return_value.max_poll_seconds = 1

    async def sleep(_delay):
        now[0] += 0.4
        await asyncio.sleep(0)

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(sleep=sleep, wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError),
    )
    owner = ResponseOwner()
    token = response_owner.set(owner)
    task = asyncio.create_task(execute(polling))
    try:
        await asyncio.wait_for(polling.blocked.wait(), 2)
        now[0] = 2.0
        result = await asyncio.wait_for(task, 2)
        assert not result.success and "Timeout" in result.message
        assert result.data["data"]["value"] == 2
        assert len(polling.hub._retained_results) == len(owner.entries) == 1
        gc.collect()
        assert polling.payload_refs[0]() is None
        assert polling.payload_refs[1]() is not None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        response_owner.reset(token)
        owner.release()
    assert not polling.hub._retained_results


@pytest.mark.asyncio
async def test_transient_poll_failure_releases_both_discarded_observations(polling):
    polling.fail_at = 3
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert (await execute(polling)).data["value"] == 12
        assert polling.observations[3] == 0
        assert len(owner.entries) == len(polling.hub._retained_results) == 1
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_custom_poll_preserves_other_results_owned_by_same_request(polling):
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        prior = await polling.hub.send_command("session", "prior", {})
        prior_key = owner.entries[0][1]
        assert (await execute(polling)).data["value"] == 12
        assert prior["data"]["value"] == 1
        assert prior_key in polling.hub._retained_results
        assert len(owner.entries) == len(polling.hub._retained_results) == 2
    finally:
        response_owner.reset(token)
        owner.release()
    assert not polling.hub._retained_results
