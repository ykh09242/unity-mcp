"""Public SDK contracts for focused capability tools and compatibility endpoints."""

from tests.test_tool_group_status import run_sdk_regression


def test_capabilities_have_narrow_schemas_and_individual_session_visibility():
    run_sdk_regression("""
        import asyncio, sys
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        from services.tools import register_all_tools

        async def scenario():
            for transport in ("http", "stdio"):
                config.transport_mode = transport
                config.http_remote_hosted = False
                server = FastMCP("capability-contract")
                register_all_tools(server, project_scoped_tools=False)
                async with Client(server, mode="legacy") as first, Client(server, mode="legacy") as second:
                    initial = {tool.name for tool in await first.list_tools()}
                    assert {"gameobject_create", "scene_inspect", "asset_inspect"} <= initial, initial
                    assert not {"manage_physics", "manage_scene", "query_physics", "manage_physics_joints"} & initial
                    activated = (await first.call_tool("manage_tools", {"action": "activate", "tool": "query_physics"})).structured_content
                    assert activated["activated_tool"] == "query_physics", activated
                    visible = {tool.name: tool for tool in await first.list_tools()}
                    assert "manage_physics_joints" not in visible and "manage_physics" not in visible
                    schema = visible["query_physics"].inputSchema
                    assert "force" not in schema["properties"] and "joint_type" not in schema["properties"]
                    assert {"raycast", "raycast_all", "linecast", "shapecast", "overlap"} == set(schema["properties"]["action"]["enum"])
                    assert "query_physics" not in {tool.name for tool in await second.list_tools()}
                    for arguments in ({"action": "apply_force"}, {"action": "raycast", "force": [0, 1, 0]}):
                        result = await first.call_tool("query_physics", arguments, raise_on_error=False)
                        assert result.is_error, result
                    await first.call_tool("manage_tools", {"action": "reset"})
                    assert {tool.name for tool in await first.list_tools()} == initial
                    await first.call_tool("manage_tools", {"action": "activate", "group": "legacy"})
                    legacy = {tool.name: tool for tool in await first.list_tools()}
                    assert "force" in legacy["manage_physics"].inputSchema["properties"]
                    assert "preview_token" in legacy["manage_gameobject"].inputSchema["properties"]
        asyncio.run(scenario())
    """)
