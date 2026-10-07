"""Exercise rendering queries through the installed MCP SDK, without Unity."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def _run_scenario(source, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_counter_category_schema_and_transport(tmp_path):
    _run_scenario(
        """
        import socket
        import sys
        from unittest.mock import AsyncMock
        import anyio
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from core.config import config
        import services.tools as tools
        import services.tools.manage_graphics as graphics
        from services.registry import get_registered_tools
        from transport.plugin_hub import PluginHub

        async def scenario():
            def deny(*args, **kwargs):
                raise AssertionError("outbound socket forbidden")
            socket.socket.connect = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            graphics.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            captured = []
            reply = {"success": True, "data": {"counters": []}}
            async def send(instance, command, params, **kwargs):
                assert instance == "Selected@hash" and command == "manage_graphics"
                assert kwargs == {"user_id": None, "retry_on_reload": True}
                captured.append(params)
                return reply
            PluginHub.send_command_for_instance = send
            metadata = [item for item in get_registered_tools() if item["name"] == "manage_graphics"]
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            mcp = FastMCP("rendering-query-contract")
            tools.register_all_tools(mcp)
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    tool = next(t for t in await client.list_tools() if t.name == "manage_graphics")
                    schema = tool.input_schema
                    assert "category" in schema["properties"], schema
                    category = schema["properties"]["category"]
                    assert category["default"] is None and "category" not in schema.get("required", [])
                    assert {s["type"] for s in category["anyOf"]} == {"string", "null"}
                    for arguments, expected in [({}, {}), ({"category": None}, {}),
                            *[({"category": value}, {"category": value}) for value in
                              ("Memory", "Scripts", "mEmOrY", "", "unrecognized")]]:
                        result = await client.call_tool("manage_graphics", {"action": "stats_list_counters", **arguments})
                        assert result.data == reply
                        assert captured[-1] == {"action": "stats_list_counters", **expected}
                    before = len(captured)
                    result = await client.call_tool("manage_graphics", {"action": "stats_list_counters", "category": 1}, raise_on_error=False)
                    assert result.is_error and len(captured) == before
        anyio.run(scenario)
    """,
        tmp_path,
    )


def test_rendering_resources_preserve_response_and_routing(tmp_path):
    _run_scenario(
        """
        import json
        import socket
        import sys
        from unittest.mock import AsyncMock
        import anyio
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from core.config import config
        import services.resources as resources
        from services.resources import cameras, volumes, renderer_features, rendering_stats
        from services.registry import get_registered_resources
        from transport.plugin_hub import PluginHub

        async def scenario():
            def deny(*args, **kwargs):
                raise AssertionError("outbound socket forbidden")
            socket.socket.connect = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            specs = [(cameras, "mcpforunity://scene/cameras", "get_cameras"),
                     (volumes, "mcpforunity://scene/volumes", "get_volumes"),
                     (renderer_features, "mcpforunity://pipeline/renderer-features", "get_renderer_features"),
                     (rendering_stats, "mcpforunity://rendering/stats", "get_rendering_stats")]
            for module, _, _ in specs:
                module.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            captured = []
            reply = None
            async def send(instance, command, params, **kwargs):
                assert instance == "Selected@hash" and params == {}
                captured.append(command)
                return reply
            PluginHub.send_command_for_instance = send
            selected = {uri for _, uri, _ in specs}
            metadata = [item for item in get_registered_resources() if item["uri"] in selected]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("rendering-resources-contract")
            resources.register_all_resources(mcp)
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    assert {str(r.uri) for r in await client.list_resources()} == selected
                    for _, uri, command in specs:
                        for payload in [
                            {"success": True, "message": "fixture", "data": {"nested": [{"zero": 0, "enabled": False, "name": "camera"}]}},
                            {"success": True, "data": {}},
                            {"success": False, "error": "unavailable", "hint": "retry", "data": {"reason": "fixture", "nested": []}},
                        ]:
                            reply = payload
                            result = json.loads((await client.read_resource(uri))[0].text)
                            assert result["success"] == payload["success"] and result["data"] == payload["data"]
                            if not payload["success"]:
                                assert result["error"] == payload["error"] and result["hint"] == payload["hint"]
                            assert captured[-1] == command
        anyio.run(scenario)
    """,
        tmp_path,
    )
