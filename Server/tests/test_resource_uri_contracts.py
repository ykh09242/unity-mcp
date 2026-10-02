import os
from pathlib import Path
import subprocess
import sys
import textwrap


def run_sdk_regression(source):
    # Legacy collection stubs SDK modules; exercise the actual dependency separately.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "1"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_component_resource_uri_exposes_declared_options_and_keeps_old_route():
    run_sdk_regression('''
        import asyncio, json, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from mcp.shared.exceptions import MCPError
        from fastmcp.exceptions import ResourceError
        import services.resources as resources
        import services.resources.gameobject as gameobject
        from services.registry import get_registered_resources

        async def send(fn, instance, command, params):
            assert instance == "Selected@hash"
            assert command == "get_gameobject_components"
            return {"success": True, "data": params}

        async def scenario():
            gameobject.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            gameobject.send_with_unity_instance = AsyncMock(side_effect=send)
            metadata = [item for item in get_registered_resources() if item["name"] == "gameobject_components"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("component-uri-regression")
            resources.register_all_resources(mcp)
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    base = "mcpforunity://scene/gameobject/42/components"
                    original = json.loads((await client.read_resource(base))[0].text)
                    assert original["data"] == {"instanceID": 42, "pageSize": 25, "cursor": 0, "includeProperties": True}
                    page = json.loads((await client.read_resource(base + "?page_size=2&cursor=3&include_properties=false"))[0].text)
                    assert page["data"] == {"instanceID": 42, "pageSize": 2, "cursor": 3, "includeProperties": False}, page
                    partial = json.loads((await client.read_resource(base + "?cursor=5"))[0].text)
                    assert partial["data"] == {"instanceID": 42, "pageSize": 25, "cursor": 5, "includeProperties": True}
                    for query in ("?page_size=bad", "?cursor=bad", "?include_properties=bad"):
                        before = gameobject.send_with_unity_instance.await_count
                        try:
                            await client.read_resource(base + query)
                        except (ResourceError, MCPError):
                            pass
                        else:
                            raise AssertionError("Malformed query must fail validation")
                        assert gameobject.send_with_unity_instance.await_count == before
                    before = gameobject.send_with_unity_instance.await_count
                    invalid = json.loads((await client.read_resource("mcpforunity://scene/gameobject/invalid/components?cursor=1"))[0].text)
                    assert invalid["success"] is False
                    assert gameobject.send_with_unity_instance.await_count == before
        asyncio.run(scenario())
    ''')
