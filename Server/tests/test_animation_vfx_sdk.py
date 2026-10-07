"""Domain payload contracts through the actual SDK, isolated from legacy stubs."""

import os
import subprocess
import sys
import textwrap


def test_animation_vfx_properties_at_actual_sdk_boundary():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        modules = [importlib.import_module("services.tools.manage_"+domain) for domain in ("animation","vfx")]
        sent = []
        async def instance(ctx): return None
        async def send(fn, instance, command, params):
            sent.append((command, params))
            return {"success": False, "data": {"reason": "fixture failure", "value": params.get("properties")}}
        server = FastMCP("domain-contract")
        for module in modules:
            module.get_unity_instance_from_context = instance
            module.send_with_unity_instance = send
            name = module.__name__.split('.')[-1]
            wrapped = log_execution(name, "Tool")(getattr(module,name))
            server.tool(name=name)(telemetry_tool(name)(wrapped))
        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server,mode=mode) as client:
                    for name, action in (("manage_animation","animator_set_parameter"),("manage_vfx","particle_set_main")):
                        for malformed in ("[]","null","1",'{"broken":'):
                            before=len(sent)
                            result=await client.call_tool(name,{"action":action,"properties":malformed})
                            assert result.structured_content["success"] is False
                            assert "properties" in result.structured_content["message"]
                            assert len(sent)==before
                        result=await client.call_tool(name,{"action":action,"properties":'{"value":null,"enabled":false,"speed":0}'})
                        assert sent[-1][1]["properties"]=={"value":None,"enabled":False,"speed":0}
                        assert result.structured_content["success"] is False
                        assert result.structured_content["data"]["reason"]=="fixture failure"
                        omitted=await client.call_tool(name,{"action":action})
                        assert "properties" not in sent[-1][1]
                        assert omitted.structured_content["success"] is False
                        assert omitted.structured_content["data"]=={"reason":"fixture failure","value":None}
                        for properties in (None, {}):
                            await client.call_tool(name,{"action":action,"properties":properties})
                            assert sent[-1][1].get("properties")==properties
            print("real SDK animation/VFX payload contracts passed")
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
    assert "real SDK animation/VFX payload contracts passed" in result.stdout
