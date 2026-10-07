"""Profiler wire compatibility and failures through production-wrapped SDK registration."""

import os
import subprocess
import sys
import textwrap


def test_profiler_defaults_and_failure_at_actual_sdk_boundary():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module=importlib.import_module("services.tools.manage_profiler")
        sent=[]
        async def instance(ctx): return None
        async def send(fn, instance, command, params):
            sent.append(params)
            return {"success":False,"error":"fixture profiler failure","data":{"reason":"preserved"}}
        module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send
        server=FastMCP("profiler-contract")
        wrapped=log_execution("manage_profiler","Tool")(module.manage_profiler)
        server.tool(name="manage_profiler")(telemetry_tool("manage_profiler")(wrapped))
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    for args in ({"action":"profiler_start"},{"action":"profiler_start","log_file":None,"enable_callstacks":None},
                                 {"action":"profiler_start","enable_callstacks":False},
                                 {"action":"profiler_set_areas","areas":{"CPU":True,"Memory":False}},
                                 {"action":"get_counters","category":"Render","counters":["first","first"]},
                                 {"action":"frame_debugger_get_events","page_size":0,"cursor":0}):
                        result=await client.call_tool("manage_profiler",args)
                        assert sent[-1]=={key:value for key,value in args.items() if value is not None}
                        assert result.structured_content=={"success":False,"error":"fixture profiler failure","data":{"reason":"preserved"}}
            print("real SDK profiler optional/null/false/paging/failure controls passed")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK profiler optional/null/false/paging/failure controls passed" in result.stdout
