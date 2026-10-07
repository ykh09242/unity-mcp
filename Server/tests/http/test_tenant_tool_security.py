"""Remote plugin metadata must remain bound to its authenticated session."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from core.config import config
from models.models import ToolDefinitionModel
from services.custom_tool_service import CustomToolService
from transport.models import RegisterToolsMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.fixture
def tenant_state(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    registry = PluginRegistry()
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong"):
        monkeypatch.setattr(PluginHub, name, {})
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_unity_transform_start", None)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    mcp = FastMCP("tenant-test")
    monkeypatch.setattr(PluginHub, "_mcp", mcp)
    return registry, mcp, CustomToolService(mcp)


@pytest.mark.asyncio
async def test_remote_registration_does_not_mutate_global_catalog(tenant_state, monkeypatch):
    registry, mcp, service = tenant_state
    notifications = AsyncMock()
    monkeypatch.setattr(PluginHub, "_notify_mcp_tool_list_changed", notifications)
    hub = PluginHub({"type": "websocket"}, receive=AsyncMock(), send=AsyncMock())
    transforms = list(mcp._transforms)
    for user in ("alice", "bob"):
        ws = SimpleNamespace(state=SimpleNamespace(user_id=user))
        await registry.register(user, "Project", "shared-hash", "6000", user_id=user)
        PluginHub._connections[user] = ws
        await hub._handle_register_tools(
            ws,
            RegisterToolsMessage(
                tools=[
                    ToolDefinitionModel(name="shared_tool", description=user),
                ]
            ),
        )

    assert await mcp.list_tools() == []
    assert mcp._transforms == transforms
    assert service._global_tools == {}
    notifications.assert_not_awaited()
    for user in ("alice", "bob"):
        tool = await service.get_tool_definition("shared-hash", "shared_tool", user_id=user)
        assert tool.description == user

    await hub.on_disconnect(PluginHub._connections["alice"], 1000)
    assert await service.list_registered_tools("shared-hash", user_id="alice") == []
    assert len(await service.list_registered_tools("shared-hash", user_id="bob")) == 1


@pytest.mark.asyncio
async def test_remote_lookup_never_falls_back_to_global_or_local_metadata(tenant_state):
    registry, mcp, service = tenant_state
    stale = ToolDefinitionModel(name="private", description="another tenant")
    service._global_tools["private"] = stale
    service._project_tools["project"] = {"private": stale}

    assert await service.get_tool_definition("project", "private", user_id="bob") is None
    assert await service.list_registered_tools("project", user_id="bob") == []
    assert await service.get_tool_definition("project", "private") is None


def test_tool_registration_count_is_bounded():
    with pytest.raises(ValidationError):
        RegisterToolsMessage(tools=[ToolDefinitionModel(name=f"tool_{i}") for i in range(257)])


@pytest.mark.asyncio
async def test_oversized_tool_metadata_is_rejected_before_storage(tenant_state):
    registry, mcp, service = tenant_state
    ws = AsyncMock()
    await registry.register("alice", "Project", "hash", "6000", user_id="alice")
    PluginHub._connections["alice"] = ws
    hub = PluginHub({"type": "websocket"}, receive=AsyncMock(), send=AsyncMock())

    await hub._handle_register_tools(
        ws,
        RegisterToolsMessage(
            tools=[
                ToolDefinitionModel(name="oversized", description="x" * (512 * 1024)),
            ]
        ),
    )

    ws.close.assert_awaited_once()
    assert await service.list_registered_tools("hash", user_id="alice") == []


@pytest.mark.asyncio
async def test_tool_listing_filters_each_users_enabled_set(tenant_state, monkeypatch):
    registry, mcp, service = tenant_state
    metadata = [
        {"name": "manage_scene", "unity_target": "manage_scene"},
        {"name": "manage_gameobject", "unity_target": "manage_gameobject"},
        {"name": "execute_custom_tool", "unity_target": None},
    ]
    monkeypatch.setattr(
        "transport.unity_instance_middleware.get_registered_tools", lambda: metadata
    )
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_inject_unity_instance", AsyncMock())
    tools = [
        SimpleNamespace(name=name)
        for name in ("manage_scene", "manage_gameobject", "execute_custom_tool", "foreign_custom")
    ]
    for user, enabled in (("alice", "manage_scene"), ("bob", "manage_gameobject")):
        await registry.register(user, "Project", "hash", "6000", user_id=user)
        await registry.register_tools_for_session(user, [ToolDefinitionModel(name=enabled)])

    for user, enabled in (("alice", "manage_scene"), ("bob", "manage_gameobject")):
        state = {"user_id": user, "unity_instance": "Project@hash"}
        context = SimpleNamespace(
            fastmcp_context=SimpleNamespace(get_state=AsyncMock(side_effect=state.get))
        )

        visible = await middleware.on_list_tools(context, AsyncMock(return_value=tools))

        assert {tool.name for tool in visible} == {enabled, "execute_custom_tool"}
