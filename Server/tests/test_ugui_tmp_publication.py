"""Actual installed SDK publication with controlled fake dispatch only."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

from .test_ugui_tmp_layout_diagnostics import sample


def test_actual_sdk_modes_closed_read_only_tmp_boundary(tmp_path):
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(sample()))
    program = textwrap.dedent(r"""
        import asyncio,importlib,json,os
        from pathlib import Path
        from fastmcp import FastMCP,Client
        from models.response_limits import response_size
        from services.registry import get_registered_tools
        module=importlib.import_module("services.tools.manage_ugui")
        payload=json.loads(Path(os.environ["UI08_PAYLOAD"]).read_text())
        calls=[]; reply={"success":True,"data":payload}
        async def preflight(*a,**k): return None
        async def instance(*a,**k): return None
        async def send(*a,**k): calls.append(a[3]); return reply
        async def mutation(*a,**k): raise AssertionError("TMP diagnosis mutated")
        module.preflight=preflight;module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send;module.send_mutation=mutation
        entry=next(e for e in get_registered_tools() if e["name"]=="manage_ugui")
        server=FastMCP("ui08")
        server.tool(name="manage_ugui",description=entry["description"],**entry["kwargs"])(module.manage_ugui)
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    tool=next(t for t in await client.list_tools() if t.name=="manage_ugui")
                    schema=tool.input_schema if hasattr(tool,"input_schema") else tool.inputSchema
                    assert "diagnose_text" in schema["properties"]["action"]["enum"]
                    for key in ("max_depth","max_evaluations","max_characters"):
                        assert "diagnose_text" in schema["properties"][key]["description"]
                    for invalid in ({"max_nodes":True},{"max_depth":25},{"max_evaluations":0},{"max_characters":-1},{"resolutions":[{"width":800,"height":600}]},{"camera":"View"},{"properties":{"text":"PRIVATE_SENTINEL"}}):
                        before=len(calls)
                        try: result=await client.call_tool("manage_ugui",{"action":"diagnose_text","target":"Root",**invalid})
                        except Exception: pass
                        else: assert result.is_error or result.structured_content.get("success") is False
                        assert len(calls)==before
                    result=await client.call_tool("manage_ugui",{"action":"diagnose_text","target":"Root"})
                    assert result.structured_content=={"success":True,"data":payload}
                    assert calls[-1]=={"action":"diagnose_text","target":"Root","include_inactive":False,"max_nodes":200,"max_depth":10,"max_evaluations":20000,"max_characters":65536}
                    assert response_size(result.structured_content) is not None
                    reply.clear();reply.update(success=True,data={"PRIVATE_SENTINEL":"old"})
                    result=await client.call_tool("manage_ugui",{"action":"diagnose_text","target":"Root"})
                    assert result.structured_content=={"success":False,"message":"unsupported_text_response"}
                    assert "PRIVATE_SENTINEL" not in str(result)
                    reply.clear();reply.update(success=True,data=payload)
                    server.disable(tags={"group:ui"},components={"tool"})
                    assert "manage_ugui" not in {t.name for t in await client.list_tools()}
                    before=len(calls)
                    try: await client.call_tool("manage_ugui",{"action":"diagnose_text","target":"Root"})
                    except Exception: pass
                    else: raise AssertionError("disabled UI group unexpectedly callable")
                    assert len(calls)==before
                    server.enable(tags={"group:ui"},components={"tool"})
                    print("UI08_NORMALIZED_SDK",mode)
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "UI08_PAYLOAD": str(payload),
            "APPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
