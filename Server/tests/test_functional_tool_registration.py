"""Feature registration must tolerate prior SDK Context injection on source tools."""

from tests.test_tool_group_status import run_sdk_regression


def test_features_register_once_after_sources_have_been_registered_with_sdk():
    run_sdk_regression("""
        import asyncio
        import sys
        sys.path.insert(0, "src")
        from tests.test_functional_tool_registration import _exercise_prior_registration
        asyncio.run(_exercise_prior_registration())
    """)


async def _exercise_prior_registration():
    import inspect
    import os
    import sys
    from importlib import import_module
    from unittest.mock import AsyncMock

    from fastmcp import Client, Context, FastMCP
    from core.config import config
    from core.sdk_compatibility import REVIEWED_SDK, sdk_versions
    from services.registry import get_registered_tools
    from services.registry.capability_catalog import CAPABILITIES, LEGACY_TOOLS
    from services.tools import register_all_tools

    assert sdk_versions() == REVIEWED_SDK
    # With an empty HTTP plugin registry, admission cannot discover a local Editor.
    config.transport_mode = "http"
    config.http_remote_hosted = False
    os.environ["UNITY_MCP_TOOL_PROFILE"] = "full"
    parents = {"manage_editor", "manage_shader", "manage_ugui"}
    original = FastMCP("original-first")
    for name in sorted(parents | LEGACY_TOOLS):
        module = import_module("services.tools." + name)
        handler = getattr(module, name)
        original.tool(handler)
        if name in parents:
            # Exercise the SDK signature that the nested admission wrapper captures.
            parameters = list(inspect.signature(handler).parameters)
            assert parameters.index("action") < parameters.index("ctx"), name

    assert "services.tools.editor_observations" not in sys.modules
    assert "services.tools.inspect_open_shader_graph" not in sys.modules
    derived = FastMCP("features-after-injection")
    register_all_tools(derived, project_scoped_tools=False)
    declared = [entry["name"] for entry in get_registered_tools()]
    assert len(declared) == len(set(declared)), declared
    assert {item.name for item in CAPABILITIES} <= set(declared)

    async def fixture_instance(ctx):
        assert (
            isinstance(ctx, Context) and ctx.fastmcp is derived and ctx.request_context is not None
        )
        return "Fixture@hash"

    from tests.test_ugui_raycast_diagnostics import sample

    raycast = sample()
    raycast["data"]["policy"]["strict_read"] = True
    send = AsyncMock(return_value={"success": True})
    pointer_send = AsyncMock(return_value=raycast)
    for name in parents | {"manage_gameobject"}:
        module = import_module("services.tools." + name)
        module.get_unity_instance_from_context = fixture_instance
        module.send_with_unity_instance = pointer_send if name == "manage_ugui" else send
        if name in {"manage_gameobject", "manage_ugui"}:
            module.preflight = AsyncMock(return_value=None)

    path = "Assets/Fixture.shadergraph"
    cases = (
        (
            "gameobject_create",
            {"action": "create", "name": "Fixture"},
            "gameobject_create",
            "create",
        ),
        ("get_game_view_size", {}, "manage_editor", "get_game_view_size"),
        ("inspect_shader_graph", {"path": path}, "manage_shader", "inspect_graph"),
        ("inspect_open_shader_graph", {"path": path}, "manage_shader", "inspect_open_graph"),
        (
            "inspect_ugui_hit_order",
            {"target": 7, "screen_position": [100, 80]},
            "manage_ugui",
            "diagnose_raycast",
        ),
    )
    for mode in ("legacy", "2026-07-28"):
        async with Client(derived, mode=mode, cache=False) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert tools["gameobject_create"].input_schema["required"] == ["action", "name"]
            for name, arguments, command, action in cases:
                assert "ctx" not in tools[name].input_schema["properties"]
                boundary = pointer_send if command == "manage_ugui" else send
                boundary.reset_mock()
                result = await client.call_tool(name, arguments)
                assert result.structured_content["success"], (name, result)
                boundary.assert_awaited_once()
                _, instance, actual_command, payload = boundary.await_args.args
                assert (instance, actual_command, payload["action"]) == (
                    "Fixture@hash",
                    command,
                    action,
                )
                assert "ctx" not in payload


def test_real_catalog_advertises_strict_routing_with_action_contracts():
    run_sdk_regression("""
        import asyncio, os
        from fastmcp import Client, FastMCP
        from jsonschema import Draft202012Validator
        from services.tools import register_all_tools

        async def scenario():
            os.environ["UNITY_MCP_TOOL_PROFILE"] = "full"
            server = FastMCP("routing-catalog")
            register_all_tools(server)
            async with Client(server) as client:
                catalog = {tool.name: tool for tool in await client.list_tools()}
                assert len(catalog) >= 100
                for name, tool in catalog.items():
                    assert ("unity_instance" in tool.input_schema.get("properties", {})) == (name != "get_execution_receipt"), name
                for name in ("refresh_unity", "run_tests", "get_test_job", "read_console"):
                    schema = catalog[name].output_schema
                    assert not schema.get("x-fastmcp-wrap-result"), (name, schema)
                    assert "result" not in schema.get("properties", {}), (name, schema)
                asset_schema = catalog["asset_edit"].input_schema
                assert "allOf" not in asset_schema
                assert all(branch["type"] == "object" for branch in asset_schema["oneOf"])
                validator = Draft202012Validator(catalog["read_console"].input_schema)
                assert not list(validator.iter_errors({"action": "get", "unity_instance": "Project@hash"}))
                assert list(validator.iter_errors({"action": "get", "unexpected": 1}))
                assert list(validator.iter_errors({"action": "not-an-action", "unity_instance": "Project@hash"}))
                assert list(validator.iter_errors({"action": "get", "unity_instance": True}))
        asyncio.run(scenario())
    """)
