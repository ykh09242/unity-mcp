"""Exercise script editing through the real SDK independently of legacy test stubs."""
import subprocess
import os
import sys
import textwrap


def test_modern_sdk_script_edit_preserves_locator_and_replacement():
    # Given: a fresh SDK process with only the Unity wire boundary substituted.
    code = textwrap.dedent('''
        import asyncio
        import importlib
        from fastmcp import Client, FastMCP
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        tools = importlib.import_module("services.tools.script_apply_edits")
        sent = []
        async def instance(ctx):
            return None
        async def read(*args, **kwargs):
            contents = "😀x" if args[3]["name"] == "Unicode" else "name=abc"
            return {"success": True, "data": {"contents": contents}}
        async def write(ctx, target, command, params, **kwargs):
            sent.append((command, params))
            return {"success": True}
        tools.get_unity_instance_from_context = instance
        tools.send_with_unity_instance = read
        tools.send_mutation = write
        server = FastMCP("script-contract")
        wrapped = log_execution("script_apply_edits", "Tool")(tools.script_apply_edits)
        wrapped = telemetry_tool("script_apply_edits")(wrapped)
        server.tool(name="script_apply_edits")(wrapped)
        async def main():
            async with Client(server, mode="2026-07-28") as client:
                result = await client.call_tool("script_apply_edits", {
                    "name": "Foo.cs", "path": "Assets/Scripts",
                    "edits": [{"op": "regex_replace", "pattern": r"name=(\\w+)", "replacement": "$1=value"}],
                })
                assert result.structured_content["success"] is True
                command, params = sent[0]
                assert command == "manage_script"
                assert (params["name"], params["path"]) == ("Foo", "Assets/Scripts")
                assert params["edits"][0]["newText"] == "abc=value"
                assert len(params["precondition_sha256"]) == 64
                unicode_result = await client.call_tool("script_apply_edits", {
                    "name": "Unicode", "path": "Assets/Scripts",
                    "edits": [{"range": {"start": {"line": 0, "character": 2}, "end": {"line": 0, "character": 3}}, "newText": "Z"}],
                })
                assert unicode_result.structured_content["success"] is True
                span = sent[-1][1]["edits"][0]
                assert (span["startCol"], span["endCol"], span["newText"]) == (2, 3, "Z")
                print("real modern SDK script edit passed")
        asyncio.run(main())
    ''')
    # When: the client validates and invokes the production decorated Python tool.
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20,
                            env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"})
    # Then: its requested file/replacement and precondition survive the actual SDK stack.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real modern SDK script edit passed" in result.stdout


def test_modern_sdk_script_literal_formatting_and_preview():
    code = textwrap.dedent(r'''
        import asyncio
        import importlib
        from fastmcp import Client, FastMCP
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        tools = importlib.import_module("services.tools.script_apply_edits")
        sent = []
        async def instance(ctx):
            return None
        async def read(*args, **kwargs):
            return {"success": True, "data": {"contents": "middle\r\n", "sha256": "previous"}}
        async def write(ctx, target, command, params, **kwargs):
            sent.append(params)
            return {"success": True}
        tools.get_unity_instance_from_context = instance
        tools.send_with_unity_instance = read
        tools.send_mutation = write
        server = FastMCP("formatting-contract")
        wrapped = log_execution("script_apply_edits", "Tool")(tools.script_apply_edits)
        server.tool(name="script_apply_edits")(telemetry_tool("script_apply_edits")(wrapped))
        async def main():
            async with Client(server, mode="2026-07-28") as client:
                payload = '\tconst string value = @"first\nsecond";\n'
                arguments = {"name": "Foo", "path": "Assets", "edits": [{"op": "append", "text": payload}]}
                result = await client.call_tool("script_apply_edits", arguments)
                assert result.structured_content["success"] is True
                assert sent[-1]["edits"][0]["newText"] == payload
                assert len(sent[-1]["precondition_sha256"]) == 64
                preview = await client.call_tool("script_apply_edits", {**arguments, "options": {"preview": True}})
                assert preview.structured_content["success"] is True
                assert len(sent) == 1
                assert ' middle\r\n' in preview.structured_content["data"]["diff"]
                snippet = 'void Added()\n{\n\tconst string s = @"a\nb";\n}'
                structured = await client.call_tool("script_apply_edits", {
                    "name": "Foo", "path": "Assets", "edits": [{"op": "insert_method", "position": "before", "beforeMethodName": "Existing", "replacement": snippet}],
                })
                assert structured.structured_content["success"] is True
                assert sent[-1]["action"] == "edit"
                assert sent[-1]["edits"][0]["replacement"] == snippet
                assert sent[-1]["edits"][0]["beforeMethodName"] == "Existing"
                print("real SDK formatting contract passed")
        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20,
                            env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK formatting contract passed" in result.stdout
