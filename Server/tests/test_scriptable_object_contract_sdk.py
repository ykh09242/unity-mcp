"""Boolean controls through real FastMCP registration, isolated from test SDK stubs."""
import os
import subprocess
import sys
import textwrap


def test_scriptable_object_boolean_controls_at_actual_sdk_boundary():
    code = textwrap.dedent('''
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module = importlib.import_module("services.tools.manage_scriptable_object")
        calls = []
        async def instance(ctx):
            calls.append("instance")
            return None
        async def send(fn, instance, command, params):
            calls.append(params)
            return {"success": False, "message": "controlled Unity failure"}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = send
        server = FastMCP("scriptable-object-contract")
        wrapped = log_execution("manage_scriptable_object", "Tool")(module.manage_scriptable_object)
        server.tool(name="manage_scriptable_object")(telemetry_tool("manage_scriptable_object")(wrapped))
        async def main():
            for protocol in ("2026-07-28", "legacy"):
                async with Client(server, mode=protocol) as client:
                    for flag, wire in (("dry_run", "dryRun"), ("overwrite", "overwrite")):
                        for invalid in ("garbage", "tru", ""):
                            before = len(calls)
                            result = await client.call_tool("manage_scriptable_object", {"action": "modify", flag: invalid})
                            assert result.structured_content["success"] is False
                            assert flag in result.structured_content["message"]
                            assert len(calls) == before
                        for value, expected in ((None, None), (True, True), (False, False),
                                                ("true", True), ("false", False), ("yes", True), ("0", False)):
                            result = await client.call_tool("manage_scriptable_object", {
                                "action": "modify", flag: value, "target": '{"path":"Assets/Fixture.asset"}',
                                "patches": '[{"path":"intValue","value":0}]'})
                            assert result.structured_content == {"success": False, "message": "controlled Unity failure"}
                            assert calls[-1]["target"] == {"path": "Assets/Fixture.asset"}
                            assert calls[-1]["patches"] == [{"path": "intValue", "value": 0}]
                            assert calls[-1].get(wire) is expected
                            assert (wire in calls[-1]) == (expected is not None)
            print("real SDK ScriptableObject boolean controls passed")
        asyncio.run(main())
    ''')
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=30,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK ScriptableObject boolean controls passed" in result.stdout
