"""UI mutation selectors through the actual SDK with controlled transport only."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_selectors_at_actual_sdk_boundary(tmp_path):
    program = textwrap.dedent(r"""
        import asyncio
        import importlib
        from fastmcp import Client, FastMCP
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool

        module = importlib.import_module("services.tools.manage_ui")
        sent = []
        failure = {"success": False, "error": "Controlled native selector refusal",
                   "data": {"modified": False, "count": 0, "reference": None}}
        async def instance(ctx): return None
        async def send(ctx, instance, command, params, **kwargs):
            assert command == "manage_ui"
            sent.append(dict(params))
            return dict(failure)
        async def unexpected(*args, **kwargs):
            raise AssertionError("selector mutation used the read transport")
        module.get_unity_instance_from_context = instance
        module.send_mutation = send
        module.send_with_unity_instance = unexpected
        server = FastMCP("ui-selector-contract")
        wrapped = log_execution("manage_ui", "Tool")(module.manage_ui)
        server.tool(name="manage_ui")(telemetry_tool("manage_ui")(wrapped))

        cases = [
            ({"element_name": "shared"}, {}),
            ({"element_name": "shared", "strict_name": False}, {"strictName": False}),
            ({"element_name": "shared", "strict_name": True}, {"strictName": True}),
            ({"element_path": ""}, {"elementPath": ""}),
            ({"element_path": "/1/0", "element_name": "shared"}, {"elementPath": "/1/0"}),
            ({"element_id": "unsaved:Fixture::Panel/name%3Ashared"},
             {"elementId": "unsaved:Fixture::Panel/name%3Ashared"}),
            ({"element_path": "/01/0", "element_name": "shared"}, {"elementPath": "/01/0"}),
            ({"element_path": "/0", "element_id": "foreign", "element_name": "shared"},
             {"elementPath": "/0", "elementId": "foreign"}),
        ]
        invalid = [{"element_path": None}, {"element_id": None},
                   {"element_path": 12}, {"element_id": []}]
        async def main():
            for protocol in ("2026-07-28", "legacy"):
                async with Client(server, mode=protocol) as client:
                    tool = next(tool for tool in await client.list_tools() if tool.name == "manage_ui")
                    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
                    for key in ("element_path", "element_id"):
                        assert schema["properties"][key]["type"] == "string", schema
                        assert key not in schema.get("required", [])
                    for key, phrase in {"element_path": "content-container", "element_id": "auto-selected", "strict_name": "UQuery"}.items():
                        assert phrase in schema["properties"][key].get("description", ""), schema["properties"][key]
                    for selectors, expected in cases:
                        result = await client.call_tool("manage_ui", {"action": "modify_visual_element",
                            "target": "123", "text": "changed", **selectors})
                        assert result.structured_content == failure
                        wire = sent[-1]
                        for key in ("elementPath", "elementId", "strictName"):
                            assert (key in wire) == (key in expected), wire
                            if key in expected: assert wire[key] == expected[key], wire
                        assert ("elementName" in wire) == ("element_name" in selectors)
                    for selectors in invalid:
                        before = len(sent)
                        try:
                            await client.call_tool("manage_ui", {"action": "modify_visual_element",
                                "target": "123", "element_name": "shared", "text": "changed", **selectors})
                        except Exception:
                            pass
                        else:
                            raise AssertionError("invalid selector reached dispatch")
                        assert len(sent) == before
            assert len(sent) == 2 * len(cases)
            print("UI selector SDK contracts passed: 16 dispatches, 8 invalid inputs refused")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "APPDATA": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UI selector SDK contracts passed" in result.stdout
