"""Readiness validation through the installed SDK and production registration."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_editor_readiness_nested_validation_and_existing_advice(tmp_path):
    source = '''
        import copy
        import json
        import socket
        import sys
        from unittest.mock import AsyncMock
        import anyio
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.resources as resources
        import services.resources.editor_state as state
        from services.registry import get_registered_resources
        from transport.plugin_hub import PluginHub

        async def scenario():
            def deny(*args, **kwargs):
                raise AssertionError("outbound socket forbidden")
            socket.socket.connect = deny
            config.transport_mode = "http"
            config.http_remote_hosted = False
            state.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
            state._now_unix_ms = lambda: 5000
            state.external_changes_scanner.update_and_get = lambda instance: {}
            reply = None
            captured = []
            async def send(instance, command, params, **kwargs):
                assert instance == "Selected@hash" and params == {}
                captured.append(command)
                if command == "get_project_info":
                    return {"success": False, "error": "controlled lookup unavailable"}
                assert command == "get_editor_state"
                return copy.deepcopy(reply)
            PluginHub.send_command_for_instance = send
            metadata = [r for r in get_registered_resources() if r["name"] == "editor_state"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("editor-readiness-contract")
            resources.register_all_resources(mcp)
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    for section in ({"compilation": "invalid"}, {"compilation": [1]},
                                    {"tests": True}, {"tests": "invalid"},
                                    {"assets": {"refresh": [1]}}, {"assets": {"refresh": "invalid"}},
                                    {"compilation": []}, {"sequence": "invalid"}):
                        reply = {"success": True, "data": {"observed_at_unix_ms": 5000, **section}}
                        before = len(captured)
                        result = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                        assert result["success"] is False and result["error"] == "invalid_editor_state", result
                        raw = result["data"]["raw"]
                        for key, value in section.items():
                            if key == "assets":
                                assert raw[key]["refresh"] == value["refresh"]
                            else:
                                assert raw[key] == value
                        assert raw["unity"]["instance_id"] == "Selected@hash"
                        assert captured[before] == "get_editor_state"
                    for observed, expected_age, stale in ((3000, 2000, False), (2999, 2001, True),
                                                         (6000, 0, False), (0, 5000, True)):
                        reply = {"success": True, "data": {"observed_at_unix_ms": observed, "sequence": 0,
                                 "compilation": None, "tests": None, "assets": {"refresh": None}}}
                        result = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                        assert result["success"] is True and result["data"]["sequence"] == 0
                        assert result["data"]["observed_at_unix_ms"] == observed
                        assert result["data"]["staleness"] == {"age_ms": expected_age, "is_stale": stale}
                        advice = result["data"]["advice"]
                        assert advice["ready_for_tools"] is (not stale)
                        assert advice["blocking_reasons"] == (["stale_status"] if stale else [])
                        assert advice["recommended_retry_after_ms"] == (500 if stale else 0)
                    reply = {"success": True, "data": {}}
                    result = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    assert result["success"] and result["data"]["sequence"] == 0
                    assert result["data"]["observed_at_unix_ms"] == 5000
                    assert result["data"]["advice"]["ready_for_tools"] is True
                    reply = {"success": True, "data": {"observed_at_unix_ms": 0,
                             "compilation": {"is_compiling": True, "is_domain_reload_pending": True},
                             "tests": {"is_running": True}, "assets": {"refresh": {"is_refresh_in_progress": True}}}}
                    result = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    assert result["data"]["advice"]["blocking_reasons"] == ["compiling", "domain_reload", "running_tests", "asset_refresh", "stale_status"]
                    reply = {"success": False, "error": "busy", "hint": "retry", "data": {"retry_after_ms": 25}}
                    before = len(captured)
                    result = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    assert all(result[key] == value for key, value in reply.items())
                    assert captured[before:] == ["get_editor_state"]
        anyio.run(scenario)
    '''
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
