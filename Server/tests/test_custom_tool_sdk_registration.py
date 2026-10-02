import os
from pathlib import Path
import subprocess
import sys
import textwrap


def run_sdk_regression(source):
    # The legacy integration suite replaces SDK modules during collection.
    server_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=server_root, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dynamic_custom_tool_registers_and_executes_through_real_sdk():
    run_sdk_regression('''
        import asyncio, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from models.models import MCPResponse, ToolDefinitionModel, ToolParameterModel
        import services.custom_tool_service as module
        from services.custom_tool_service import CustomToolService

        async def scenario():
            mcp = FastMCP("custom-tool-regression")
            service = CustomToolService(mcp)
            module.get_unity_instance_from_context = AsyncMock(return_value="Project@hash")
            module.resolve_project_id_for_unity_instance = lambda instance: "project"
            service.execute_tool = AsyncMock(return_value=MCPResponse(success=True, data={"received": True}))
            service.register_global_tools([ToolDefinitionModel(name="custom_echo", parameters=[
                ToolParameterModel(name="count", type="integer", required=False, default_value="3"),
                ToolParameterModel(name="text", type="string", required=True),
            ])])
            async with Client(mcp) as client:
                tools = await client.list_tools()
                tool = next((tool for tool in tools if tool.name == "custom_echo"), None)
                assert tool is not None, "Parameterized custom tool disappeared during SDK registration"
                assert set(tool.inputSchema["properties"]) == {"count", "text"}
                assert tool.inputSchema["properties"]["count"]["type"] == "integer"
                assert tool.inputSchema["properties"]["count"]["default"] == 3
                assert tool.inputSchema["required"] == ["text"]
                result = await client.call_tool("custom_echo", {"text": "hello"})
                assert not result.is_error
            service.execute_tool.assert_awaited_once_with(
                "project", "custom_echo", "Project@hash", {"count": 3, "text": "hello"}, user_id=None,
            )
        asyncio.run(scenario())
    ''')


def test_invalid_custom_parameter_names_do_not_abort_following_registrations():
    run_sdk_regression('''
        import asyncio, sys
        sys.path.insert(0, "src")
        from fastmcp import FastMCP
        from models.models import ToolDefinitionModel, ToolParameterModel
        from services.custom_tool_service import CustomToolService

        async def scenario():
            mcp = FastMCP("custom-invalid-parameter-regression")
            service = CustomToolService(mcp)
            definitions = [ToolDefinitionModel(name=f"invalid_{i}", parameters=[
                ToolParameterModel(name=name) for name in names
            ]) for i, names in enumerate([["ctx"], ["unity_instance"], ["value", "value"], ["class"], ["bad-name"]])]
            valid = ToolDefinitionModel(name="valid_empty")
            service.register_global_tools(definitions + [valid])
            assert [tool.name for tool in await mcp.list_tools()] == ["valid_empty"]
            assert list(service._global_tools) == ["valid_empty"]
        asyncio.run(scenario())
    ''')


def test_custom_structured_output_flag_controls_schema_and_result():
    run_sdk_regression('''
        import asyncio, json, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from models.models import MCPResponse, ToolDefinitionModel
        import services.custom_tool_service as module
        from services.custom_tool_service import CustomToolService

        async def scenario():
            mcp = FastMCP("custom-output-regression")
            service = CustomToolService(mcp)
            module.get_unity_instance_from_context = AsyncMock(return_value="Project@hash")
            module.resolve_project_id_for_unity_instance = lambda instance: "project"
            service.execute_tool = AsyncMock(return_value=MCPResponse(success=True, data={"received": True}))
            service.register_global_tools([
                ToolDefinitionModel(name="structured_echo"),
                ToolDefinitionModel(name="text_echo", structured_output=False),
            ])
            async with Client(mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                assert tools["text_echo"].output_schema is None
                structured = await client.call_tool("structured_echo", {})
                text = await client.call_tool("text_echo", {})
                assert structured.structured_content["data"] == {"received": True}
                assert text.structured_content is None
                assert json.loads(text.content[0].text) == structured.structured_content
                module.get_unity_instance_from_context.return_value = None
                error = await client.call_tool("text_echo", {})
                assert error.structured_content is None
                assert json.loads(error.content[0].text)["success"] is False
        asyncio.run(scenario())
    ''')
