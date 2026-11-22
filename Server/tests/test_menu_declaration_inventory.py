"""The tools-only host reads the same managed declarations under the resource's policies."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import AuthorizationError, ToolError
from fastmcp.server.middleware import Middleware

from services.registry.integration_action_guidance import MENU_INVENTORY_OBSERVATION
from services.registry.observation_predicates import evaluate_observation
from tests.http.test_project_capabilities import install_native_replies
from tests.test_camera_inventory_tool import SELECT, inventory_server
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import

TOOL = "list_menu_declarations"
URI = "mcpforunity://menu-items"
PATHS = ["Tools/Fixture/Registered Command"]


@pytest.mark.parametrize(
    "success, data, observed",
    [(True, PATHS, True), (True, [], True), (False, PATHS, False), (True, {}, False)],
)
def test_inventory_observation_advertises_only_declared_paths(success, data, observed):
    fields = ["menu.declared_paths", "menu.enabled"]
    assert evaluate_observation(
        MENU_INVENTORY_OBSERVATION, {"success": success, "data": data}, fields
    ) == {"menu.declared_paths": observed, "menu.enabled": False}


@pytest.mark.asyncio
async def test_menu_inventory_is_equal_on_both_surfaces_and_never_invokes(command_hub, monkeypatch):
    await install_native_replies(command_hub, {"get_menu_items": PATHS})
    async with Client(inventory_server(monkeypatch), mode="legacy", cache=False) as client:
        listed = {tool.name: tool for tool in await client.list_tools()}
        assert TOOL in listed
        assert listed[TOOL].annotations.read_only_hint
        assert set(listed[TOOL].input_schema["properties"]) == {"unity_instance"}
        for surface, key, identifier in [("tools", "tool", TOOL), ("resources", "uri", URI)]:
            query = {"route_surfaces": surface, "required_values": ["menu.declared_paths"]}
            result = (await client.call_tool("search_unity_tools", query)).structured_content
            (group,) = result["route_groups"]
            assert group["representative"][key] == identifier and not group["alternatives"]
        assert not command_hub.sent
        fixed = (await client.call_tool(TOOL, SELECT)).structured_content
        direct = json.loads((await client.read_resource(URI, meta=SELECT))[0].text)
        assert fixed == direct and fixed["data"] == PATHS
        with pytest.raises(ToolError):
            await client.call_tool(TOOL, {**SELECT, "menu_path": PATHS[0]})
        assert [(sent["name"], sent["params"]) for sent in command_hub.sent] == [
            ("get_menu_items", {"refresh": False, "search": ""}),
            ("get_menu_items", {"refresh": False, "search": ""}),
        ]


@pytest.mark.asyncio
async def test_menu_reader_preserves_resource_read_policy(command_hub, monkeypatch):
    class DenyMenus(Middleware):
        async def check_resource_access(self, context):
            if str(context.message.uri) == URI:
                raise AuthorizationError("Menu declarations hidden")

    server = inventory_server(monkeypatch)
    server.add_middleware(DenyMenus())
    async with Client(server, mode="legacy", cache=False) as client:
        assert TOOL not in {tool.name for tool in await client.list_tools()}
        with pytest.raises(ToolError):
            await client.call_tool(TOOL, SELECT)
    assert not command_hub.sent
