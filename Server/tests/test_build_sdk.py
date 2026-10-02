"""Actual SDK settings-value and pending/terminal build response contracts."""
import os
import subprocess
import sys
import textwrap


def test_build_settings_and_status_at_actual_sdk_boundary():
    code = textwrap.dedent('''
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        from services.custom_tool_service import CustomToolService
        from models.models import ToolDefinitionModel
        module = importlib.import_module("services.tools.manage_build")
        execute = importlib.import_module("services.tools.execute_custom_tool")
        poll = importlib.import_module("services.custom_tool_service")
        sent=[]
        async def instance(ctx): return "fixture-instance"
        async def send(fn, instance, command, params, **kwargs):
            sent.append(params)
            if params["action"]=="settings": return {"success":True,"data":{"value":params.get("value","OLD")}}
            if params["action"]=="batch": return {"success":True,"_mcp_status":"pending","data":{"job_id":"batch-new","total":2}}
            state=params.get("job_id")
            if state is None: return {"success":True,"data":{"job_id":"build-first","result":"succeeded"}}
            if state=="batch-new": return {"success":True,"data":{"job_id":state,"result":"succeeded","completed":2}}
            if state in ("pending","building"):
                return {"success":True,"_mcp_status":"pending","_mcp_poll_interval":10.0,"data":{"job_id":state,"result":state}}
            if state=="absent": return {"success":False,"error":"No job found","data":{"job_id":state}}
            return {"success":True,"data":{"job_id":state,"result":state}}
        module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send
        server=FastMCP("build-contract")
        server.tool(name="manage_build")(telemetry_tool("manage_build")(log_execution("manage_build","Tool")(module.manage_build)))
        service=CustomToolService(server)
        # Same metadata emitted from ManageBuild's production attribute; the managed proof exports it.
        definition=ToolDefinitionModel(name="manage_build",requires_polling=True,poll_action="status",max_poll_seconds=1800)
        service.register_global_tools([definition])
        assert "manage_build" not in service._global_tools  # Existing built-in remains untouched.
        service._register_project_tools("fixture-project",[definition])
        execute.get_unity_instance_from_context=instance
        execute.resolve_project_id_for_unity_instance=lambda instance:"fixture-project"
        poll.send_with_unity_instance=send
        async def no_sleep(seconds): pass
        poll.asyncio.sleep=no_sleep
        server.tool(name="execute_custom_tool")(execute.execute_custom_tool)
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    for extra in ({},{"value":None},{"value":""},{"value":"NEW"}):
                        result=await client.call_tool("manage_build",{"action":"settings","property":"defines",**extra})
                        assert result.structured_content["success"] is True
                        if extra.get("value") is None: assert "value" not in sent[-1]
                        else: assert sent[-1]["value"]==extra["value"]
                    for state in ("pending","building","succeeded","failed","cancelled","absent"):
                        result=await client.call_tool("manage_build",{"action":"status","job_id":state})
                        response=result.structured_content
                        assert sent[-1]=={"action":"status","job_id":state}
                        if state in ("pending","building"): assert response["_mcp_status"]=="pending"
                        elif state=="absent": assert response["success"] is False and response["error"]=="No job found"
                        else: assert "_mcp_status" not in response and response["data"]["result"]==state
                    before=len(sent)
                    result=await client.call_tool("execute_custom_tool",{"tool_name":"manage_build","parameters":{"action":"batch","targets":["windows64","linux64"]}})
                    assert result.structured_content["data"]=={"job_id":"batch-new","result":"succeeded","completed":2}
                    assert sent[before:]==[{"action":"batch","targets":["windows64","linux64"]},{"action":"status","targets":["windows64","linux64"],"job_id":"batch-new"}]
            print("real SDK build settings/status contracts passed")
        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                            env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK build settings/status contracts passed" in result.stdout
