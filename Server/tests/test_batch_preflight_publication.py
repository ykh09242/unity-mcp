"""Batch preview uses the real public SDK and controlled transport, never Unity."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_batch_preview_at_actual_sdk_boundary(tmp_path):
    program = textwrap.dedent(r"""
        import asyncio
        import copy
        import importlib
        import json
        from fastmcp import Client, FastMCP
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool

        module = importlib.import_module("services.tools.batch_execute")
        sent = []
        lookups = []
        commands = [{"tool": "manage_gameobject", "params": {"action": "create", "name": "fixture"}}]
        preview = {"success": True, "data": {"validation": {
            "contractVersion": 1, "validateOnly": True, "scope": "action_names",
            "complete": True, "valid": True, "items": [{"index": 0,
            "tool": "manage_gameobject", "toolTruncated": False, "status": "valid", "reason": "ok"}]}}}
        reply = preview
        async def instance(ctx): lookups.append(1); return None
        async def limit(ctx, instance): return 25
        async def send(fn, instance, name, params, **kwargs):
            sent.append((name, copy.deepcopy(params)))
            return copy.deepcopy(reply)
        module.get_unity_instance_from_context = instance
        module._get_max_commands_from_editor_state = limit
        module.send_with_unity_instance = send
        server = FastMCP("batch-preview-contract")
        wrapped = log_execution("batch_execute", "Tool")(module.batch_execute)
        server.tool(name="batch_execute")(telemetry_tool("batch_execute")(wrapped))

        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    tool = next(row for row in await client.list_tools() if row.name == "batch_execute")
                    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
                    assert schema["properties"]["validate_only"]["type"] == "boolean"
                    assert schema["properties"]["validate_only"]["default"] is False
                    for supplied in ({}, {"validate_only": False}):
                        before = copy.deepcopy(commands)
                        result = await client.call_tool("batch_execute", {"commands": commands, **supplied})
                        assert sent[-1] == ("batch_execute", {"commands": commands})
                        assert commands == before
                    result = await client.call_tool("batch_execute", {"commands": commands, "validate_only": True})
                    assert result.structured_content == preview
                    assert json.loads(result.content[0].text) == preview
                    assert sent[-1] == ("batch_validate", {"commands": commands})
                    for bad in (None, "true", "false", 0, 1, [], {}):
                        before = (len(sent), len(lookups))
                        try:
                            result = await client.call_tool("batch_execute", {"commands": commands, "validate_only": bad})
                        except Exception:
                            pass
                        else:
                            assert result.is_error
                        assert (len(sent), len(lookups)) == before
                    for nested in ("batch_execute", "BATCH_EXECUTE", "batch_validate", "BATCH_VALIDATE"):
                        before = len(sent)
                        try:
                            result = await client.call_tool("batch_execute", {"commands": [{"tool": nested}], "validate_only": True})
                        except Exception:
                            pass
                        else:
                            assert result.is_error
                        assert len(sent) == before
            print("Batch public preview contract: both modes, exact legacy wire and zero-dispatch refusals")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "APPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "both modes, exact legacy wire and zero-dispatch refusals" in result.stdout
