"""Camera request contracts through the real SDK, isolated from legacy stubs."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_camera_rejects_invalid_explicit_inputs_at_sdk_boundary():
    code = textwrap.dedent("""
        import asyncio, importlib, json
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module = importlib.import_module("services.tools.manage_camera")
        sent, routed = [], []
        async def instance(ctx):
            routed.append(True)
            return None
        async def send(fn, instance, command, params):
            sent.append(params)
            return {"success": False, "message": "fixture failure"}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = send
        server = FastMCP("camera-contract")
        wrapped = log_execution("manage_camera", "Tool")(module.manage_camera)
        server.tool(name="manage_camera")(telemetry_tool("manage_camera")(wrapped))
        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    for malformed in ("[]", "null", "7", "true", '{"broken":'):
                        before = (len(sent), len(routed))
                        result = await client.call_tool("manage_camera", {"action": "create_camera", "properties": malformed})
                        assert result.structured_content["success"] is False
                        assert "properties" in result.structured_content["message"]
                        assert (len(sent), len(routed)) == before
                    for action in ("screenshot", "screenshot_multiview"):
                        for field in ("screenshot_super_size", "max_resolution", "orbit_angles", "include_image"):
                            before = (len(sent), len(routed))
                            result = await client.call_tool("manage_camera", {"action": action, field: "garbage"})
                            assert result.structured_content["success"] is False
                            assert field in result.structured_content["message"]
                            assert (len(sent), len(routed)) == before
                    result = await client.call_tool("manage_camera", {"action": "create_camera", "properties": '{"enabled":false,"priority":0,"follow":null}'})
                    assert json.loads(sent[-1]["properties"]) == {"enabled": False, "priority": 0, "follow": None}
                    assert result.structured_content == {"success": False, "message": "fixture failure"}
                    for properties in (None, {}):
                        await client.call_tool("manage_camera", {"action": "create_camera", "properties": properties})
                        assert sent[-1].get("properties") == properties
                    for field, wire in (("screenshot_super_size", "superSize"), ("max_resolution", "maxResolution"), ("orbit_angles", "orbitAngles")):
                        for value in (None, 2, "2"):
                            await client.call_tool("manage_camera", {"action": "screenshot", field: value})
                            if value is None: assert wire not in sent[-1]
                            else: assert sent[-1][wire] == 2
                    for value, expected in ((None, None), (False, False), ("false", False), ("0", False), (True, True), ("yes", True)):
                        await client.call_tool("manage_camera", {"action": "screenshot", "include_image": value})
                        if expected is None: assert "includeImage" not in sent[-1]
                        else: assert sent[-1]["includeImage"] is expected
                    await client.call_tool("manage_camera", {"action": "ping", "max_resolution": "garbage"})
                    assert sent[-1] == {"action": "ping"}
            print("real SDK camera input contracts passed")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK camera input contracts passed" in result.stdout
