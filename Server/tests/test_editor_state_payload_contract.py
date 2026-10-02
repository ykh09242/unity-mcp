"""Exercise readiness payload validation through the actual SDK in a fresh process."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_editor_state_rejects_missing_payload_without_fabricating_readiness():
    source = '''
        import asyncio, copy, json, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        import services.resources as resources
        import services.resources.editor_state as state
        from services.registry import get_registered_resources

        async def scenario():
            state.get_unity_instance_from_context = AsyncMock(return_value=None)
            state.infer_single_instance_id = AsyncMock(return_value=None)
            state._now_unix_ms = lambda: 5000
            metadata = [r for r in get_registered_resources() if r["name"] == "editor_state"]
            resources.discover_modules = lambda *args: []
            resources.get_registered_resources = lambda: metadata
            mcp = FastMCP("editor-state-payload-contract")
            resources.register_all_resources(mcp)
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    for raw in (None, "invalid", {"success": True}, {"success": True, "data": None},
                                {"success": True, "data": []}, {"success": True, "data": "invalid"}):
                        state.unity_transport.send_with_unity_instance = AsyncMock(return_value=copy.deepcopy(raw))
                        response = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                        assert response["success"] is False, (mode, raw, response)
                        assert response["error"] == "invalid_editor_state", response
                        assert response.get("data") is None or not response["data"].get("advice"), response

                    for data in ({}, {"observed_at_unix_ms": 5000, "sequence": 0},
                                 {"observed_at_unix_ms": 5000, "compilation": {"is_compiling": False},
                                  "tests": {"is_running": False}}):
                        state.unity_transport.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": copy.deepcopy(data)})
                        response = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                        assert response["success"] is True, response
                        assert response["data"]["advice"]["ready_for_tools"] is True, response
                        assert response["data"]["sequence"] == 0, response

                    state.unity_transport.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"observed_at_unix_ms": 0}})
                    response = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    assert response["success"] is True and response["data"]["staleness"]["is_stale"] is True, response
                    assert response["data"]["advice"]["ready_for_tools"] is False, response
                    state.unity_transport.send_with_unity_instance = AsyncMock(return_value={"success": False, "error": "busy", "data": {"retry_after_ms": 25}})
                    response = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
                    assert response["success"] is False and response["error"] == "busy", response
                    assert response["data"]["retry_after_ms"] == 25, response

        asyncio.run(scenario())
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
