"""Registered actual SDK prefab URIs preserve already-decoded asset paths."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize(
    "resource_name,suffix,action",
    [
        ("prefab_info", "", "get_info"),
        ("prefab_hierarchy", "/hierarchy", "get_hierarchy"),
    ],
)
def test_prefab_resource_preserves_decoded_path_and_error_metadata(
    resource_name, suffix, action, tmp_path
):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    source = f"""
        import json, os, socket, sys
        from pathlib import Path
        from urllib.parse import quote
        from unittest.mock import AsyncMock
        home = Path(os.environ["APPDATA"]).parent / "owned-home"
        home.mkdir()
        Path.home = lambda: home
        sys.path.insert(0, "src")
        import anyio
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.resources as resources
        from services.resources import prefab
        from services.registry import get_registered_resources
        from transport.plugin_hub import PluginHub

        async def scenario():
            # Given actual templates, an explicit selected instance, and inert Unity.
            def deny(*args, **kwargs): raise AssertionError("application network forbidden")
            socket.socket.connect = deny
            socket.create_connection = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            prefab.get_unity_instance_from_context = AsyncMock(return_value="Selected@owned")
            metadata = [item for item in get_registered_resources() if item["name"] == {resource_name!r}]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            server = FastMCP("prefab-path-contract")
            resources.register_all_resources(server)
            requests = []
            reply = {{"success": True, "data": {{"extra_native_field": {{"keep": True}}}}}}
            async def send(instance, command, params, **kwargs):
                requests.append((instance, command, dict(params)))
                return reply
            PluginHub.send_command_for_instance = send
            paths = (
                "Assets/Prefabs/Player.prefab", "Assets/Prefabs/My Player.prefab",
                "Assets/Prefabs/Icon%20Small.prefab", "Assets/Prefabs/Icon%2FSmall.prefab",
                "Assets/Prefabs/Rate%25.prefab", "Assets/Prefabs/Literal%23.prefab",
                "Assets/Prefabs/Hash#Name.prefab", "Assets/Prefabs/\ud55c\uae00.prefab",
                "Assets/Prefabs/100%Ready.prefab",
            )
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    for path in paths:
                        # When Client reads the correctly percent-encoded public URI.
                        uri = "mcpforunity://prefab/" + quote(path, safe="") + {suffix!r}
                        result = json.loads((await client.read_resource(uri))[0].text)
                        # Then Unity receives the identical asset path exactly once.
                        assert requests[-1] == ("Selected@owned", "manage_prefabs", {{"action": {action!r}, "prefabPath": path}}), requests[-1]
                        assert result["success"] is True and result["data"] == reply["data"]
                    reply = {{"success": False, "error": "fixture_missing", "hint": "retry", "data": {{"reason": "reloading", "retry_after_ms": 123}}}}
                    result = json.loads((await client.read_resource("mcpforunity://prefab/" + quote(paths[2], safe="") + {suffix!r}))[0].text)
                    for key, value in reply.items(): assert result[key] == value
                    assert requests[-1][2]["prefabPath"] == paths[2]
                    reply = {{"success": True, "data": {{"extra_native_field": {{"keep": True}}}}}}
            assert len(requests) == 20
        anyio.run(scenario)
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
