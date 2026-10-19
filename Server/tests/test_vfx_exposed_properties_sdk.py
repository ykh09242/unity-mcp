"""SPEC04 exposed VFX read action at the actual registered FastMCP boundary."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_exposed_vfx_schema_read_sdk_contract(tmp_path):
    program = textwrap.dedent("""
        import anyio, importlib
        from fastmcp import FastMCP, Client
        from services.registry import get_registered_tools
        module = importlib.import_module("services.tools.manage_vfx")
        sent = []
        async def instance(ctx): return "Vfx@owned"
        async def send(fn, selected, command, params):
            sent.append((selected, command, params))
            return {"success": True, "data": {"properties": [], "override_status": "unavailable"}}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = send
        entry = next(t for t in get_registered_tools() if t["name"] == "manage_vfx")
        server = FastMCP("exposed-vfx-read")
        server.tool(name=entry["name"], **entry["kwargs"])(entry["func"])
        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    for props in (None, {}, {"parameter": "OwnedFloat", "page_size": 1, "page_number": 1,
                                           "max_curve_keys": 0, "max_gradient_keys": 0, "max_characters": 2048}):
                        args = {"action": "vfx_get_exposed_properties", "target": "42", "search_method": "by_id"}
                        if props is not None: args["properties"] = props
                        result = await client.call_tool("manage_vfx", args)
                        assert result.structured_content["success"], result
                        expected = {"action": "vfx_get_exposed_properties", "target": "42", "searchMethod": "by_id"}
                        if props is not None: expected["properties"] = props
                        assert sent[-1] == ("Vfx@owned", "manage_vfx", expected), sent[-1]
                    for props in ({"page_size": True}, {"page_number": 0}, {"page_size": 201},
                                  {"max_curve_keys": -1}, {"max_gradient_keys": 33},
                                  {"max_characters": 0}, {"parameter": None}, {"parameter": ""},
                                  {"parameter": "x" * 1025}, {"unknown": 1}, {"page_size": "1"}):
                        before = len(sent)
                        result = await client.call_tool("manage_vfx", {"action": "vfx_get_exposed_properties", "target": "42", "properties": props})
                        assert not result.structured_content["success"], result
                        assert len(sent) == before
                    before = len(sent)
                    result = await client.call_tool("manage_vfx", {"action": "vfx_get_info", "target": "42"})
                    assert result.structured_content["success"]
                    assert len(sent) == before + 1
                    assert sent[-1][2] == {"action": "vfx_get_info", "target": "42"}
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
