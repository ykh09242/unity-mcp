"""Composed discovery retains explicit activation and actual inventory semantics."""

from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest

import services.tools.manage_tools as module
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import


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
    monkeypatch.setattr(module, "_visible_tools", AsyncMock(return_value=[]))
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
    result = await selected_context(target, asyncio.get_running_loop().time() + 3)
    assert result["generation"] == "generation"
    assert result["editor_state"]["compilation"] == {"is_compiling": True}
    assert "unexpected" not in result["editor_state"]["unity"]
    fetch.assert_awaited_once_with(target, "get_editor_state", {})
    fetch.reset_mock()
    assert (await selected_context(None, asyncio.get_running_loop().time() + 3))[
        "status"
    ] == "unavailable"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reconnect", [False, True])
async def test_switch_snapshot_uses_new_editor_generation(command_hub, monkeypatch, reconnect):
    import services.tools.set_active_instance as selection
    from tests.integration.test_helpers import DummyContext
    from transport.models import CommandResultMessage
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(selection, "get_unity_instance_middleware", lambda: middleware)
    ctx = DummyContext()
    await command_hub.registry.register("old-session", "Old", "old-hash", "6000.3")
    await middleware.set_active_instance(ctx, "Old@old-hash")
    await ctx.set_state("unity_instance", "Old@old-hash", serializable=False)
    await ctx.set_state("unity_session_id", "old-session", serializable=False)
    original = middleware.set_active_instance

    async def persist(context, instance):
        await original(context, instance)
        if reconnect:
            command_hub.websocket.state.plugin_generation = "replacement"

    async def answer(message):
        command_hub.sent.append(message["name"])
        await command_hub.endpoint._handle_command_result(
            command_hub.websocket,
            CommandResultMessage(
                id=message["id"], result={"success": True, "data": {"activity": {"phase": "idle"}}}
            ),
        )

    monkeypatch.setattr(middleware, "set_active_instance", persist)
    command_hub.websocket.send_json = answer
    result = await selection.set_active_instance(ctx, "Fixture@hash", include_context=True)
    assert result["success"] and await middleware.get_active_instance(ctx) == "Fixture@hash"
    context = result["data"]["context"]
    if reconnect:
        assert context == {"status": "unavailable", "reason": "observation_generation_changed"}
        assert command_hub.sent == []
    else:
        assert context["instance"] == "Fixture@hash" and context["generation"] == "generation"
        assert context["editor_state"]["activity"] == {"phase": "idle"}
        assert command_hub.sent == ["get_editor_state"]


@pytest.mark.asyncio
async def test_remote_switch_checks_read_permission_on_new_socket(command_hub, monkeypatch):
    import services.tools.set_active_instance as selection
    import services.tools.wait_for_operation as waiting
    from tests.integration.test_helpers import DummyContext
    from tests.remote_credentials import remote_credentials
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setattr(selection.config, "http_remote_hosted", True)
    monkeypatch.setattr(selection, "get_unity_instance_middleware", UnityInstanceMiddleware)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    ctx = DummyContext()
    await ctx.set_state("user_id", "alice", serializable=False)
    await ctx.set_state("unity_session_id", "old-session", serializable=False)
    fetch = AsyncMock()
    monkeypatch.setattr(waiting, "fetch_observation", fetch)
    async with remote_credentials({"request": "alice", "plugin": "alice"}) as credentials:
        await credentials.attach_socket(command_hub.websocket, "plugin")
        await credentials.service.invalidate_cache("plugin")
        async with credentials.requester("request"):
            result = await selection.set_active_instance(ctx, "Fixture@hash", include_context=True)
    assert result["success"]
    assert result["data"]["context"] == {"status": "unavailable", "reason": "CredentialLeaseError"}
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_stdio_switch_captures_and_reads_only_selected_connection(monkeypatch):
    import services.tools.set_active_instance as selection
    import services.tools.wait_for_operation as waiting
    from tests.integration.test_helpers import DummyContext
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setattr(selection.config, "transport_mode", "stdio")
    monkeypatch.setattr(selection.config, "http_remote_hosted", False)
    monkeypatch.setattr(selection, "get_unity_instance_middleware", UnityInstanceMiddleware)
    target = SimpleNamespace(id="New@bbbbbbbb", hash="bbbbbbbb")
    monkeypatch.setattr(
        selection,
        "get_unity_connection_pool",
        lambda: SimpleNamespace(discover_all_instances=lambda **_: [target]),
    )
    generation = AsyncMock(return_value="new-generation")
    send = AsyncMock(return_value={"success": True, "data": {"assets": {"is_updating": False}}})
    monkeypatch.setattr(waiting, "get_authenticated_stdio_generation", generation)
    monkeypatch.setattr(waiting, "async_send_command_with_retry", send)
    ctx = DummyContext()
    await ctx.set_state("unity_instance", "Old@aaaaaaaa", serializable=False)
    result = await selection.set_active_instance(ctx, target.id, include_context=True)
    assert result["data"]["context"]["status"] == "observed"
    generation.assert_awaited_once_with(target.id)
    send.assert_awaited_once_with(
        "get_editor_state",
        {},
        instance_id=target.id,
        retry_on_reload=False,
        _expected_generation="new-generation",
    )
