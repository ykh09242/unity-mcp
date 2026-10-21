"""Advertised action rules and stable response variants through the actual SDK."""

import pytest
from tests.test_tool_group_status import run_sdk_regression


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_action_schemas_preserve_valid_requests_and_refuse_invalid_before_dispatch(mode):
    run_sdk_regression(
        """
        import asyncio
        from importlib import import_module
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from jsonschema import Draft202012Validator
        from core.config import config
        from services.registry import TOOL_GROUPS
        from services.tools import register_all_tools
        from transport.response_limit_middleware import ResponseLimitMiddleware
        asset = import_module("services.tools.manage_asset")
        prefab = import_module("services.tools.manage_prefabs")
        for module in (asset, prefab):
            module.preflight = AsyncMock(return_value=None)
            module.get_unity_instance_from_context = AsyncMock(return_value="Fixture@hash")
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"path": "Assets/item.mat"}})

        async def scenario():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            server = FastMCP("action-contracts")
            server.add_middleware(ResponseLimitMiddleware())
            register_all_tools(server, project_scoped_tools=False)
            for group in TOOL_GROUPS:
                server.enable(tags={f"group:{group}"}, components={"tool"})
            async with Client(server, mode=MODE) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                for name in ("asset_edit", "prefab_contents", "asset_bundles"):
                    schema = tools[name].input_schema
                    assert schema["allOf"], (name, schema)
                    validator = Draft202012Validator(schema)
                    for example in schema["examples"]:
                        validator.validate(example)
                    assert "Action inputs:" in tools[name].description
                invalid = [
                    ("asset_edit", {"action": "import"}),
                    ("asset_edit", {"action": "move", "path": "Assets/a.mat"}),
                    ("asset_edit", {"action": "create", "path": "Assets/a.mat"}),
                    ("asset_edit", {"action": "modify", "path": "Assets/a.mat"}),
                    ("prefab_contents", {"action": "apply_preview", "preview_id": "plan", "position": [1,2,3]}),
                    ("prefab_contents", {"action": "modify_contents", "prefab_path": "Assets/a.prefab", "preview_id": "plan"}),
                    ("prefab_contents", {"action": "preview_modify_contents", "prefab_path": "Assets/a.prefab", "target": "Child"}),
                    ("asset_bundles", {"action": "get_bundle_assets"}),
                ]
                for name, arguments in invalid:
                    assert list(Draft202012Validator(tools[name].input_schema).iter_errors(arguments)), (name, arguments)
                    result = await client.call_tool(name, arguments, raise_on_error=False)
                    assert result.is_error, (name, result)
                for module in (asset, prefab):
                    module.preflight.assert_not_awaited()
                    module.send_with_unity_instance.assert_not_awaited()
                for arguments in (
                    {"action": "duplicate", "path": "Assets/a.mat"},
                    {"action": "modify", "path": "Assets/a.mat", "properties": '{"name":"New"}'},
                    {"action": "create", "path": "Assets/New", "asset_type": "Folder"},
                    {"action": "rename", "path": "Assets/a.mat", "destination": "Assets/b.mat"},
                ):
                    Draft202012Validator(tools["asset_edit"].input_schema).validate(arguments)
                    result = await client.call_tool("asset_edit", arguments)
                    assert not result.is_error, result
                assert asset.send_with_unity_instance.await_count == 4
        asyncio.run(scenario())
    """.replace("MODE", repr(mode))
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_bundle_result_variants_keep_native_wire_fields_and_machine_errors(mode):
    run_sdk_regression(
        """
        import asyncio
        from importlib import import_module
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from jsonschema import Draft202012Validator
        from core.config import config
        from services.tools import register_all_tools
        from transport.response_limit_middleware import ResponseLimitMiddleware
        asset = import_module("services.tools.manage_asset")
        asset.get_unity_instance_from_context = AsyncMock(return_value="Fixture@hash")
        send = asset.send_with_unity_instance = AsyncMock()

        async def scenario():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            server = FastMCP("result-contracts")
            server.add_middleware(ResponseLimitMiddleware())
            register_all_tools(server, project_scoped_tools=False)
            server.enable(tags={"group:assets"}, components={"tool"})
            async with Client(server, mode=MODE) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                schema = tools["asset_bundles"].output_schema
                assert {variant["properties"]["success"]["const"] for variant in schema["oneOf"]} == {True, False}
                validator = Draft202012Validator(schema)
                success = {"success": True, "message": "Observed", "data": {
                    "source": "AssetDatabase assignments (not built bundle contents)",
                    "bundleName": None, "recursive": False, "entries": ["a", "b"],
                    "totalEntries": 2000000, "pageSize": 2, "pageNumber": 1,
                    "maxPageSize": 100, "hasMore": True, "futureNativeField": "retained"}}
                failure = {"success": False, "code": 0, "error": "Not available", "data": {"reason": "unknown"}}
                for expected in (success, failure):
                    validator.validate(expected)
                    send.return_value = expected
                    result = await client.call_tool("asset_bundles", {"action": "list_asset_bundles"}, raise_on_error=False)
                    assert result.structured_content == expected, result
                    assert result.is_error is (expected["success"] is False), result
                for malformed in ({"success": True, "data": {}}, {"success": 1, "data": success["data"]}):
                    assert not validator.is_valid(malformed)
                    send.return_value = malformed
                    result = await client.call_tool("asset_bundles", {"action": "list_asset_bundles"}, raise_on_error=False)
                    assert result.is_error, result
                assert send.await_count == 4
        asyncio.run(scenario())
    """.replace("MODE", repr(mode))
    )
