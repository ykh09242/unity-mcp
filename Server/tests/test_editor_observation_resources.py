"""SDK resource reads retain the selected Editor's built-in catalog and read grants."""

import json
from urllib.parse import quote

from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError
import pytest

from core.config import config
from models.models import ToolDefinitionModel
from services.credential_lease import bind_credential_lease
from services.resources import register_all_resources
from services.tools import register_all_tools
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
from transport.unity_instance_middleware import UnityInstanceMiddleware


GAME_VIEW = "mcpforunity://editor/game-view-size"
GRAPH_PATH = "Assets/Shaders/Fixture #1.shadergraph"
GRAPH = "mcpforunity://shader/graph/" + quote(GRAPH_PATH, safe="")
READS = (
    (GAME_VIEW, "manage_editor", {"action": "get_game_view_size"}),
    (
        GRAPH + "?page_size=3&page_number=2",
        "manage_shader",
        {"action": "inspect_graph", "path": GRAPH_PATH, "pageSize": 3, "pageNumber": 2},
    ),
)
TARGET = {"unity_instance": "Fixture@hash"}


@pytest.mark.asyncio
async def test_mixed_tool_descriptions_point_to_advertised_read_surfaces(monkeypatch):
    from services.registry import get_registered_tools
    from services.registry.action_catalog import action_card

    server = observation_server(monkeypatch)
    async with Client(server) as client:
        tools = {item.name: item for item in await client.list_tools()}
        resources = {item.name: str(item.uri) for item in await client.list_resources()}
        resources.update(
            {item.name: str(item.uri_template) for item in await client.list_resource_templates()}
        )
        declarations = {entry["name"]: entry for entry in get_registered_tools()}
        for parent, action, resource in [
            ("manage_editor", "get_game_view_size", "game_view_size"),
            ("manage_shader", "inspect_graph", "shader_graph_structure"),
        ]:
            assert resources[resource] in tools[parent].description
            assert resources[resource] in action_card(declarations[parent], action)["purpose"]


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
                    await command_hub.registry.register_tools_for_session("session", [])
                    with pytest.raises(MCPError):
                        await client.read_resource(uri, meta=TARGET)
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
                    else:
                        with pytest.raises(MCPError):
                            await client.read_resource(uri, meta=TARGET)
                        assert command_hub.sent == []
