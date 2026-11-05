"""Custom payload, discovery, diagnostics and component action contract regressions."""

import pytest

from tests.sdk_scenarios import run_sdk_regression, sdk_scenario_host  # noqa: F401


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_open_custom_payload_context_is_separate_from_injected_context(mode):
    run_sdk_regression(
        """
        import asyncio
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as custom
        async def scenario():
            app = FastMCP("context-payload")
            service = custom.CustomToolService(app)
            custom.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            custom.get_user_id_from_context = AsyncMock(return_value=None)
            custom.resolve_project_id_for_unity_instance = lambda _: "shared"
            send = custom.send_with_unity_instance = AsyncMock(return_value={"success": True})
            for closed in (False, True):
                service.register_global_tools([ToolDefinitionModel(name="closed" if closed else "open",
                    schema_version=1, input_schema={"type": "object", "properties": {
                        "value": {"type": "integer", "default": 3}}, "additionalProperties": not closed})])
            payload = {"ctx": "payload-field", "other": None}
            direct = await service.execute_tool("shared", "open", "A@a", payload)
            assert direct.success and send.call_args.args[3] == {**payload, "value": 3}
            async with Client(app, mode=MODE) as client:
                result = await client.call_tool("open", payload, raise_on_error=False)
                assert not result.is_error, result
                assert send.call_args.args[3] == {**payload, "value": 3}
                send.reset_mock()
                rejected = await client.call_tool("closed", payload, raise_on_error=False)
                assert rejected.is_error
                send.assert_not_awaited()
            reserved = await service.execute_tool("shared", "open", "A@a", {"unity_instance": "B@b"})
            assert not reserved.success
            send.assert_not_awaited()
        asyncio.run(scenario())
        """.replace("MODE", repr(mode))
    )


@pytest.mark.parametrize("hosted", [False, True])
def test_public_custom_listing_filters_canonical_names_but_retains_catalog_evidence(hosted):
    run_sdk_regression(
        """
        import asyncio
        from unittest.mock import AsyncMock
        from fastmcp import FastMCP
        from core.config import config
        from models.models import ToolDefinitionModel
        import services.custom_tool_service as custom
        async def scenario():
            config.http_remote_hosted = HOSTED
            app = FastMCP("custom-discovery")
            service = custom.CustomToolService(app)
            custom.get_registered_tools = lambda: [{"name": "manage_scene", "unity_target": "native_alias"}]
            rows = [ToolDefinitionModel(name=name) for name in (
                "manage_scene", "MANAGE_SCENE", " native_alias ", "tail_console", "batch_validate", "real_custom")]
            custom.PluginHub.get_tools_for_project = AsyncMock(return_value=rows)
            if not HOSTED:
                service._register_project_tools("project", rows)
            listed = await service.list_registered_tools("project", user_id="user")
            assert [tool.name for tool in listed] == ["real_custom"], listed
            if HOSTED:
                assert await service.list_registered_tools("project") == []
            else:
                assert set(service._project_tools["project"]) == {row.name for row in rows}
                assert (await service.get_tool_definition("project", "manage_scene")).name == "manage_scene"
        asyncio.run(scenario())
        """.replace("HOSTED", repr(hosted))
    )


def test_custom_validation_diagnostics_are_distinguishable_bounded_and_value_free():
    run_sdk_regression(
        """
        import asyncio, json
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from models.models import ToolDefinitionModel, ToolParameterModel
        import services.custom_tool_service as custom
        async def scenario():
            app = FastMCP("safe-diagnostics")
            service = custom.CustomToolService(app)
            custom.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            custom.get_user_id_from_context = AsyncMock(return_value=None)
            custom.resolve_project_id_for_unity_instance = lambda _: "shared"
            send = custom.send_with_unity_instance = AsyncMock()
            rich = ToolDefinitionModel(name="rich", schema_version=1, input_schema={"type": "object",
                "properties": {"count": {"type": "integer", "minimum": 1}, "node": {"type": "object",
                    "properties": {"count": {"type": "integer"}}, "required": ["count"]}}, "required": ["count"]})
            scalar = ToolDefinitionModel(name="scalar", parameters=[ToolParameterModel(name="count", type="integer")])
            service.register_global_tools([rich, scalar])
            missing = ToolDefinitionModel(name="missing", schema_version=1, input_schema={"type": "object",
                "properties": {key: {"type": "integer"} for key in ("first", "second", "third")},
                "required": ["first", "second", "third"]})
            service.register_global_tools([missing])
            result = await service.execute_tool("shared", "missing", "A@a", {})
            assert result.data["validation_errors"] == [
                {"path": "/" + key, "category": "required"} for key in ("first", "second", "third")], result
            assert result.data["truncated"] is False
            cases = [("rich", {}, "/count", "required"), ("rich", {"count": "secret-value"}, "/count", "type"),
                ("rich", {"count": 0}, "/count", "minimum"),
                ("rich", {"count": 1, "node": {"count": "secret-value"}}, "/node/count", "type"),
                ("scalar", {}, "/count", "required"), ("scalar", {"count": "secret-value"}, "/count", "type")]
            for name, payload, path, category in cases:
                result = await service.execute_tool("shared", name, "A@a", payload)
                assert not result.success and result.code == "invalid_custom_tool_parameters", result
                assert {"path": path, "category": category} in result.data["validation_errors"], result
                assert "secret-value" not in result.model_dump_json()
            # Dynamic dictionary keys must not disclose submitted data either.
            service.register_global_tools([ToolDefinitionModel(name="dynamic", schema_version=1,
                input_schema={"type": "object", "additionalProperties": {"type": "integer"}})])
            many = {"secret-key-" + str(i): "secret-value" for i in range(1000)}
            result = await service.execute_tool("shared", "dynamic", "A@a", many)
            assert len(result.data["validation_errors"]) <= 16 and result.data["truncated"]
            assert len(result.model_dump_json()) < 8192
            assert "secret-key" not in result.model_dump_json() and "secret-value" not in result.model_dump_json()
            async with Client(app) as client:
                for name in ("rich", "scalar"):
                    rejected = await client.call_tool(name, {"count": "secret-value"}, raise_on_error=False)
                    assert rejected.is_error
                    message = " ".join(block.text for block in rejected.content)
                    assert "invalid_custom_tool_parameters" in message and "/count" in message and "type" in message, message
                    assert "secret-value" not in message
            send.assert_not_awaited()
        asyncio.run(scenario())
        """
    )


def test_production_http_custom_calls_and_counts_follow_the_same_contract():
    run_sdk_regression(
        """
        import asyncio, json
        from unittest.mock import AsyncMock
        import httpx
        from core.config import config
        from models.models import ToolDefinitionModel
        from transport.plugin_registry import PluginRegistry
        import main
        import services.custom_tool_service as custom
        async def scenario():
            config.http_remote_hosted = False
            config.transport_mode = "http"
            config.local_auth_token = "task-two-fixture-token"
            server = main.create_mcp_server(False)
            service = custom.CustomToolService.get_instance()
            definition = ToolDefinitionModel(name="http_custom", schema_version=1,
                input_schema={"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]})
            service._register_project_tools("a", [definition, ToolDefinitionModel(name="manage_scene")])
            service.register_global_tools([definition])
            custom.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            custom.get_user_id_from_context = AsyncMock(return_value=None)
            custom.resolve_project_id_for_unity_instance = lambda _: "a"
            main.resolve_project_id_for_unity_instance = lambda _: "a"
            send = custom.send_with_unity_instance = AsyncMock(return_value={"success": True})
            registry = main._plugin_registry = PluginRegistry()
            await registry.register("fixture", "A", "a", "6000")
            await registry.register_tools_for_session("fixture", [definition])
            custom.PluginHub.configure(registry, mcp=server)
            app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
            headers = {"X-Unity-MCP-Token": "task-two-fixture-token", "Accept": "application/json, text/event-stream"}
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=headers) as client:
                    listed = await client.get("/api/custom-tools", params={"instance": "A@a"})
                    assert listed.status_code == 200, listed.text
                    body = listed.json()
                    assert body["tool_count"] == 1 and [row["name"] for row in body["tools"]] == ["http_custom"], body
                    for payload, valid in (({"count": 1, "ctx": "payload-field", "other": None}, True), ({"count": "secret-value"}, False)):
                        response = await client.post("/mcp", headers={"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call", "Mcp-Name": "http_custom"},
                            json={"jsonrpc": "2.0", "id": "task-two", "method": "tools/call", "params": {
                                "name": "http_custom", "arguments": payload, "_meta": {
                                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                    "io.modelcontextprotocol/clientCapabilities": {},
                                    "io.modelcontextprotocol/clientInfo": {"name": "task-two", "version": "1"}}}})
                        assert response.status_code == 200, response.text
                        result = response.json()["result"]
                        assert bool(result.get("isError")) is not valid, result
                        if valid:
                            assert send.call_args.args[3] == payload
                            send.reset_mock()
                        else:
                            assert "invalid_custom_tool_parameters" in json.dumps(result) and "secret-value" not in json.dumps(result), result
                            send.assert_not_awaited()
        async def bounded():
            await asyncio.wait_for(scenario(), timeout=10)
        asyncio.run(bounded())
        """
    )


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_component_action_schema_matches_runtime_presence_and_null_contract(mode):
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
        components = import_module("services.tools.manage_components")
        async def scenario():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            app = FastMCP("component-actions")
            app.add_middleware(ResponseLimitMiddleware())
            components.preflight = AsyncMock(return_value=None)
            components.get_unity_instance_from_context = AsyncMock(return_value="A@a")
            send = components.send_with_unity_instance = AsyncMock(return_value={"success": True})
            register_all_tools(app, project_scoped_tools=False)
            app.enable(names={"manage_components"}, components={"tool"})
            async with Client(app, mode=MODE) as client:
                tool = next(tool for tool in await client.list_tools() if tool.name == "manage_components")
                validator = Draft202012Validator(tool.input_schema)
                base = {"action": "set_property", "target": "Player", "component_type": "Rigidbody"}
                for update in ({}, {"property": "mass"}, {"property": " " , "value": 3},
                    {"properties": None}, {"properties": {}}, {"properties": "{}"}):
                    arguments = {**base, **update}
                    assert not validator.is_valid(arguments), arguments
                    result = await client.call_tool(tool.name, arguments, raise_on_error=False)
                    assert result.is_error, result
                send.assert_not_awaited()
                components.preflight.assert_not_awaited()
                for update in ({"property": "reference", "value": None}, {"properties": {"mass": 3}},
                    {"properties": '{"mass":3}'}, {"property": "mass", "value": 0},
                    {"action": "add"}, {"action": "remove"}):
                    arguments = {**base, **update}
                    validator.validate(arguments)
                    result = await client.call_tool(tool.name, arguments, raise_on_error=False)
                    assert not result.is_error, result
                    if "value" in update:
                        assert send.call_args.args[3]["value"] == update["value"]
        asyncio.run(scenario())
        """.replace("MODE", repr(mode))
    )
