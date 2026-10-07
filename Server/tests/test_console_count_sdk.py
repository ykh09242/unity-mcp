"""Actual SDK console count semantics, isolated from legacy collection stubs."""

import os
import subprocess
import sys
import textwrap


def test_console_all_and_defaults_at_actual_sdk_boundary():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module=importlib.import_module("services.tools.read_console")
        sent=[]
        async def instance(ctx): return None
        async def send(fn, instance, command, params):
            sent.append(params)
            return {"success":True,"data":{"lines":[]}}
        module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send
        server=FastMCP("console-count-contract")
        wrapped=log_execution("read_console","Tool")(module.read_console)
        server.tool(name="read_console")(telemetry_tool("read_console")(wrapped))
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    for value, expected in (("all",None),("*",None),(" ALL ",None),(None,10),(0,0),("5",5)):
                        result=await client.call_tool("read_console",{"count":value})
                        assert sent[-1]["count"]==expected
                        assert result.structured_content["success"] is True
                    await client.call_tool("read_console",{})
                    assert sent[-1]["count"]==10
                    await client.call_tool("read_console",{"count":"all","page_size":2,"cursor":0})
                    assert sent[-1]["count"] is None and sent[-1]["pageSize"]==2 and sent[-1]["cursor"]==0
                    await client.call_tool("read_console",{"action":"clear"})
                    assert sent[-1]["action"]=="clear" and sent[-1]["count"] is None
            print("real SDK console all/default/null/zero/paging controls passed")
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
    assert "real SDK console all/default/null/zero/paging controls passed" in result.stdout
