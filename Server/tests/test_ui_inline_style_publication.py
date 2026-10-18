"""Inline style contracts at the real SDK boundary with controlled mutation I/O."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_inline_style_publication_at_actual_sdk_boundary(tmp_path):
    program = textwrap.dedent(r"""
        import asyncio
        import importlib
        import json
        from fastmcp import Client, FastMCP
        from fastmcp.exceptions import ToolError
        from mcp import MCPError
        from pydantic import ValidationError
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        from services.registry import get_registered_tools

        module = importlib.import_module("services.tools.manage_ui")
        sent = []
        resolutions = []
        partial = {"success": True, "data": {"modifications": ["width=typed"],
            "style_results": {"schema_version": 1,
                "applied": [{"index": 0, "key": "width", "property": "width", "reason": "assigned",
                    "assigned_value": {"value": 50, "unit": "%"},
                    "final_inline_value": {"value": 50, "unit": "%"}}],
                "rejected": [{"index": 1, "key": "color", "property": "color", "reason": "invalid_color"}]}}}
        refused = {"success": False, "error": "strict_style_rejected", "data": {
            "style_results": {"schema_version": 1, "applied": [], "rejected": [
                {"index": 0, "key": "width", "property": "width", "reason": "request_aborted_before_mutation"},
                {"index": 1, "key": "color", "property": "color", "reason": "invalid_color"}]}}}
        async def instance(ctx):
            resolutions.append(True)
            return None
        async def send(ctx, selected, command, params, **kwargs):
            assert command == "manage_ui"
            sent.append(dict(params))
            return refused if params.get("strict_style") else partial
        async def unexpected(*args, **kwargs):
            raise AssertionError("inline style mutation used read transport")
        module.get_unity_instance_from_context = instance
        module.send_mutation = send
        module.send_with_unity_instance = unexpected
        entry = next(entry for entry in get_registered_tools() if entry["name"] == "manage_ui")
        server = FastMCP("ui-inline-style-contract")
        wrapped = log_execution("manage_ui", "Tool")(entry["func"])
        wrapped = telemetry_tool("manage_ui")(wrapped)
        server.tool(name=entry["name"], **entry["kwargs"])(wrapped)
        styles = {"width": {"value": 50, "unit": "%"}, "color": "not-a-color"}
        async def main():
            for protocol in ("2026-07-28", "legacy"):
                async with Client(server, mode=protocol) as client:
                    tool = next(tool for tool in await client.list_tools() if tool.name == "manage_ui")
                    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
                    assert schema["properties"]["strict_style"]["type"] == "boolean", schema
                    assert schema["properties"]["strict_style"]["default"] is False
                    for flag in ("omitted", False, True):
                        args = {"action": "modify_visual_element", "target": "123", "element_name": "owned", "style": styles}
                        if flag != "omitted": args["strict_style"] = flag
                        result = await client.call_tool("manage_ui", args)
                        expected = refused if flag is True else partial
                        assert result.structured_content == expected, result
                        blocks = [json.loads(block.text) for block in result.content if getattr(block, "type", None) == "text"]
                        assert blocks == [expected], blocks
                        assert sent[-1]["style"] == styles
                        assert ("strict_style" in sent[-1]) == (flag is True), sent[-1]
                    for invalid in ("true", 1, None):
                        before = (len(sent), len(resolutions))
                        try:
                            result = await client.call_tool("manage_ui", {"action": "modify_visual_element",
                                "target": "123", "element_name": "owned", "strict_style": invalid})
                        except (MCPError, ToolError, ValidationError):
                            pass
                        else:
                            assert result.is_error, result
                        assert (len(sent), len(resolutions)) == before
                    before = (len(sent), len(resolutions))
                    result = await client.call_tool("manage_ui", {"action": "ping", "strict_style": True})
                    assert result.structured_content["success"] is False
                    assert result.structured_content["message"] == "strict_style requires modify_visual_element."
                    assert (len(sent), len(resolutions)) == before
            assert len(sent) == 6
            print("UI inline style SDK contracts passed")
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
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UI inline style SDK contracts passed" in result.stdout
