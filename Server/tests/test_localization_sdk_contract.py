"""Localization read/correction publication through actual registered FastMCP clients."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_localization_sdk_read_and_explicit_mutation_contract(tmp_path):
    program = textwrap.dedent("""
        import anyio, importlib, importlib.util
        from fastmcp import FastMCP, Client
        from services.registry import get_registered_tools
        assert importlib.util.find_spec("services.tools.manage_localization") is not None, "Localization public tool is absent"
        module = importlib.import_module("services.tools.manage_localization")
        sent = []
        async def instance(ctx): return "Loc@owned"
        async def read(fn, selected, command, params):
            sent.append(("read", selected, command, params))
            return {"success": True, "data": {"revision": "a" * 64}}
        async def write(ctx, selected, command, params):
            sent.append(("write", selected, command, params))
            return {"success": True, "data": {"persisted": True}}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = read
        module.send_mutation = write
        entry = next(t for t in get_registered_tools() if t["name"] == "manage_localization")
        server = FastMCP("localization-contract")
        server.tool(name=entry["name"], **entry["kwargs"])(entry["func"])
        async def main():
            async with Client(server) as client:
                tool = next(t for t in await client.list_tools() if t.name == "manage_localization")
                schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
                assert schema["required"] == ["action"]
                requests = (
                    ({"action": "capabilities"}, "read"),
                    ({"action": "list_locales", "offset": 0, "limit": 10}, "read"),
                    ({"action": "list_tables"}, "read"),
                    ({"action": "inspect", "table_paths": [f"Assets/locale{i}.asset" for i in range(40)]}, "read"),
                    ({"action": "set_string_entry", "table_path": "Assets/fr.asset", "key_id": "101", "value": "x" * 16000, "expected_revision": "a"*64}, "write"),
                    ({"action": "set_fallbacks", "locale_path": "Assets/fr.asset", "fallback_locale_paths": [f"Assets/locale{i}.asset" for i in range(12)], "expected_revision": "a"*64}, "write"),
                    ({"action": "inspect", "table_paths": ["Assets/en.asset", "Assets/fr.asset"], "argument_names": ["player"]}, "read"),
                    ({"action": "set_string_entry", "table_path": "Assets/fr.asset", "key_id": "101", "value": "Bonjour {player}", "expected_revision": "a"*64}, "write"),
                    ({"action": "set_asset_entry", "table_path": "Assets/fr.asset", "key_id": "201", "asset_path": "Assets/Icon.png", "expected_revision": "a"*64}, "write"),
                    ({"action": "set_fallbacks", "locale_path": "Assets/fr.asset", "fallback_locale_paths": [], "expected_revision": "a"*64}, "write"),
                )
                for args, route in requests:
                    result = await client.call_tool("manage_localization", args)
                    assert result.structured_content["success"], result
                    assert sent[-1] == (route, "Loc@owned", "manage_localization", args), sent[-1]
                for args in (
                    {"action": "inspect", "table_paths": []},
                    {"action": "inspect", "table_paths": ["Assets/../bad.asset"]},
                    {"action": "set_string_entry", "table_path": "Assets/fr.asset", "key_id": "101", "value": "Changed"},
                    {"action": "set_string_entry", "table_path": "Assets/fr.asset", "key_id": "101", "value": "Changed", "expected_revision": "a"*64, "is_smart": "true"},
                    {"action": "set_fallbacks", "locale_path": "Assets/fr.asset", "expected_revision": "a"*64},
                    {"action": "list_tables", "limit": True},
                ):
                    before = len(sent)
                    try: result = await client.call_tool("manage_localization", args)
                    except Exception as error:
                        assert type(error).__name__ in ("ToolError", "MCPError", "ValidationError"), error
                    else: assert not result.structured_content["success"] or result.is_error, result
                    assert len(sent) == before
        anyio.run(main)
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        capture_output=True,
        text=True,
        timeout=45,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "APPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
