"""UI inventory revision routing at the real FastMCP SDK boundary."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("tool_name", ["manage_ui", "ui_source"])
def test_inventory_revision_reaches_read_bridge_and_rejects_invalid_calls(
    tmp_path: Path, tool_name: str
) -> None:
    program = textwrap.dedent(r"""
        import anyio
        import importlib
        from fastmcp import Client, FastMCP
        from services.tools import register_all_tools

        module = importlib.import_module("services.tools.manage_ui")
        sent = []
        async def instance(ctx): return None
        async def read(sender, selected, command, params):
            sent.append((command, params))
            return {"success": True, "data": {"assets": [], "total": 0,
                    "inventory_revision": params.get("inventory_revision"), "inventory_retained": True}}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = read
        server = FastMCP("ui-inventory-contract")
        register_all_tools(server, project_scoped_tools=False)
        server.enable(components={"tool"})
        tool_name = "__TEST_TOOL__"

        async def main():
            # Given: actual SDK registration and a fake Unity transport boundary.
            async with Client(server) as client:
                tool = next(t for t in await client.list_tools() if t.name == tool_name)
                schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
                assert "inventory_revision" in schema["properties"]
                token = "mcp-ui-v1_" + "a" * 32
                # When: a caller continues a read-only inventory.
                result = await client.call_tool(tool_name, {"action": "list", "path": "Assets/UI",
                        "page_size": 20, "page_number": 2, "inventory_revision": token})
                # Then: the exact revision and page reach the read bridge and survive the response.
                assert result.structured_content["success"], result
                assert sent == [(tool_name, {"action": "list", "path": "Assets/UI", "pageSize": 20,
                                 "pageNumber": 2, "inventory_revision": token})]
                assert result.structured_content["data"]["inventory_revision"] == token
                for invalid in ({"inventory_revision": "bad"}, {"inventory_revision": True},
                                {"inventory_revision": 12}, {"inventory_revision": token + "\n"},
                                {"action": "ping", "inventory_revision": token}):
                    count = len(sent)
                    try:
                        rejected = await client.call_tool(tool_name, {"action": "list", **invalid})
                    except Exception as exc:
                        assert type(exc).__name__ in ("ToolError", "MCPError", "ValidationError"), exc
                    else:
                        assert rejected.is_error or not rejected.structured_content["success"], rejected
                    assert len(sent) == count
        anyio.run(main)
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program.replace("__TEST_TOOL__", tool_name)],
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
