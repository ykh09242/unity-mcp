"""Check graphics failure and optional-value contracts using the real SDK."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_graphics_sdk_preserves_rejected_bakes_and_optional_values():
    source = '''
        import sys
        from unittest.mock import AsyncMock
        import anyio
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        import services.tools as tools
        import services.tools.manage_graphics as graphics
        from services.registry import get_registered_tools

        async def scenario():
            graphics.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            captured = []
            response = {"success": False, "code": "bake_rejected", "error": "bake rejected"}
            async def send(fn, instance, command, params):
                assert instance == "Selected@hash" and command == "manage_graphics"
                captured.append(params)
                return response
            graphics.send_with_unity_instance = send
            metadata = [item for item in get_registered_tools() if item["name"] == "manage_graphics"]
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            mcp = FastMCP("graphics-response-contract")
            tools.register_all_tools(mcp)
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    for async_flag in (False, True):
                        result = await client.call_tool("manage_graphics", {"action": "bake_start", "async_bake": async_flag})
                        assert result.data == response, (mode, result)
                        assert captured[-1] == {"action": "bake_start", "async": async_flag}, captured[-1]
                    result = await client.call_tool("manage_graphics", {"action": "volume_create", "weight": 0, "priority": 0, "is_global": False, "profile_path": None})
                    assert captured[-1] == {"action": "volume_create", "weight": 0, "priority": 0, "is_global": False}, captured[-1]
                    result = await client.call_tool("manage_graphics", {"action": "bake_start", "async_bake": None})
                    assert captured[-1] == {"action": "bake_start"}, captured[-1]
                    response = {"success": True, "_mcp_status": "pending", "_mcp_poll_interval": 2.0, "data": {"mode": "async"}}
                    result = await client.call_tool("manage_graphics", {"action": "bake_start"})
                    assert result.data == response, result
                    response = {"success": True, "data": {"mode": "sync", "lightmapCount": 0}}
                    result = await client.call_tool("manage_graphics", {"action": "bake_start", "async_bake": False})
                    assert result.data == response, result
                    response = {"success": False, "code": "bake_rejected", "error": "bake rejected"}
                    before = len(captured)
                    result = await client.call_tool("manage_graphics", {"action": "invalid"})
                    assert result.data["success"] is False and len(captured) == before
        anyio.run(scenario)
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
