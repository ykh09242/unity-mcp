"""Group selection and plugin catalogs retain their client and project scope."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP

from core.config import config
from models.models import ToolDefinitionModel, ToolParameterModel
from services.registry import DEFAULT_ENABLED_GROUPS, get_group_tool_names
from services.tools import sync_tool_visibility_from_unity
from services.tools.set_active_instance import set_active_instance
from services.tools.manage_tools import manage_tools
from transport.models import RegisterMessage, RegisterToolsMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.fixture
def group_server() -> FastMCP:
    server = FastMCP("visibility-migration")
    server.tool(name="manage_tools")(manage_tools)

    @server.tool(tags={"group:vfx"})
    def effect_probe() -> str:
        return "effect"

    server.disable(tags={"group:vfx"}, components={"tool"})
    return server


@pytest.mark.asyncio
async def test_legacy_group_selection_persists_and_isolated_between_clients(group_server: FastMCP) -> None:
    async with Client(group_server, mode="legacy") as first, Client(group_server, mode="legacy") as second:
        assert "effect_probe" not in {tool.name for tool in await first.list_tools()}
        result = await first.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        assert result.structured_content["activated"] == "vfx"
        assert result.structured_content["tools"] == get_group_tool_names().get("vfx", [])
        assert "effect_probe" in {tool.name for tool in await first.list_tools()}
        assert "effect_probe" not in {tool.name for tool in await second.list_tools()}
        result = await first.call_tool("manage_tools", {"action": "deactivate", "group": "vfx"})
        assert result.structured_content["deactivated"] == "vfx"
        assert "effect_probe" not in {tool.name for tool in await first.list_tools()}
        await first.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        result = await first.call_tool("manage_tools", {"action": "reset"})
        assert result.structured_content["reset"] is True
        assert result.structured_content["default_groups"] == sorted(DEFAULT_ENABLED_GROUPS)
        assert "effect_probe" not in {tool.name for tool in await first.list_tools()}
        assert "effect_probe" not in {tool.name for tool in await second.list_tools()}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["activate", "deactivate", "reset"])
async def test_sessionless_group_changes_report_unsupported_persistence(group_server: FastMCP, action: str) -> None:
    async with Client(group_server) as client:
        result = await client.call_tool("manage_tools", {"action": action, "group": "vfx"})
        assert "sessionless" in result.structured_content["error"]
        assert "effect_probe" not in {tool.name for tool in await client.list_tools()}
        groups = await client.call_tool("manage_tools", {"action": "list_groups"})
        assert any(group["name"] == "vfx" for group in groups.structured_content["groups"])


@pytest_asyncio.fixture
async def local_catalog(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted", "_retained_results"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp", "_unity_transform_start"):
        monkeypatch.setattr(PluginHub, name, None)
    metadata = [
        {"name": "run_tests", "group": "testing", "unity_target": "run_tests"},
        {"name": "manage_scene", "group": "core", "unity_target": "manage_scene"},
        {"name": "scene_alias", "group": "core", "unity_target": "manage_scene"},
        {"name": "set_active_instance", "group": None, "unity_target": None},
    ]
    monkeypatch.setattr("services.registry.tool_registry._tool_registry", metadata)
    server = FastMCP("local-plugin-catalog")
    server.tool(name="set_active_instance")(set_active_instance)

    @server.tool(tags={"group:testing"})
    def run_tests() -> str:
        return "testing"

    @server.tool(tags={"group:core"})
    def manage_scene() -> str:
        return "scene"

    @server.tool(tags={"group:core"})
    def scene_alias() -> str:
        return "alias"

    server.disable(tags={"group:testing"}, components={"tool"})
    registry = PluginRegistry()
    PluginHub.configure(registry, mcp=server)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    sockets = {}
    for project, tool_name in (("A", "run_tests"), ("B", "manage_scene")):
        socket = AsyncMock()
        socket.state = SimpleNamespace()
        sockets[project] = socket
        await hub._handle_register(socket, RegisterMessage(project_name=project, project_hash=project.lower()))
        await hub._handle_register_tools(socket, RegisterToolsMessage(tools=[ToolDefinitionModel(name=tool_name)]))
    yield server, hub, registry, sockets
    await PluginHub.shutdown()


@pytest.mark.asyncio
async def test_real_server_inventory_preserves_all_connected_project_groups(local_catalog) -> None:
    server, _, _, _ = local_catalog
    assert {tool.name for tool in await server.list_tools()} == {
        "run_tests", "manage_scene", "scene_alias", "set_active_instance",
    }


@pytest.mark.asyncio
async def test_manual_http_sync_preserves_other_project_real_inventory(local_catalog, monkeypatch: pytest.MonkeyPatch) -> None:
    server, _, _, _ = local_catalog
    send = AsyncMock(return_value={"success": True, "data": {"tools": [
        {"name": "manage_scene", "enabled": True},
    ]}})
    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result["synced"] is True
    assert {tool.name for tool in await server.list_tools()} == {
        "run_tests", "manage_scene", "scene_alias", "set_active_instance",
    }


@pytest.mark.asyncio
async def test_manual_http_sync_updates_only_queried_project_catalog(local_catalog, monkeypatch: pytest.MonkeyPatch) -> None:
    server, _, registry, _ = local_catalog
    send = AsyncMock(return_value={"success": True, "data": {"tools": [
        {"name": "manage_scene", "enabled": False},
        {"name": "run_tests", "enabled": True},
    ]}})
    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result["synced"] is True
    assert {tool.name for tool in await server.list_tools()} == {"run_tests", "set_active_instance"}
    assert {tool.name for tool in await PluginHub.get_tools_for_project("a")} == {"run_tests"}
    assert {tool.name for tool in await PluginHub.get_tools_for_project("b")} == {"run_tests"}


@pytest.mark.asyncio
async def test_manual_http_sync_does_not_overwrite_reconnected_project_catalog(local_catalog, monkeypatch: pytest.MonkeyPatch) -> None:
    server, hub, _, _ = local_catalog

    async def respond_after_reconnect(*args, **kwargs):
        replacement = AsyncMock()
        replacement.state = SimpleNamespace()
        await hub._handle_register(replacement, RegisterMessage(project_name="B", project_hash="b"))
        await hub._handle_register_tools(replacement, RegisterToolsMessage(tools=[ToolDefinitionModel(name="manage_scene")]))
        return {"success": True, "data": {"tools": [{"name": "run_tests", "enabled": True}]}}

    monkeypatch.setattr(PluginHub, "send_command_for_instance", respond_after_reconnect)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result["synced"] is True
    assert {tool.name for tool in await PluginHub.get_tools_for_project("b")} == {"manage_scene"}
    assert {tool.name for tool in await server.list_tools()} == {
        "run_tests", "manage_scene", "scene_alias", "set_active_instance",
    }


@pytest.mark.asyncio
async def test_manual_http_sync_preserves_registered_runtime_metadata(local_catalog, monkeypatch: pytest.MonkeyPatch) -> None:
    _, hub, _, sockets = local_catalog
    definition = ToolDefinitionModel(
        name="manage_scene", description="Owned scene definition", structured_output=False,
        requires_polling=True, poll_action="scene_status", max_poll_seconds=60,
        parameters=[ToolParameterModel(name="target", required=False, default_value="owned")],
    )
    await hub._handle_register_tools(sockets["B"], RegisterToolsMessage(tools=[definition]))
    send = AsyncMock(return_value={"success": True, "data": {"tools": [
        {"name": "manage_scene", "enabled": True},
    ]}})
    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result["synced"] is True
    assert await PluginHub.get_tools_for_project("b") == [definition]


@pytest.mark.asyncio
async def test_real_client_inventory_filters_union_to_selected_project(local_catalog) -> None:
    server, _, _, _ = local_catalog
    server.add_middleware(UnityInstanceMiddleware())
    async with Client(server, mode="legacy") as first, Client(server, mode="legacy") as second:
        await first.call_tool("set_active_instance", {"instance": "A@a"})
        await second.call_tool("set_active_instance", {"instance": "B@b"})
        assert {tool.name for tool in await first.list_tools()} == {"run_tests", "set_active_instance"}
        assert {tool.name for tool in await second.list_tools()} == {
            "manage_scene", "scene_alias", "set_active_instance",
        }


@pytest.mark.asyncio
async def test_reregistered_project_replaces_its_contribution_to_real_inventory(local_catalog) -> None:
    server, hub, _, sockets = local_catalog
    await hub._handle_register_tools(sockets["B"], RegisterToolsMessage(tools=[ToolDefinitionModel(name="run_tests")]))
    assert {tool.name for tool in await server.list_tools()} == {"run_tests", "set_active_instance"}
    await hub._handle_register_tools(sockets["A"], RegisterToolsMessage(tools=[ToolDefinitionModel(name="manage_scene")]))
    assert {tool.name for tool in await server.list_tools()} == {
        "run_tests", "manage_scene", "scene_alias", "set_active_instance",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("removal", ["disconnect", "evict"])
async def test_removed_project_no_longer_contributes_to_real_inventory(local_catalog, removal: str) -> None:
    server, hub, registry, sockets = local_catalog
    if removal == "disconnect":
        await hub.on_disconnect(sockets["B"], 1001)
    else:
        session_id = await registry.get_session_id_by_hash("b")
        await PluginHub._evict_connection(session_id, "test-removal")
    assert {tool.name for tool in await server.list_tools()} == {"run_tests", "set_active_instance"}


@pytest.mark.asyncio
async def test_reconnected_project_preserves_other_project_inventory(local_catalog) -> None:
    server, hub, _, _ = local_catalog
    replacement = AsyncMock()
    replacement.state = SimpleNamespace()
    await hub._handle_register(replacement, RegisterMessage(project_name="B", project_hash="b"))
    assert {tool.name for tool in await server.list_tools()} == {"run_tests", "set_active_instance"}
    await hub._handle_register_tools(replacement, RegisterToolsMessage(tools=[ToolDefinitionModel(name="manage_scene")]))
    assert {tool.name for tool in await server.list_tools()} == {
        "run_tests", "manage_scene", "scene_alias", "set_active_instance",
    }


@pytest.mark.asyncio
async def test_last_disconnect_restores_startup_inventory(local_catalog) -> None:
    server, hub, _, sockets = local_catalog
    await hub.on_disconnect(sockets["B"], 1001)
    await hub.on_disconnect(sockets["A"], 1001)
    assert {tool.name for tool in await server.list_tools()} == {
        "manage_scene", "scene_alias", "set_active_instance",
    }
