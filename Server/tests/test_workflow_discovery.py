"""Composed discovery retains explicit activation and actual inventory semantics."""

from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, Mock

import pytest

import services.tools.manage_tools as module


@pytest.mark.asyncio
async def test_activation_can_return_schema_and_declared_readiness(monkeypatch):
    def feature(action: Literal["capabilities", "edit"]):
        return action

    descriptor = {"name": "feature", "inputSchema": {"type": "object"}}
    definition = Mock()
    definition.to_mcp_tool.return_value.model_dump.return_value = descriptor
    discovery = SimpleNamespace(
        definitions={"feature": definition}, update=AsyncMock(return_value=True)
    )
    ctx = SimpleNamespace(
        fastmcp=SimpleNamespace(_tool_discovery=discovery), enable_components=AsyncMock()
    )
    monkeypatch.setattr(module, "is_sessionless", lambda _ctx: False)
    monkeypatch.setattr(
        module,
        "get_registered_tools",
        lambda: [{"name": "feature", "group": "test", "func": feature}],
    )
    result = await module.manage_tools(ctx, "activate", tool="feature", include_schema=True)
    assert result["description"]["tool"] == descriptor
    assert result["client_inventory_status"] == "unconfirmed"
    assert result["readiness"] == {
        "status": "unobserved",
        "next_action": {"tool": "feature", "action": "capabilities"},
    }
    discovery.update.assert_awaited_once_with(ctx, ["feature"], enabled=True)


@pytest.mark.asyncio
async def test_schema_option_rejected_before_group_activation(monkeypatch):
    discovery = SimpleNamespace(update=AsyncMock())
    ctx = SimpleNamespace(fastmcp=SimpleNamespace(_tool_discovery=discovery))
    monkeypatch.setattr(module, "is_sessionless", lambda _ctx: False)
    result = await module.manage_tools(ctx, "activate", group="ui", include_schema=True)
    assert result["success"] is False
    discovery.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_context_is_bounded_to_captured_editor(monkeypatch):
    import services.operation_permissions as permissions
    import services.tools.wait_for_operation as waiting
    from services.tools.set_active_instance import selected_context

    target = waiting.ObservationTarget("Project@hash", None, "generation", None)
    monkeypatch.setattr(permissions, "authorize_remote_operation", AsyncMock())
    monkeypatch.setattr(waiting, "capture_target", AsyncMock(return_value=target))
    fetch = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "unity": {"unity_version": "6000.0", "unexpected": "large" * 1000},
                "compilation": {"is_compiling": True},
                "assets": {"is_updating": False},
            },
        }
    )
    monkeypatch.setattr(waiting, "fetch_observation", fetch)
    result = await selected_context(None, "Project@hash")
    assert result["generation"] == "generation"
    assert result["editor_state"]["compilation"] == {"is_compiling": True}
    assert "unexpected" not in result["editor_state"]["unity"]
    fetch.assert_awaited_once_with(target, "get_editor_state", {})
    fetch.reset_mock()
    assert (await selected_context(None, "Other@hash"))["status"] == "unavailable"
    fetch.assert_not_awaited()
