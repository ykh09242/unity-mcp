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
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_menu_inventory_is_equal_on_both_surfaces_and_never_invokes(
    command_hub, monkeypatch, mode
):
    await install_native_replies(command_hub, {"get_menu_items": PATHS})
    async with Client(inventory_server(monkeypatch), mode=mode, cache=False) as client:
        listed = {tool.name: tool for tool in await client.list_tools()}
        assert TOOL in listed
        assert listed[TOOL].annotations.read_only_hint
        assert set(listed[TOOL].input_schema["properties"]) == {"unity_instance"}
        for surface, key, identifier in [("tools", "tool", TOOL), ("resources", "uri", URI)]:
            query = {"route_surfaces": surface, "required_values": ["menu.declared_paths"]}
            result = (await client.call_tool("search_unity_tools", query)).structured_content
            (group,) = result["route_groups"]
            assert group["representative"][key] == identifier and not group["alternatives"]
            if surface == "resources":
                assert group["representative"]["preferred_read_tool"] == {
                    "tool": "read_unity_resource",
                    "arguments": {"uri": URI},
                }

        raw = f"Execute this exact Unity editor menu item: {PATHS[0]}"
        result = (
            await client.call_tool(
                "search_unity_tools",
                {"query": raw, "route_surfaces": "tools", "limit": 3},
                meta=SELECT,
            )
        ).structured_content
        assert result["query"] == result["matched_query"] == raw
        route = next(
            item
            for item in result["routes"]
            if (item.get("tool"), item.get("operation")) == ("execute_menu_item", "execute")
        )
        assert "menu_path" not in route.get("arguments", {})
        assert route["readiness"] == "unobserved"
        assert not command_hub.sent

        fixed = (await client.call_tool(TOOL, SELECT)).structured_content
        direct = json.loads((await client.read_resource(URI, meta=SELECT))[0].text)
        assert fixed == direct and fixed["data"] == PATHS
        for name, args in (
            ("read_unity_resource", {"uri": URI}),
            ("manage_tools", {"action": "read_resource", "uri": URI}),
        ):
            generic = (await client.call_tool(name, {**args, **SELECT})).structured_content
            assert json.loads(generic["contents"][0]["text"]) == fixed
        with pytest.raises(ToolError):
            await client.call_tool(TOOL, {**SELECT, "menu_path": PATHS[0]})
        assert [(sent["name"], sent["params"]) for sent in command_hub.sent] == [
            ("get_menu_items", {"refresh": False, "search": ""}),
        ] * 4


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
        result = (
            await client.call_tool("search_unity_tools", {"query": "메뉴 경로는 어디야?"})
        ).structured_content
        assert TOOL not in [entry["name"] for entry in result["results"]]
        assert all(
            binding.get("tool") != TOOL and binding.get("uri") != URI
            for group in result["route_groups"]
            for binding in (group["representative"], *group["alternatives"])
        )
        with pytest.raises(ToolError):
            await client.call_tool(TOOL, SELECT)
    assert not command_hub.sent
