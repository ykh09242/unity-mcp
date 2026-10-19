"""SPEC11 audio extension through actual FastMCP schema and transport publication."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_audio_diagnostics_policy_sdk_boundary(tmp_path):
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from services.registry import get_registered_tools
        module = importlib.import_module("services.tools.manage_audio")
        sent = []
        async def instance(ctx): return "Owned@audio"
        async def read(fn, selected, command, params):
            sent.append(("read", selected, params))
            return {"success": True, "data": {"mode": "read"}}
        async def write(ctx, selected, command, params):
            sent.append(("write", selected, params))
            return {"success": True, "data": {"mode": "write"}}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = read
        module.send_mutation = write
        entry = next(t for t in get_registered_tools() if t["name"] == "manage_audio")
        server = FastMCP("audio-diagnostics-policy")
        server.tool(name=entry["name"], **entry["kwargs"])(entry["func"])
        async def main():
            async with Client(server) as client:
                tool = next(t for t in await client.list_tools() if t.name == "manage_audio")
                schema = tool.inputSchema
                assert {"action", "target"} == set(schema["required"])
                assert "diagnose" in schema["properties"]["action"]["enum"]
                assert not any(part.get("type") == "null" for part in schema["properties"]["settings"].get("anyOf", []))
                for args, route in (
                    ({"action": "diagnose", "target": 42}, "read"),
                    ({"action": "get_routing", "target": "Music", "mixer_path": "Assets/Owned.mixer"}, "read"),
                    ({"action": "set_routing", "target": 42, "mixer_path": "Assets/Owned.mixer", "group_id": "24300002"}, "write"),
                    ({"action": "set_routing", "target": 42, "clear_routing": True}, "write"),
                    ({"action": "read_import_settings", "target": "Assets/Owned.wav", "platform": "Standalone"}, "read"),
                    ({"action": "apply_import_settings", "target": "Assets/Owned.wav", "platform": "Standalone", "settings": {"quality": .42, "load_type": "CompressedInMemory"}}, "write"),
                    ({"action": "apply_import_settings", "target": "Assets/Owned.wav", "platform": "Standalone", "clear_override": True}, "write"),
                ):
                    result = await client.call_tool("manage_audio", args)
                    assert result.structured_content["success"], result
                    assert sent[-1] == (route, "Owned@audio", args), sent[-1]
                for invalid in (
                    {"action": "apply_import_settings", "target": "Assets/Owned.wav", "platform": "Standalone", "clear_override": True, "settings": None},
                    {"action": "apply_import_settings", "target": "Assets/Owned.wav", "platform": "Standalone", "clear_override": True, "settings": "invalid"},
                    {"action": "set_routing", "target": 42},
                    {"action": "set_routing", "target": 42, "clear_routing": "true"},
                    {"action": "diagnose", "target": 42, "settings": {"quality": .5}},
                    {"action": "apply_import_settings", "target": "Assets/Owned.wav", "settings": {"quality": 2}},
                    {"action": "apply_import_settings", "target": "Assets/Owned.wav", "settings": {"force_to_mono": "true"}},
                    {"action": "apply_import_settings", "target": "Assets/Owned.wav", "settings": {"unsupported": True}},
                ):
                    before = len(sent)
                    try: result = await client.call_tool("manage_audio", invalid)
                    except Exception as error:
                        assert type(error).__name__ in ("ToolError", "MCPError", "ValidationError"), error
                    else: assert result.is_error or not result.structured_content["success"], result
                    assert len(sent) == before
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
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
