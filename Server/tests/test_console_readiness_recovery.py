"""Console recovery retains the production operation owner across a reconnect."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from core.config import config
from models import MCPResponse
from services.operation_policy import OperationMode
from services.registry.operation_context import active_operation
from services.registry.operation_invocation import admission_for, operation_session
from services.registry.operation_target import capture_http_target
from services.tools import read_console as console, refresh_unity as refresh
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("ready", [False, True])
async def test_supported_refusals_share_recovery_and_keep_diagnostics(monkeypatch, typed, ready):
    # Given either supported transport representation of the same reload refusal.
    refusal = {"success": False, "error": "busy", "hint": "retry", "data": {"reason": "reloading"}}
    original = MCPResponse(**refusal) if typed else refusal
    send = AsyncMock(
        side_effect=[
            original,
            MCPResponse(success=True, data=[]) if typed else {"success": True, "data": []},
        ]
    )
    wait = AsyncMock(return_value=(ready, None))
    monkeypatch.setattr(console, "send_with_unity_instance", send)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", wait)
    # When a console read attempts its one bounded recovery.
    result = await console._send_console_observation(
        None, "Owned@owned", "read_console", {"action": "get"}
    )
    # Then representation does not change recovery or erase the original diagnostic.
    assert wait.await_count == 1
    assert send.await_count == (2 if ready else 1)
    assert result["success"] is ready
    if not ready:
        assert result["data"]["reason"] == "reloading"
        assert result["hint"] == "retry"
        assert result["error"] == "busy"


@pytest_asyncio.fixture
async def peer(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    registry = PluginRegistry()
    await registry.register("owned", "Owned", "project", "fixture")
    state = SimpleNamespace(
        calls=[],
        scopes=[],
        deadlines=[],
        principal=None,
        project="project",
        block=False,
        entered=asyncio.Event(),
        cancelled=asyncio.Event(),
    )

    def socket(generation):
        return SimpleNamespace(
            state=SimpleNamespace(
                user_id=state.principal,
                plugin_generation=generation,
                plugin_arbitration_generation=None,
                plugin_features=frozenset(),
            )
        )

    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_connections", {"owned": socket("old")})
    monkeypatch.setattr(PluginHub, "_lock", None)

    async def wire(session, command, params, **kwargs):
        assert session == "owned"
        state.calls.append((command, dict(params)))
        state.deadlines.append(kwargs["_budget"].expires_at)
        state.scopes.append(active_operation().request.scope)
        if len(state.calls) == 1:
            return {"success": False, "hint": "retry", "data": {"reason": "reloading"}}
        if state.block:
            state.entered.set()
            try:
                await asyncio.Future()
            finally:
                state.cancelled.set()
        return {"success": True, "data": []}

    async def route(_sender, instance, command, params, **kwargs):
        assert instance == "Owned@project"
        return await PluginHub.send_command("owned", command, params)

    async def reconnect(_ctx, timeout_s):
        assert 0 < timeout_s <= 3
        await registry.register("owned", "Owned", state.project, "fixture", user_id=state.principal)
        PluginHub._connections["owned"] = socket("new")
        return True, None

    monkeypatch.setattr(PluginHub, "_send_command_response_admitted", wire)
    monkeypatch.setattr(console, "send_with_unity_instance", route)
    monkeypatch.setattr(
        console, "get_unity_instance_from_context", AsyncMock(return_value="Owned@project")
    )
    monkeypatch.setattr(refresh, "wait_for_editor_ready", reconnect)
    state.target = await capture_http_target("owned")
    return state


async def read_owned(peer, deadline):
    async with operation_session(
        admission_for(peer.target, OperationMode.EXCLUSIVE, expires_at=deadline)
    ):
        return await console.read_console(None, action="get")


@pytest.mark.asyncio
async def test_same_project_reconnect_retries_once_with_original_owner_and_deadline(peer):
    # Given a selected connection that reloads during the first read.
    deadline = asyncio.get_running_loop().time() + 1
    # When readiness reports the same project on its new generation.
    result = await read_owned(peer, deadline)
    # Then the real transport admission allows only the continuation, retaining ownership.
    assert result["success"] is True
    assert len(peer.calls) == 2 and peer.calls[0] == peer.calls[1]
    assert peer.scopes == [peer.target.scope, peer.target.scope]
    assert peer.deadlines == [deadline, deadline]
    fresh = await capture_http_target("owned")
    async with operation_session(
        admission_for(fresh, OperationMode.EXCLUSIVE, expires_at=deadline)
    ):
        await refresh.wait_for_editor_ready(None, timeout_s=1)
        refused = await PluginHub.send_command("owned", "read_console", {"action": "get"})
    assert refused["data"]["reason"] == "operation_target_changed"
    assert len(peer.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("principal", "other"), ("project", "other")])
async def test_reconnect_cannot_change_principal_or_project(peer, field, value):
    # Given a reload that resolves to another principal or project.
    setattr(peer, field, value)
    # When the real admission compares it to the original owner.
    result = await read_owned(peer, asyncio.get_running_loop().time() + 5)
    # Then no read is sent to that target, and both recovery diagnostics survive.
    assert not result["success"] and len(peer.calls) == 1
    assert result["data"]["reason"] == "operation_target_changed"
    assert result["observation"]["initial_failure"]["data"]["reason"] == "reloading"


@pytest.mark.asyncio
async def test_recovery_read_is_cancelled_at_original_deadline(peer):
    # Given a recovered peer that never completes its second read.
    peer.block = True
    deadline = asyncio.get_running_loop().time() + 0.1
    # When the original owner's budget expires.
    result = await read_owned(peer, deadline)
    # Then transport drains the pending read and does not extend the budget.
    assert not result["success"]
    assert peer.entered.is_set() and peer.cancelled.is_set()
    assert peer.deadlines == [deadline, deadline]


@pytest.mark.asyncio
async def test_cancellation_drains_recovery_read(peer):
    # Given an active recovered read.
    peer.block = True
    task = asyncio.create_task(read_owned(peer, asyncio.get_running_loop().time() + 5))
    await asyncio.wait_for(peer.entered.wait(), 2)
    # When the request is cancelled.
    task.cancel()
    # Then cancellation propagates and the transport child is drained.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert peer.cancelled.is_set()


@pytest.mark.asyncio
async def test_clear_and_uncertain_reads_are_never_replayed(peer, monkeypatch):
    # Given a transient clear refusal.
    wait = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(refresh, "wait_for_editor_ready", wait)
    # When clearing the console through the public function.
    async with operation_session(
        admission_for(peer.target, OperationMode.EXCLUSIVE, expires_at=None)
    ):
        result = await console.read_console(None, action="clear")
    # Then no readiness or replay is attempted.
    assert not result["success"] and len(peer.calls) == 1
    wait.assert_not_awaited()
    peer.calls.clear()
    send = AsyncMock(return_value={"success": False, "data": {"reason": "outcome_unknown"}})
    monkeypatch.setattr(console, "send_with_unity_instance", send)
    result = await console.read_console(None, action="get")
    assert not result["success"] and send.await_count == 1
    wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_recovery_expires_with_original_owner(peer, monkeypatch):
    # Given a readiness wait that cannot complete before the original operation expires.
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(_ctx, timeout_s):
        assert timeout_s <= 0.1
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(refresh, "wait_for_editor_ready", blocked)
    # When the bounded recovery wait runs under the original owner.
    result = await read_owned(peer, asyncio.get_running_loop().time() + 0.1)
    # Then the original refusal remains available, with no delayed second dispatch.
    assert entered.is_set() and cancelled.is_set()
    assert result["data"]["reason"] == "reloading"
    assert result["hint"] == "retry"
    assert len(peer.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorized,requested",
    [
        ({"action": "clear"}, {"action": "clear"}),
        ({"action": "get", "count": 1}, {"action": "get", "count": 2}),
    ],
)
async def test_console_continuation_does_not_grant_clear_or_changed_parameters(
    peer, authorized, requested
):
    from services.registry.operation_invocation import console_read_continuation

    # Given an existing owner and a new connection generation.
    async with operation_session(
        admission_for(peer.target, OperationMode.EXCLUSIVE, expires_at=None)
    ):
        await refresh.wait_for_editor_ready(None, timeout_s=1)
        # When a mutation or a different read tries to use the continuation scope.
        with console_read_continuation("read_console", authorized):
            result = await PluginHub.send_command("owned", "read_console", requested)
    # Then normal generation fencing still rejects it before wire dispatch.
    assert result["data"]["reason"] == "operation_target_changed"
    assert peer.calls == []
