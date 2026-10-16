"""Real SDK publication and transport envelopes for bounded visual-tree reads."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_tree_budgets_at_actual_sdk_boundary(tmp_path):
    program = textwrap.dedent(r"""
        import asyncio
        import importlib
        import json
        from fastmcp import Client, FastMCP
        from mcp.types import CallToolResult
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        from models.response_limits import response_size, bounded_json_text, MAX_RESPONSE_BYTES, MAX_RESPONSE_DEPTH, MAX_RESPONSE_NODES

        module = importlib.import_module("services.tools.manage_ui")
        sent = []
        # Synthetic cap-shaped wire evidence; native output is checked separately.
        root = {"type": "VisualElement", "name": "root", "classes": []}
        parent = root
        for i in range(24):
            child = {"type": "VisualElement", "name": "child", "classes": []}
            parent["children"] = [child]
            parent = child
        root["children"].extend({"type": "VisualElement", "name": "x", "classes": ["c"] * 64} for _ in range(975))
        root["text"] = '\\"\U0001F680' * 40000
        envelope = {"success": True, "data": {"tree": root, "traversal": {
            "max_depth": 24, "max_nodes": 1000, "max_evaluations": 20000,
            "max_characters": 262144, "returned_nodes": 1000,
            "evaluations": 20000, "characters": 262144, "complete": False,
            "truncated": True, "reasons": ["max_characters"]}}}
        assert response_size(envelope) is not None
        async def instance(ctx): return None
        async def send(func, instance, command, params, **kwargs):
            sent.append(dict(params))
            return envelope
        async def unexpected(*args, **kwargs): raise AssertionError("tree read used mutation transport")
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = send
        module.send_mutation = unexpected
        server = FastMCP("ui-tree-contract")
        server.tool(name="manage_ui")(telemetry_tool("manage_ui")(log_execution("manage_ui", "Tool")(module.manage_ui)))

        async def main():
            for protocol in ("2026-07-28", "legacy"):
                async with Client(server, mode=protocol) as client:
                    tool = next(tool for tool in await client.list_tools() if tool.name == "manage_ui")
                    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
                    for key, low, high in (("max_depth", 0, 24), ("max_nodes", 1, 1000),
                                            ("max_evaluations", 1, 20000), ("max_characters", 1, 262144)):
                        prop = schema["properties"][key]
                        integer = next((part for part in prop.get("anyOf", [prop]) if part.get("type") == "integer"), None)
                        assert integer and integer["minimum"] == low and integer["maximum"] == high, prop
                        assert "get_visual_tree" in prop.get("description", ""), prop
                        assert key not in schema.get("required", [])
                        for value in (low - 1, high + 1, True, 1.5, [], {}):
                            before = len(sent)
                            try: await client.call_tool("manage_ui", {"action": "get_visual_tree", "target": "123", key: value})
                            except Exception: pass
                            else: raise AssertionError((key, value, "invalid budget accepted"))
                            assert len(sent) == before
                    for budgets in ({}, {"max_depth": None}, {"max_depth": 0, "max_nodes": 1000, "max_evaluations": 20000, "max_characters": 262144}):
                        result = await client.call_tool("manage_ui", {"action": "get_visual_tree", "target": "123", **budgets})
                        assert result.structured_content == envelope
                        assert ("maxDepth" in sent[-1]) == (budgets.get("max_depth") is not None)
                        if budgets.get("max_depth") == 0: assert sent[-1]["maxDepth"] == 0
                        wire = CallToolResult(content=result.content, structuredContent=result.structured_content,
                                              isError=result.is_error, _meta=result.meta).model_dump(mode="json", exclude_none=True)
                        assert response_size(wire) is not None
                        text = json.dumps(wire, ensure_ascii=False)
                        assert bounded_json_text(text, max_bytes=MAX_RESPONSE_BYTES, max_depth=MAX_RESPONSE_DEPTH, max_nodes=MAX_RESPONSE_NODES) is not None
            assert len(sent) == 6
            print("UI tree SDK contracts passed")
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
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UI tree SDK contracts passed" in result.stdout
