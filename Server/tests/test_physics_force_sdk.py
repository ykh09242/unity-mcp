"""Real SDK wire controls, separate from legacy collection stubs."""
import os
import subprocess
import sys
import textwrap


def test_physics_force_shape_and_failure_at_actual_sdk_boundary():
    code = textwrap.dedent('''
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module=importlib.import_module("services.tools.manage_physics")
        sent=[]
        async def instance(ctx): return None
        async def send(fn, instance, command, params):
            sent.append(params)
            return {"success":False,"error":"invalid torque","data":{"target":params["target"]}}
        module.get_unity_instance_from_context=instance
        module.send_with_unity_instance=send
        server=FastMCP("physics-contract")
        wrapped=log_execution("manage_physics","Tool")(module.manage_physics)
        server.tool(name="manage_physics")(telemetry_tool("manage_physics")(wrapped))
        async def main():
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    for dimension, force, torque in (("2d",[1,2],[3]),("3d",[1,2,3],[4,5,6])):
                        for selector in (None,"by_name","by_path"):
                            args={"action":"apply_force","target":"123","dimension":dimension,"force":force,"torque":torque}
                            if selector is not None: args["search_method"]=selector
                            result=await client.call_tool("manage_physics",args)
                            assert sent[-1]["force"]==force and sent[-1]["torque"]==torque
                            if selector is None: assert "search_method" not in sent[-1]
                            else: assert sent[-1]["search_method"]==selector
                            assert result.structured_content=={"success":False,"error":"invalid torque","data":{"target":"123"}}
            print("real SDK physics force contracts passed")
        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                            env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK physics force contracts passed" in result.stdout
