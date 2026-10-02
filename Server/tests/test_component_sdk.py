"""Exercise nullable component edits through the actual SDK in a fresh process."""
import os
import subprocess
import sys
import textwrap


def test_component_value_omission_and_null_survive_sdk():
    code = textwrap.dedent('''
        import asyncio
        import importlib
        import json
        import warnings
        from fastmcp import Client, FastMCP
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        tools = importlib.import_module("services.tools.manage_components")
        sent = []
        gates = []
        async def instance(ctx):
            return None
        async def gate(ctx, **kwargs):
            gates.append(kwargs)
            return None
        async def send(sender, target, command, params, **kwargs):
            sent.append(params)
            return {"success": True, "data": {}}
        tools.get_unity_instance_from_context = instance
        tools.preflight = gate
        tools.send_with_unity_instance = send
        server = FastMCP("component-contract")
        wrapped = log_execution("manage_components", "Tool")(tools.manage_components)
        wrapped = telemetry_tool("manage_components")(wrapped)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            server.tool(name="manage_components")(wrapped)
        assert not caught, [str(item.message) for item in caught]
        async def main():
            async with Client(server, mode="2026-07-28") as client:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    schema = (await client.list_tools())[0].input_schema
                    json.dumps(schema)
                assert not caught, [str(item.message) for item in caught]
                assert "value" not in schema.get("required", [])
                assert {"type": "null"} in schema["properties"]["value"]["anyOf"]
                base = {"action": "set_property", "target": "Player", "component_type": "Renderer", "property": "sharedMaterial"}
                missing = await client.call_tool("manage_components", base)
                assert missing.structured_content["success"] is False
                assert "value" in missing.structured_content["message"]
                assert not sent and not gates
                cleared = await client.call_tool("manage_components", {**base, "value": None})
                assert cleared.structured_content["success"] is True
                assert sent[-1]["property"] == "sharedMaterial"
                assert "value" in sent[-1] and sent[-1]["value"] is None
                json.dumps(sent[-1])
                multi = await client.call_tool("manage_components", {
                    "action": "set_property", "target": "Player", "component_type": "Renderer",
                    "properties": {"sharedMaterial": None},
                })
                assert multi.structured_content["success"] is True
                assert sent[-1]["properties"] == {"sharedMaterial": None}
                assert "value" not in sent[-1]
                for value in (False, 0, ""):
                    result = await client.call_tool("manage_components", {**base, "value": value})
                    assert result.structured_content["success"] is True
                    assert sent[-1]["value"] == value
                    assert type(sent[-1]["value"]) is type(value)
                before = len(gates), len(sent)
                invalid = await client.call_tool("manage_components", {
                    "action": "add", "target": "Player", "component_type": "Rigidbody",
                    "properties": "[object Object]",
                })
                assert invalid.structured_content["success"] is False
                assert (len(gates), len(sent)) == before
                add = await client.call_tool("manage_components", {"action": "add", "target": "Player", "component_type": "Rigidbody"})
                assert add.structured_content["success"] is True
                assert "value" not in sent[-1]
                print("real SDK component omission/null/schema passed")
        asyncio.run(main())
    ''')
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK component omission/null/schema passed" in result.stdout
