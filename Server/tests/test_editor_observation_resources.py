"""SDK resource reads retain the selected Editor's built-in catalog and read grants."""

import json
from urllib.parse import quote

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
import pytest

from core.config import config
from models.models import ToolDefinitionModel
from services.credential_lease import bind_credential_lease
from services.resources import register_all_resources
from services.tools import editor_observations, register_all_tools
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
from transport.unity_instance_middleware import UnityInstanceMiddleware


GAME_VIEW = "mcpforunity://editor/game-view-size"
GRAPH_PATH = "Assets/Shaders/Fixture #1.shadergraph"
GRAPH = "mcpforunity://shader/graph/" + quote(GRAPH_PATH, safe="")
GRAPH_TEMPLATE = "mcpforunity://shader/graph/{encoded_path}{?page_size,page_number}"
ACTION_OBSERVATIONS = "io.github.ykh09242.unity-mcp/action-observations"
OBSERVATION = "io.github.ykh09242.unity-mcp/observation"
READS = (
    (GAME_VIEW, "manage_editor", {"action": "get_game_view_size"}),
    (
        GRAPH + "?page_size=3&page_number=2",
        "manage_shader",
        {"action": "inspect_graph", "path": GRAPH_PATH, "pageSize": 3, "pageNumber": 2},
    ),
)
TARGET = {"unity_instance": "Fixture@hash"}
FOCUSED = {
    "manage_editor": (editor_observations.get_game_view_size.__name__, {}),
    "manage_shader": (
        editor_observations.inspect_shader_graph.__name__,
        {"path": GRAPH_PATH, "page_size": 3, "page_number": 2},
    ),
    "manage_ugui": ("inspect_ugui_hit_order", {"target": 7, "screen_position": [100, 80]}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_fixed_state_and_pointer_are_routed_reads(command_hub, monkeypatch, mode):
    from unittest.mock import AsyncMock
    import importlib
    from tests.test_ugui_raycast_diagnostics import sample
    from tests.http.test_doctor import install_native_replies, observations
    from services.operation_permissions import Permission, required_permissions

    ugui = importlib.import_module("services.tools.manage_ugui")
    await install_native_replies(command_hub, observations())
    preflight = AsyncMock(return_value=None)
    monkeypatch.setattr(ugui, "preflight", preflight)
    native = AsyncMock(return_value=sample())
    monkeypatch.setattr(ugui, "send_with_unity_instance", native)
    async with Client(observation_server(monkeypatch), mode=mode, cache=False) as client:
        for name in ("inspect_editor_state", "inspect_ugui_hit_order"):
            assert required_permissions(name, {}) == {Permission.READ}
        state = await client.call_tool("inspect_editor_state", TARGET)
        assert (
            state.structured_content["success"]
            and command_hub.sent[-1]["name"] == "get_editor_state"
        )
        args = {**FOCUSED["manage_ugui"][1], **TARGET}
        result = await client.call_tool("inspect_ugui_hit_order", args)
        assert result.structured_content["message"] == "strict_raycast_unavailable"
        reply = sample()
        reply["data"]["policy"]["strict_read"] = True
        native.return_value = reply
        result = await client.call_tool("inspect_ugui_hit_order", args)
        assert result.structured_content == reply
        assert native.call_args.args[1:3] == ("Fixture@hash", "manage_ugui")
        assert native.call_args.args[3]["strict_read"] is True
        assert native.call_args.args[3]["allow_custom_filters"] is False
        assert not preflight.call_args.kwargs.get("refresh_if_dirty", False)
        with pytest.raises(ToolError):
            await client.call_tool("inspect_ugui_hit_order", {**args, "allow_custom_filters": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_tools_only_client_discovers_source_and_invokes_the_read(
    command_hub, monkeypatch, mode
):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    server = FastMCP("tools-only")
    server.add_middleware(UnityInstanceMiddleware())
    register_all_tools(server)
    async with Client(server, mode=mode, cache=False) as client:
        tools = {item.name: item for item in await client.list_tools()}
        for _, name, expected in READS:
            action = expected["action"]
            parent = tools[name]
            assert action in parent.description
            description = (
                await client.call_tool(
                    "manage_tools",
                    {
                        "action": "describe",
                        "tool": name,
                        "operation": action,
                    },
                )
            ).structured_content
            card = description["operation"]
            observation = parent.meta[ACTION_OBSERVATIONS][action]
            assert card["observation"] == observation
            arguments = card["invoke"]["arguments"].copy()
            if action == "inspect_graph":
                assert "Unsaved" in parent.description
                assert observation["source"] == "asset_file"
                assert {"nodes", "nodeCount", "edgeCount"} <= set(observation["values"])
                assert "unsaved_graph_structure" in observation["unavailable"]
                assert "path" in card["required_parameters"]
                arguments.update(path=GRAPH_PATH, page_size=3, page_number=2)
            else:
                assert observation["source"] == "editor_live"
                assert {"render_size.width", "render_size.height", "current_size"} <= set(
                    observation["values"]
                )
            result = await client.call_tool(name, {**arguments, **TARGET})
            assert result.structured_content["success"]
            [sent] = command_hub.sent
            assert (sent["name"], sent["params"]) == (name, expected)
            command_hub.sent.clear()
            fixed, inputs = FOCUSED[name]
            tool = tools[fixed]
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.meta[OBSERVATION] == observation
            assert set(tool.input_schema.get("properties", {})) - {"unity_instance"} == set(inputs)
            assert tools[name].annotations.read_only_hint is not True
            fixed_description = (
                await client.call_tool("manage_tools", {"action": "describe", "tool": fixed})
            ).structured_content
            assert fixed_description["operation"]["observation"] == observation
            result = await client.call_tool(fixed, {**inputs, **TARGET})
            assert result.structured_content["success"]
            [sent] = command_hub.sent
            assert (sent["name"], sent["params"]) == (name, expected)
            command_hub.sent.clear()
            with pytest.raises(ToolError, match="unexpected_keyword_argument"):
                await client.call_tool(fixed, {**inputs, **TARGET, "action": "delete"})
            assert command_hub.sent == []
        shader = tools["manage_shader"].meta[ACTION_OBSERVATIONS]
        assert shader["read"]["source"] == "asset_file"
        assert shader["diagnostics"]["source"] == "imported_asset"
        assert all("unsaved_graph_structure" not in read["values"] for read in shader.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_resource_observation_matches_the_same_tool_and_catalog(
    command_hub, monkeypatch, mode
):
    async with Client(observation_server(monkeypatch), mode=mode, cache=False) as client:
        tools = {item.name: item for item in await client.list_tools()}
        resources = {str(item.uri): item for item in await client.list_resources()}
        templates = {
            str(item.uri_template): item for item in await client.list_resource_templates()
        }
        for uri, resource, group, parent, action in [
            (GAME_VIEW, resources[GAME_VIEW], "resources", "manage_editor", "get_game_view_size"),
            (
                GRAPH_TEMPLATE,
                templates[GRAPH_TEMPLATE],
                "resource_templates",
                "manage_shader",
                "inspect_graph",
            ),
        ]:
            assert uri in tools[parent].description
            assert action in resource.description
            observation = resource.meta[OBSERVATION]
            assert tools[parent].meta[ACTION_OBSERVATIONS][action] == observation
            page = (
                await client.call_tool(
                    "manage_tools",
                    {
                        "action": "catalog",
                        "group": group,
                        "limit": 20,
                    },
                )
            ).structured_content
            assert (
                next(row for row in page[group] if row["name"] == resource.name)["observation"]
                == observation
            )
            found = (
                await client.call_tool(
                    "manage_tools",
                    {
                        "action": "search",
                        "query": uri,
                    },
                )
            ).structured_content["routes"][0]
            assert found["observation"] == observation
        assert command_hub.sent == []


def observation_server(monkeypatch, profile="full"):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", profile)
    server = FastMCP("editor-observations")
    server.add_middleware(UnityInstanceMiddleware())
    register_all_tools(server)
    register_all_resources(server)
    return server


@pytest.mark.asyncio
async def test_selected_observation_reads_and_invalid_pages(command_hub, monkeypatch):
    server = observation_server(monkeypatch)
    async with Client(server, cache=False) as client:
        assert GAME_VIEW in {str(item.uri) for item in await client.list_resources()}
        assert "mcpforunity://shader/graph/{encoded_path}{?page_size,page_number}" in {
            str(item.uri_template) for item in await client.list_resource_templates()
        }
        for uri, name, expected in READS:
            response = json.loads((await client.read_resource(uri, meta=TARGET))[0].text)
            assert response["success"], response
            [sent] = command_hub.sent
            assert (sent["name"], sent["params"]) == (name, expected)
            command_hub.sent.clear()

        for query in ("page_size=0", "page_size=101", "page_number=2147483648"):
            response = json.loads(
                (await client.read_resource(GRAPH + "?" + query, meta=TARGET))[0].text
            )
            assert not response["success"]
        with pytest.raises(MCPError):
            await client.read_resource(GAME_VIEW, meta={"unity_instance": "Missing@absent"})
        assert command_hub.sent == []

        # Unknown URI fields can only be rejected or ignored by the SDK.
        # They must never select another action or reach the native payload.
        for uri, name, expected in READS:
            separator = "&" if "?" in uri else "?"
            try:
                await client.read_resource(
                    uri + separator + "action=delete&contents=abc", meta=TARGET
                )
            except MCPError:
                assert command_hub.sent == []
            else:
                [sent] = command_hub.sent
                assert (sent["name"], sent["params"]) == (name, expected)
                command_hub.sent.clear()


@pytest.mark.asyncio
async def test_no_dispatch_when_parent_tool_unselected(command_hub, monkeypatch):
    async with Client(observation_server(monkeypatch, profile="minimal"), cache=False) as client:
        for uri, _, _ in READS:
            with pytest.raises(MCPError):
                await client.read_resource(uri, meta=TARGET)
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_deactivating_one_parent_also_hides_and_denies_its_fixed_read(
    command_hub, monkeypatch
):
    async with Client(observation_server(monkeypatch), mode="legacy", cache=False) as client:
        for parent, (fixed, inputs) in FOCUSED.items():
            assert fixed in {item.name for item in await client.list_tools()}
            await client.call_tool("manage_tools", {"action": "deactivate", "tool": parent})
            await client.call_tool("manage_tools", {"action": "activate", "tool": fixed})
            assert fixed not in {item.name for item in await client.list_tools()}
            description = (
                await client.call_tool("manage_tools", {"action": "describe", "tool": fixed})
            ).structured_content
            assert description["catalog"]["advertised"] is False
            result = await client.call_tool(fixed, {**inputs, **TARGET})
            assert result.structured_content["success"] is False
        assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("request_read,plugin_read", [(True, True), (False, True), (True, False)])
async def test_hosted_reads_require_both_grants_and_selected_catalog(
    command_hub, monkeypatch, request_read, plugin_read
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        "request": {"unity_mcp_permissions": ["read"] if request_read else ["edit"]},
        "plugin": {"unity_mcp_permissions": ["read"] if plugin_read else ["run"]},
    }
    server = observation_server(monkeypatch)
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        with bind_credential_lease(requester):
            async with Client(server, cache=False) as client:
                for uri, name, expected in READS:
                    fixed, inputs = FOCUSED[name]
                    # A peer advertising only the projection cannot stand in for the native parent.
                    await command_hub.registry.register_tools_for_session(
                        "session", [ToolDefinitionModel(name=fixed)]
                    )
                    with pytest.raises(MCPError):
                        await client.read_resource(uri, meta=TARGET)
                    with pytest.raises(MCPError):
                        await client.call_tool(fixed, {**inputs, **TARGET})
                    assert command_hub.sent == []
                    await command_hub.registry.register_tools_for_session(
                        "session", [ToolDefinitionModel(name=name)]
                    )
                    if request_read and plugin_read:
                        response = json.loads(
                            (await client.read_resource(uri, meta=TARGET))[0].text
                        )
                        assert response["success"], response
                        [sent] = command_hub.sent
                        assert (sent["name"], sent["params"]) == (name, expected)
                        command_hub.sent.clear()
                        result = await client.call_tool(fixed, {**inputs, **TARGET})
                        assert result.structured_content["success"]
                        [sent] = command_hub.sent
                        assert (sent["name"], sent["params"]) == (name, expected)
                        command_hub.sent.clear()
                        await command_hub.registry.register_tools_for_session("session", [])
                        with pytest.raises(MCPError):
                            await client.call_tool(fixed, {**inputs, **TARGET})
                        assert command_hub.sent == []
                    else:
                        with pytest.raises(MCPError):
                            await client.read_resource(uri, meta=TARGET)
                        with pytest.raises(MCPError):
                            await client.call_tool(fixed, {**inputs, **TARGET})
                        assert command_hub.sent == []

                if request_read and plugin_read:
                    await command_hub.registry.register_tools_for_session(
                        "session", [ToolDefinitionModel(name=parent) for parent in FOCUSED]
                    )
                    claims["plugin"] = {"unity_mcp_permissions": []}
                    await fixture.service.invalidate_cache("plugin")
                    renewed = (await fixture.service.validate("plugin")).lease
                    setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, renewed)
                    for fixed, inputs in FOCUSED.values():
                        with pytest.raises(MCPError):
                            await client.call_tool(fixed, {**inputs, **TARGET})
                    assert command_hub.sent == []
