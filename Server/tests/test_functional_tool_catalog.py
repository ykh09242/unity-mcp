"""Public SDK contracts for focused capability tools and compatibility endpoints."""

import pytest

from tests.test_tool_group_status import run_sdk_regression


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_distributed_core_examples_validate_against_real_default_catalog(mode):
    run_sdk_regression(
        r"""
        import asyncio, json, re, sys
        from pathlib import Path
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from jsonschema import Draft202012Validator
        from core.config import config
        from services.registry import get_registered_tools
        from services.tools import register_all_tools
        from transport.unity_instance_middleware import UnityInstanceMiddleware

        async def scenario():
            # Given: both distributed skill copies and the actual untouched core catalog.
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            server = FastMCP("default-example-contract")
            server.add_middleware(UnityInstanceMiddleware())
            register_all_tools(server, project_scoped_tools=False)
            async with Client(server, mode=MODE, cache=False) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                assert not {"manage_scene", "manage_gameobject", "manage_ui"} & tools.keys()
                optional = []
                for skill in ("unity-mcp-skill", ".claude/skills/unity-mcp-skill"):
                    checked = 0
                    for relative in ("connection.md", "workflows.md", "tools-reference.md"):
                        text = (Path("..") / skill / "references" / relative).read_text(encoding="utf-8")
                        for section in re.findall(r"<!-- default-core:start -->(.*?)<!-- default-core:end -->", text, re.S):
                            for body in re.findall(r"```json\s*\n(.*?)\n```", section, re.S):
                                # When: a recipe is resolved using only the default public schema.
                                example = json.loads(body)
                                assert example["tool"] in tools, example
                                schema = tools[example["tool"]].input_schema
                                # Then: the advertised tool and every sample argument are valid.
                                Draft202012Validator(schema).validate(example["params"])
                                checked += 1
                        for group, section in re.findall(r"<!-- optional-group:([a-z_]+):start -->(.*?)<!-- optional-group:end -->", text, re.S):
                            for body in re.findall(r"```json\s*\n(.*?)\n```", section, re.S):
                                optional.append((group, json.loads(body)))
                    assert checked >= 6, (skill, checked)
                assert {group for group, _ in optional} == {"ui", "camera"}
                groups = {item["name"]: item["group"] for item in get_registered_tools()}
                for group, example in optional:
                    # Explicit optional exemptions must name the actual group, and
                    # their examples must match the real exposed schema after enabling it.
                    name = example["tool"]
                    assert name not in tools, example
                    assert groups[name] == group, (group, example)
                    server.enable(tags={f"group:{group}"}, components={"tool"})
                    expanded = {tool.name: tool for tool in await client.list_tools()}
                    Draft202012Validator(expanded[name].input_schema).validate(example["params"])
        asyncio.run(scenario())
        """.replace("MODE", repr(mode))
    )


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
