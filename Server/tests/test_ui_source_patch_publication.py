"""Source persistence preview/apply at the actual FastMCP SDK boundary."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_source_patch_publication_at_actual_sdk_boundary(tmp_path):
    program = textwrap.dedent(r"""
        import asyncio
        import importlib
        from fastmcp import Client, FastMCP
        from services.registry import get_registered_tools
        module = importlib.import_module("services.tools.manage_ui")
        sent = []
        async def instance(ctx): return None
        async def read(sender, selected, command, params):
            sent.append(("read", params))
            return {"success": True, "data": {"preview": True, "source_hash": "a" * 64}}
        async def write(ctx, selected, command, params):
            sent.append(("write", params))
            return {"success": True, "data": {"preview": False}}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = read
        module.send_mutation = write
        entry = next(e for e in get_registered_tools() if e["name"] == "manage_ui")
        server = FastMCP("ui-source-patch-contract")
        server.tool(name=entry["name"], **entry["kwargs"])(entry["func"])
        async def main():
            async with Client(server) as client:
                tool = next(t for t in await client.list_tools() if t.name == "manage_ui")
                schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
                assert "patch_source" in schema["properties"]["action"]["enum"]
                args = {"action": "patch_source", "path": "Assets/UI/A.uxml",
                        "source_selector": "#title", "source_changes": {"text": "Changed"}}
                result = await client.call_tool("manage_ui", args)
                assert result.structured_content["success"], result
                assert sent[-1][0] == "read"
                assert sent[-1][1]["preview"] is True
                result = await client.call_tool("manage_ui", {**args, "preview": False, "expected_source_hash": "a" * 64})
                assert result.structured_content["success"], result
                assert sent[-1][0] == "write"
                assert sent[-1][1]["source_changes"] == {"text": "Changed"}
                for invalid in ({"preview": False}, {"preview": "false"}, {"preview": 0},
                                {"preview": None}, {"source_changes": {}},
                                {"path": "Assets/../A.uxml"}, {"source_changes": {"text": 12}},
                                {"source_changes": {str(i): "v" for i in range(33)}}):
                    before = len(sent)
                    try:
                        result = await client.call_tool("manage_ui", {**args, **invalid})
                    except Exception as exc:
                        assert type(exc).__name__ in ("ToolError", "MCPError", "ValidationError"), exc
                    else:
                        assert result.is_error or not result.structured_content["success"], result
                    assert len(sent) == before
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
