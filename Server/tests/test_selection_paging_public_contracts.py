"""Actual registered selection resource reads in both supported SDK modes."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_selection_paging_registered_contract(mode, tmp_path):
    """Given registered resources, SDK reads enforce bounds and preserve native outcomes."""
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    source_path = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys([str(source_path), *env.get("PYTHONPATH", "").split(os.pathsep)])
    )
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        env[key] = str(tmp_path / key)
    program = r"""
        import copy, json, socket
        from unittest.mock import AsyncMock
        import anyio
        from fastmcp import Client, FastMCP
        from fastmcp.exceptions import ResourceError
        from mcp.shared.exceptions import MCPError
        from core.config import config
        import services.resources as resources
        from services.resources import selection
        from services.registry import get_registered_resources
        from transport.plugin_hub import PluginHub

        async def scenario():
            def deny(*args, **kwargs): raise AssertionError("application network forbidden")
            socket.socket.connect = deny
            socket.create_connection = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            selection.get_unity_instance_from_context = AsyncMock(return_value="Owned@selection")
            metadata = [item for item in get_registered_resources()
                        if item["name"] in {"editor_selection", "editor_selection_page"}]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            server = FastMCP("selection-contract")
            resources.register_all_resources(server)
            requests = []
            reply = {"success": True, "data": {"count": 10000, "objects": [],
                     "activeObject": None, "nextCursor": "a" * 32, "nativeExtra": False}}
            async def send(instance, command, params, **kwargs):
                requests.append((instance, command, copy.deepcopy(params)))
                return copy.deepcopy(reply)
            PluginHub.send_command_for_instance = staticmethod(send)
            async with Client(server, mode=MODE) as client:
                # Given: both legacy and paged resources are discoverable.
                assert any(str(item.uri) == "mcpforunity://editor/selection"
                           for item in await client.list_resources())
                templates = await client.list_resource_templates()
                template = next(item for item in templates if item.name == "editor_selection_page")
                assert str(template.uri_template) == "mcpforunity://editor/selection/page{?page_size,cursor,fields}"
                async def read(suffix):
                    return json.loads((await client.read_resource("mcpforunity://editor/selection" + suffix))[0].text)

                # When: default, cheap, and projected pages pass through the real SDK.
                for suffix, expected in (
                    ("/page", {"paged": True, "page_size": 50, "fields": "objects"}),
                    ("/page?fields=none", {"paged": True, "page_size": 50, "fields": "none"}),
                    ("/page?page_size=500&fields=all", {"paged": True, "page_size": 500, "fields": "all"}),
                    ("/page?page_size=1&fields=gameObjects&cursor=" + "b" * 32,
                     {"paged": True, "page_size": 1, "fields": "gameObjects", "cursor": "b" * 32}),
                    ("/page?fields=assetGUIDs", {"paged": True, "page_size": 50, "fields": "assetGUIDs"}),
                    ("/page?page_size=%31&fields=%6f%62%6a%65%63%74%73",
                     {"paged": True, "page_size": 1, "fields": "objects"}),
                ):
                    before = len(requests)
                    result = await read(suffix)
                    # Then: routing and opaque response values survive exactly.
                    assert requests[before:] == [("Owned@selection", "get_selection", expected)]
                    assert result["data"] == reply["data"]

                for suffix in ("/page?page_size=0", "/page?page_size=501", "/page?page_size=1.5",
                               "/page?page_size=true", "/page?fields=unknown", "/page?cursor=bad",
                               "/page?cursor=" + "z" * 32, "/page?cursor=" + "a" * 33):
                    before = len(requests)
                    try:
                        result = await read(suffix)
                    except (ResourceError, MCPError):
                        pass
                    else:
                        assert result["success"] is False, (suffix, result)
                    assert len(requests) == before, suffix

                # Native selection revision/session rejections retain their recovery data.
                reply = {"success": False, "error": "stale_cursor", "hint": "restart_read",
                         "data": {"reason": "selection_changed", "count": 0}}
                result = await read("/page?cursor=" + "a" * 32)
                for key, value in reply.items(): assert result[key] == value

                # Existing callers retain their URI, empty command arguments, and shape.
                reply = {"success": True, "data": {"activeObject": None, "activeGameObject": None,
                         "activeTransform": None, "activeInstanceID": 0, "count": 0,
                         "objects": [], "gameObjects": [], "assetGUIDs": []}}
                before = len(requests)
                result = await read("")
                assert requests[before:] == [("Owned@selection", "get_selection", {})]
                assert result["data"] == reply["data"]
            print("selection SDK contract passed", MODE, len(requests))
        anyio.run(scenario)
    """
    result = subprocess.run(
        [sys.executable, "-B", "-c", "MODE = " + repr(mode) + "\n" + textwrap.dedent(program)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
