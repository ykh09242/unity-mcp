"""Exercise optional field-list schema through real current and legacy SDKs."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_registered_console_schema_accepts_projection_and_preserves_defaults():
    source = """
        import copy, importlib
        import anyio
        from fastmcp import FastMCP, Client
        from unittest.mock import AsyncMock
        console = importlib.import_module("services.tools.read_console")
        sent = []
        body = "Full error body\\nsecond line\\n" + "x" * 1000
        native = {"type": "Error", "message": body, "file": "Assets/Fixture.cs", "line": 17, "stackTrace": "Fixture:Method()"}
        async def send(fn, instance, name, params):
            assert name == "read_console"
            sent.append(copy.deepcopy(params))
            item = {key: native[key] for key in params["fields"]} if "fields" in params else copy.deepcopy(native)
            return {"success": True, "data": [item]}
        console.get_unity_instance_from_context = AsyncMock(return_value="Fixture@owned")
        console.send_with_unity_instance = send
        server = FastMCP("console-projection-sdk")
        server.tool(name="read_console", description="console projection")(console.read_console)
        async def scenario():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    tools = await client.list_tools()
                    schema = next(tool.inputSchema for tool in tools if tool.name == "read_console")
                    assert "fields" in schema["properties"]
                    for field_input in (["type", "message"], '["type", "message"]'):
                        before = len(sent)
                        result = await client.call_tool("read_console", {"format": "json", "fields": field_input})
                        assert len(sent) == before + 1
                        assert result.structured_content["data"] == [{"type": "Error", "message": body}]
                        assert sent[-1]["fields"] == ["type", "message"]
                    result = await client.call_tool("read_console", {"format": "json", "include_stacktrace": True})
                    assert result.structured_content["data"] == [native]
                    assert "fields" not in sent[-1]
                    before = len(sent)
                    for invalid in ('[]', '["message"]', '["type", "message", "unknown"]'):
                        result = await client.call_tool("read_console", {"format": "json", "fields": invalid})
                        assert result.structured_content["success"] is False
                    assert len(sent) == before
        anyio.run(scenario)
    """
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
