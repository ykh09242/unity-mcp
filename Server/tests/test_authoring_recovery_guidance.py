"""Recovery guidance must not turn a stale generation into a mutation replay."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.registry import operation_invocation as mod
from services.registry.operation_scheduler import OperationScope, OperationScopeChanged
from services.registry.operation_target import OperationTarget
from transport.command_budget import _CommandBudget


@pytest.mark.asyncio
@pytest.mark.parametrize("inside_call", [False, True])
async def test_generation_refusal_explains_when_the_tool_may_have_run(monkeypatch, inside_call):
    async def authorize(*args, **kwargs):
        pass

    async def target(*args, **kwargs):
        return OperationTarget(OperationScope("local", "owned", "old"), lambda: inside_call, True)

    async def callback(ctx):
        raise OperationScopeChanged()

    monkeypatch.setattr(mod, "authorize_remote_operation", authorize)
    monkeypatch.setattr(mod, "capture_target", target)
    monkeypatch.setattr(mod, "trusted_tool", lambda *_: True)
    monkeypatch.setattr("services.tools.get_unity_instance_from_context", authorize)
    result = await mod.wrap_tool(callback, "prefab_contents", "manage_prefabs")(SimpleNamespace())
    assert result["error"] == "operation_target_changed"
    assert result["data"]["dispatch"] == ("unknown" if inside_call else "not_dispatched")
    assert result["hint"] == ("inspect_state_before_retry" if inside_call else "select_instance")
    assert "mcpforunity://instances" in result["message"]
    assert result["data"]["automatic_retry"] is False


def test_console_readiness_timeout_guides_wait_without_extending_the_clock():
    budget = _CommandBudget(123, phase="readiness", command="read_console")
    result = budget.timeout_response("read_console")
    assert result["data"]["dispatch"] == "not_dispatched"
    assert result["data"]["next_action"] == "wait_for_editor_ready"
    assert "mcpforunity://editor/state" in result["message"]
    assert "Play Mode" in result["message"]
    assert budget.expires_at == 123


def test_console_dispatched_timeout_never_promises_safe_replay():
    result = _CommandBudget(123, phase="response", dispatched=True).timeout_response("read_console")
    assert result["hint"] == "inspect_state_before_retry"
    assert result["data"]["reason"] == "outcome_unknown"
    assert "next_action" not in result["data"]


@pytest.mark.asyncio
async def test_console_probe_expiry_is_also_an_explicit_undispatched_wait(monkeypatch):
    from transport import plugin_hub as hub

    clock = SimpleNamespace(now=0.0)

    async def probe(*args):
        clock.now = 2.0
        return "retry"

    monkeypatch.setattr(hub.config, "http_remote_hosted", False)
    monkeypatch.setattr(hub, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setenv("UNITY_MCP_SESSION_READY_WAIT_SECONDS", "1")
    monkeypatch.setattr(hub.PluginHub, "_resolve_session_id", AsyncMock(return_value="owned"))
    monkeypatch.setattr(hub.PluginHub, "_ensure_live_connection", AsyncMock(return_value=True))
    monkeypatch.setattr(hub.PluginHub, "_read_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(hub.PluginHub, "_readiness_probe", probe)
    dispatch = AsyncMock()
    monkeypatch.setattr(hub.PluginHub, "send_command", dispatch)
    budget = _CommandBudget(123, command="read_console")
    result = await hub.PluginHub._send_command_for_instance(
        "Owned@hash", "read_console", {}, None, True, budget=budget
    )
    assert result["data"]["dispatch"] == "not_dispatched"
    assert result["data"]["next_action"] == "wait_for_editor_ready"
    dispatch.assert_not_awaited()
