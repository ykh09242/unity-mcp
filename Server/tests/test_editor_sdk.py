"""Editor control parameters and native result shapes through the actual MCP SDK."""

import os
import subprocess
import sys
import textwrap


def test_editor_controls_at_actual_sdk_boundary():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        from services.registry import get_registered_tools
        module = importlib.import_module("services.tools.manage_editor")
        sent=[]
        paused=False
        async def instance(ctx): return "fixture-instance"
        async def send(fn, instance, command, params):
            global paused
            assert instance=="fixture-instance" and command=="manage_editor"
            sent.append(params)
            if params["action"]=="pause":
                paused=not paused
                return {"success":True,"message":"Game paused." if paused else "Game resumed."}
            if params.get("toolName")=="-999":
                return {"success":False,"error":"Cannot directly set tool to '-999'. It might be None, Custom, or invalid.","data":{"writes":0}}
            return {"success":True,"message":"Fixture outcome","data":{"value":0}}
        module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send
        module.record_tool_usage=lambda *args: None
        server=FastMCP("editor-contract")
        definition=next(tool for tool in get_registered_tools() if tool["name"]=="manage_editor")
        server.tool(name="manage_editor",description=definition["description"],**definition["kwargs"])(telemetry_tool("manage_editor")(log_execution("manage_editor","Tool")(module.manage_editor)))
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    tool=next(tool for tool in await client.list_tools() if tool.name=="manage_editor")
                    assert "pause toggles" in tool.description
                    assert "pause" in tool.input_schema["properties"]["action"]["enum"]
                    for extra in ({},{"tool_name":None},{"tool_name":"Move"},{"tool_name":"1"},{"tool_name":"-999"}):
                        result=await client.call_tool("manage_editor",{"action":"set_active_tool",**extra})
                        response=result.structured_content
                        assert sent[-1]=={"action":"set_active_tool",**({"toolName":extra["tool_name"]} if extra.get("tool_name") is not None else {})}
                        if extra.get("tool_name")=="-999":
                            assert response["success"] is False and response["data"]=={"writes":0}
                            assert "-999" in response["error"]
                        else: assert response["success"] is True and response["data"]["value"]==0
                    for message in ("Game paused.","Game resumed."):
                        result=await client.call_tool("manage_editor",{"action":"pause"})
                        assert result.structured_content["message"]==message
                        assert sent[-1]=={"action":"pause"}
                    before=len(sent)
                    for action in ("telemetry_status","telemetry_ping"):
                        result=await client.call_tool("manage_editor",{"action":action})
                        assert result.structured_content["success"] is True
                    assert len(sent)==before
            print("real SDK editor control contracts passed in both protocol modes")
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
    assert "real SDK editor control contracts passed" in result.stdout
