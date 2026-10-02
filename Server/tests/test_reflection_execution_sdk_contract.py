"""Exercise reflection names and execution values through real SDK and CLI routes."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_reflection_execution_sdk_and_cli_contracts():
    source = '''
        import sys
        from unittest.mock import AsyncMock
        import anyio
        from click.testing import CliRunner
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        import services.tools as tools
        import services.tools.unity_reflect as reflection
        import services.tools.execute_code as execution
        from services.registry import get_registered_tools
        from cli.commands.reflect import reflect
        from cli.utils import connection
        from cli.utils.config import CLIConfig, set_config

        names = ["Dictionary<string,List<int>>", "Dictionary<List<int>,Dictionary<string,List<bool>>>"]
        captured = []
        response = {"success": True, "message": "safe generic", "data": {"found": True, "is_generic_type_definition": True}}
        async def send(fn, instance, command, params):
            assert instance == "Selected@hash"
            captured.append((command, params))
            return response
        reflection.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
        execution.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
        reflection.send_with_unity_instance = send
        execution.send_with_unity_instance = send
        metadata = [item for item in get_registered_tools() if item["name"] in {"unity_reflect", "execute_code"}]
        tools.discover_modules = lambda *args: []
        tools.get_registered_tools = lambda: metadata
        mcp = FastMCP("reflection-execution-contract")
        tools.register_all_tools(mcp)

        async def scenario():
            global response
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    for name in names:
                        result = await client.call_tool("unity_reflect", {"action": "get_type", "class_name": name})
                        assert result.data == response
                        assert captured[-1] == ("unity_reflect", {"action": "get_type", "class_name": name})
                        result = await client.call_tool("unity_reflect", {"action": "get_member", "class_name": name, "member_name": "Add"})
                        assert captured[-1] == ("unity_reflect", {"action": "get_member", "class_name": name, "member_name": "Add"})
                    for value in (0, False, None):
                        response = {"success": True, "message": "executed", "data": {"result": value, "compiler": "roslyn"}}
                        result = await client.call_tool("execute_code", {"action": "execute", "code": "return 0;", "safety_checks": False, "compiler": "roslyn"})
                        assert result.data == response
                        assert captured[-1] == ("execute_code", {"action": "execute", "code": "return 0;", "safety_checks": False, "compiler": "roslyn"})
                    before = len(captured)
                    try:
                        await client.call_tool("execute_code", {"action": "execute", "code": "return 1;", "compiler": "unknown"})
                    except Exception as error:
                        assert "compiler" in str(error), error
                    else:
                        raise AssertionError("SDK must reject an unknown compiler")
                    assert len(captured) == before
                    response = {"success": True, "message": "safe generic", "data": {"found": True, "is_generic_type_definition": True}}
        anyio.run(scenario)

        async def cli_send(command, params, config, timeout):
            captured.append((command, params))
            return response
        connection.send_command = cli_send
        set_config(CLIConfig(format="json"))
        runner = CliRunner()
        for name in names:
            result = runner.invoke(reflect, ["type", name])
            assert result.exit_code == 0, result.output
            assert captured[-1] == ("unity_reflect", {"action": "get_type", "class_name": name})
            result = runner.invoke(reflect, ["member", name, "Add"])
            assert result.exit_code == 0, result.output
            assert captured[-1] == ("unity_reflect", {"action": "get_member", "class_name": name, "member_name": "Add"})
    '''
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
