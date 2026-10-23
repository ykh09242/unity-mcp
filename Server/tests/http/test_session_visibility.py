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
from transport.charge_ledger import ChargeLedger
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
async def test_legacy_group_selection_persists_and_isolated_between_clients(
    group_server: FastMCP,
) -> None:
    async with (
        Client(group_server, mode="legacy") as first,
        Client(group_server, mode="legacy") as second,
    ):
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
async def test_sessionless_group_changes_report_unsupported_persistence(
    group_server: FastMCP, action: str
) -> None:
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
    for name in (
        "_connections",
        "_pending",
        "_ping_tasks",
        "_last_pong",
        "_admitted",
        "_retained_results",
    ):
        monkeypatch.setattr(PluginHub, name, ChargeLedger() if name == "_retained_results" else {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
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

    # This fixture explicitly selects testing; availability still caps each request.
    server.add_middleware(UnityInstanceMiddleware())
    registry = PluginRegistry()
    PluginHub.configure(registry, mcp=server)
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    sockets = {}
    for project, tool_name in (("A", "run_tests"), ("B", "manage_scene")):
        socket = AsyncMock()
        socket.state = SimpleNamespace()
        sockets[project] = socket
        await hub._handle_register(
            socket, RegisterMessage(project_name=project, project_hash=project.lower())
        )
        await hub._handle_register_tools(
            socket, RegisterToolsMessage(tools=[ToolDefinitionModel(name=tool_name)])
        )
    yield server, hub, registry, sockets
    await PluginHub.shutdown()


async def inventory(server):
    async with Client(server, mode="legacy") as client:
        return {tool.name for tool in await client.list_tools()}


@pytest.mark.asyncio
async def test_real_server_inventory_preserves_all_connected_project_groups(local_catalog) -> None:
    server, _, _, _ = local_catalog
    assert await inventory(server) == {
        "run_tests",
        "manage_scene",
        "scene_alias",
        "set_active_instance",
    }


@pytest.mark.asyncio
async def test_manual_http_sync_preserves_other_project_real_inventory(
    local_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, _, _, _ = local_catalog
    send = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "tools": [
                    {"name": "manage_scene", "enabled": True},
                ]
            },
        }
    )
    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result["synced"] is True
    assert await inventory(server) == {
        "run_tests",
        "manage_scene",
        "scene_alias",
        "set_active_instance",
    }


@pytest.mark.asyncio
async def test_manual_http_sync_updates_only_queried_project_catalog(
    local_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, _, registry, _ = local_catalog
    send = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "tools": [
                    {"name": "manage_scene", "enabled": False},
                    {"name": "run_tests", "enabled": True},
                ]
            },
        }
    )
    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result["synced"] is True
    assert await inventory(server) == {"run_tests", "set_active_instance"}
    assert {tool.name for tool in await PluginHub.get_tools_for_project("a")} == {"run_tests"}
    assert {tool.name for tool in await PluginHub.get_tools_for_project("b")} == {"run_tests"}


@pytest.mark.asyncio
async def test_manual_http_sync_does_not_overwrite_reconnected_project_catalog(
    local_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, hub, _, _ = local_catalog

    async def respond_after_reconnect(*args, **kwargs):
        replacement = AsyncMock()
        replacement.state = SimpleNamespace()
        await hub._handle_register(replacement, RegisterMessage(project_name="B", project_hash="b"))
        await hub._handle_register_tools(
            replacement, RegisterToolsMessage(tools=[ToolDefinitionModel(name="manage_scene")])
        )
        return {"success": True, "data": {"tools": [{"name": "run_tests", "enabled": True}]}}

    monkeypatch.setattr(PluginHub, "send_command_for_instance", respond_after_reconnect)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert "synced" not in result
    assert result["error"] == "Unity catalog generation changed"
    assert {tool.name for tool in await PluginHub.get_tools_for_project("b")} == {"manage_scene"}
    assert await inventory(server) == {
        "run_tests",
        "manage_scene",
        "scene_alias",
        "set_active_instance",
    }


@pytest.mark.asyncio
async def test_manual_http_sync_preserves_registered_runtime_metadata(
    local_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, hub, _, sockets = local_catalog
    definition = ToolDefinitionModel(
        name="manage_scene",
        description="Owned scene definition",
        structured_output=False,
        requires_polling=True,
        poll_action="scene_status",
        max_poll_seconds=60,
        parameters=[ToolParameterModel(name="target", required=False, default_value="owned")],
    )
    await hub._handle_register_tools(sockets["B"], RegisterToolsMessage(tools=[definition]))
    send = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "tools": [
                    {"name": "manage_scene", "enabled": True},
                ]
            },
        }
    )
    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result["synced"] is True
    assert await PluginHub.get_tools_for_project("b") == [definition]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_old_http_toggles_preserve_omitted_metadata_and_ignore_unknown_custom_schema(
    local_catalog, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from services.custom_tool_service import CustomToolService

    server, hub, _, sockets = local_catalog
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(server)
    known = ToolDefinitionModel(
        name="known_custom",
        description="authoritative",
        structured_output=False,
        requires_polling=True,
        poll_action="owned_status",
        max_poll_seconds=30,
        parameters=[
            ToolParameterModel(name="value", type="integer", required=False, default_value="7")
        ],
    )
    await hub._handle_register_tools(
        sockets["B"],
        RegisterToolsMessage(
            tools=[
                ToolDefinitionModel(name="manage_scene"),
                known,
            ]
        ),
    )
    component = service._owned_tools["known_custom"]
    monkeypatch.setattr(
        PluginHub,
        "send_command_for_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "tools": [
                        {"name": "manage_scene", "enabled": False},
                        {
                            "name": "known_custom",
                            "enabled": True,
                            "description": "not a schema",
                            "parameters": [],
                        },
                        {
                            "name": "unknown_custom",
                            "enabled": True,
                            "parameters": [{"name": "forged", "type": "string"}],
                        },
                    ]
                },
            }
        ),
    )
    assert (await sync_tool_visibility_from_unity("B@b", notify=False))["synced"]
    assert await PluginHub.get_tools_for_project("b") == [known]
    assert service._owned_tools["known_custom"] is component
    async with Client(server, mode=mode) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert set(tools) == {"known_custom", "run_tests", "set_active_instance"}
        assert set(tools["known_custom"].input_schema["properties"]) == {"value"}
    # Omission from an old-format reply is not an authoritative schema removal.
    monkeypatch.setattr(
        PluginHub,
        "send_command_for_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"tools": [{"name": "manage_scene", "enabled": False}]},
            }
        ),
    )
    assert (await sync_tool_visibility_from_unity("B@b", notify=False))["synced"]
    assert await PluginHub.get_tools_for_project("b") == [known]
    assert service._owned_tools["known_custom"] is component


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["fresh", "authoritative", "reconnected"])
async def test_old_http_toggle_does_not_create_authoritative_empty_evidence(
    local_catalog, monkeypatch: pytest.MonkeyPatch, evidence: str
) -> None:
    from services.custom_tool_service import CustomToolService

    server, hub, registry, _ = local_catalog
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(server)
    socket = AsyncMock()
    socket.state = SimpleNamespace()
    await hub._handle_register(socket, RegisterMessage(project_name="C", project_hash="c"))
    first_generation = socket.state.plugin_session_id
    if evidence != "fresh":
        await hub._handle_register_tools(
            socket, RegisterToolsMessage(tools=[ToolDefinitionModel(name="manage_scene")])
        )
    if evidence == "reconnected":
        socket = AsyncMock()
        socket.state = SimpleNamespace()
        await hub._handle_register(socket, RegisterMessage(project_name="C", project_hash="c"))
        assert socket.state.plugin_session_id != first_generation
    monkeypatch.setattr(
        PluginHub,
        "send_command_for_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"tools": [{"name": "manage_scene", "enabled": True}]},
            }
        ),
    )
    assert (await sync_tool_visibility_from_unity("C@c", notify=False))["synced"]
    source = service._sources[("http", None, "c")]
    assert source.generation == socket.state.plugin_session_id
    assert source.known is (evidence == "authoritative")
    session = await registry.get_session(socket.state.plugin_session_id)
    original_tools = session.tools
    original_revision = session.tools_revision
    original_sources = service._sources
    original_transforms = server._transforms
    async with Client(server, mode="legacy") as client:
        original_inventory = {tool.name for tool in await client.list_tools()}
        monkeypatch.setattr(
            PluginHub,
            "send_command_for_instance",
            AsyncMock(return_value={"success": True, "data": {"tools": []}}),
        )
        result = await sync_tool_visibility_from_unity("C@c", notify=False)
        assert {tool.name for tool in await client.list_tools()} == original_inventory
    if evidence == "authoritative":
        assert result["synced"]
        assert session.tools == {}
        assert session.tools_revision == original_revision + 1
    else:
        assert result == {"error": "Empty Unity catalog has no same-generation extended evidence"}
        assert session.tools is original_tools and session.tools_revision == original_revision
        assert service._sources is original_sources and server._transforms is original_transforms


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_tail",
    [
        {"name": "unknown_custom", "enabled": "true"},
        {"name": "unknown_custom", "parameters": [{"name": "bad", "required": "true"}]},
        {"name": "manage_scene", "enabled": True},
        "not a descriptor",
    ],
)
async def test_malformed_old_http_toggle_delta_rejects_whole_publication(
    local_catalog, monkeypatch: pytest.MonkeyPatch, invalid_tail
) -> None:
    server, _, registry, sockets = local_catalog
    session = await registry.get_session(sockets["B"].state.plugin_session_id)
    original_tools = session.tools
    original_revision = session.tools_revision
    original_transforms = server._transforms
    monkeypatch.setattr(
        PluginHub,
        "send_command_for_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"tools": [{"name": "manage_scene", "enabled": False}, invalid_tail]},
            }
        ),
    )
    assert "error" in await sync_tool_visibility_from_unity("B@b", notify=False)
    assert session.tools is original_tools and session.tools_revision == original_revision
    assert server._transforms is original_transforms
    assert await inventory(server) == {
        "run_tests",
        "manage_scene",
        "scene_alias",
        "set_active_instance",
    }


@pytest.mark.asyncio
async def test_old_http_toggle_publication_failure_restores_union_and_catalog(
    local_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.custom_tool_service import CustomToolService

    server, _, registry, sockets = local_catalog
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(server)
    session = await registry.get_session(sockets["B"].state.plugin_session_id)
    original_tools = session.tools
    original_revision = session.tools_revision
    original_transforms = server._transforms
    original_sources = service._sources
    add_tool = server.add_tool

    def fail_after_mutation(tool):
        add_tool(tool)
        raise RuntimeError("component publication failed after mutation")

    monkeypatch.setattr(server, "add_tool", fail_after_mutation)
    monkeypatch.setattr(
        PluginHub,
        "send_command_for_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "tools": [{"name": "custom_probe", "enabled": True, "is_built_in": False}]
                },
            }
        ),
    )
    assert "error" in await sync_tool_visibility_from_unity("B@b", notify=False)
    assert session.tools is original_tools and session.tools_revision == original_revision
    assert server._transforms is original_transforms and service._sources is original_sources
    assert await inventory(server) == {
        "run_tests",
        "manage_scene",
        "scene_alias",
        "set_active_instance",
    }


@pytest.mark.asyncio
async def test_old_http_toggle_merge_rejects_catalog_capacity_overflow(
    local_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, hub, registry, sockets = local_catalog
    await hub._handle_register_tools(
        sockets["B"],
        RegisterToolsMessage(
            tools=[ToolDefinitionModel(name=f"known_{index}") for index in range(256)]
        ),
    )
    session = await registry.get_session(sockets["B"].state.plugin_session_id)
    original = session.tools
    monkeypatch.setattr(
        PluginHub,
        "send_command_for_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"tools": [{"name": "run_tests", "enabled": True}]},
            }
        ),
    )
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result == {"error": "Unity catalog exceeds supported bounds"}
    assert session.tools is original


@pytest.mark.asyncio
async def test_old_http_toggle_reply_cannot_overwrite_newer_same_session_revision(
    local_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, hub, _, sockets = local_catalog
    replacement = ToolDefinitionModel(name="manage_scene", description="new revision")

    async def after_update(*_args, **_kwargs):
        await hub._handle_register_tools(sockets["B"], RegisterToolsMessage(tools=[replacement]))
        return {"success": True, "data": {"tools": [{"name": "manage_scene", "enabled": False}]}}

    monkeypatch.setattr(PluginHub, "send_command_for_instance", after_update)
    result = await sync_tool_visibility_from_unity("B@b", notify=False)
    assert result == {"error": "Unity catalog generation changed"}
    assert await PluginHub.get_tools_for_project("b") == [replacement]


@pytest.mark.asyncio
async def test_real_client_inventory_filters_union_to_selected_project(local_catalog) -> None:
    server, _, _, _ = local_catalog
    async with Client(server, mode="legacy") as first, Client(server, mode="legacy") as second:
        await first.call_tool("set_active_instance", {"instance": "A@a"})
        await second.call_tool("set_active_instance", {"instance": "B@b"})
        assert {tool.name for tool in await first.list_tools()} == {
            "run_tests",
            "set_active_instance",
        }
        assert {tool.name for tool in await second.list_tools()} == {
            "manage_scene",
            "scene_alias",
            "set_active_instance",
        }


@pytest.mark.asyncio
async def test_reregistered_project_replaces_its_contribution_to_real_inventory(
    local_catalog,
) -> None:
    server, hub, _, sockets = local_catalog
    await hub._handle_register_tools(
        sockets["B"], RegisterToolsMessage(tools=[ToolDefinitionModel(name="run_tests")])
    )
    assert await inventory(server) == {"run_tests", "set_active_instance"}
    await hub._handle_register_tools(
        sockets["A"], RegisterToolsMessage(tools=[ToolDefinitionModel(name="manage_scene")])
    )
    assert await inventory(server) == {
        "run_tests",
        "manage_scene",
        "scene_alias",
        "set_active_instance",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("removal", ["disconnect", "evict"])
async def test_removed_project_no_longer_contributes_to_real_inventory(
    local_catalog, removal: str
) -> None:
    server, hub, registry, sockets = local_catalog
    if removal == "disconnect":
        await hub.on_disconnect(sockets["B"], 1001)
    else:
        session_id = await registry.get_session_id_by_hash("b")
        await PluginHub._evict_connection(session_id, "test-removal")
    assert await inventory(server) == {"run_tests", "set_active_instance"}


@pytest.mark.asyncio
async def test_reconnected_project_preserves_other_project_inventory(local_catalog) -> None:
    server, hub, _, _ = local_catalog
    replacement = AsyncMock()
    replacement.state = SimpleNamespace()
    await hub._handle_register(replacement, RegisterMessage(project_name="B", project_hash="b"))
    assert await inventory(server) == {"run_tests", "set_active_instance"}
    await hub._handle_register_tools(
        replacement, RegisterToolsMessage(tools=[ToolDefinitionModel(name="manage_scene")])
    )
    assert await inventory(server) == {
        "run_tests",
        "manage_scene",
        "scene_alias",
        "set_active_instance",
    }


@pytest.mark.asyncio
async def test_last_disconnect_restores_startup_inventory(local_catalog) -> None:
    server, hub, _, sockets = local_catalog
    await hub.on_disconnect(sockets["B"], 1001)
    await hub.on_disconnect(sockets["A"], 1001)
    assert await inventory(server) == {
        "run_tests",
        "manage_scene",
        "scene_alias",
        "set_active_instance",
    }
