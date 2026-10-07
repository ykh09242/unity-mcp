"""Real SDK shader CRUD/encoding and animation value contracts in fresh processes."""

import os
import subprocess
import sys
import textwrap


def test_shader_and_animation_at_actual_sdk_and_transport_boundary(tmp_path):
    code = textwrap.dedent(r"""
        import base64
        import copy
        import anyio
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from core.config import config
        from transport.plugin_hub import PluginHub
        config.transport_mode = "http"
        config.http_remote_hosted = False
        requests, failures = [], []
        checks = 0
        raw = {"success": True, "message": "Done", "data": {"count": 0, "enabled": False, "reference": None}}
        def check(condition, label):
            global checks
            checks += 1
            if not condition:
                failures.append(label)
                print("FAIL", label)
        async def controlled_send(instance, command, params, **kwargs):
            requests.append((instance, command, copy.deepcopy(params)))
            return copy.deepcopy(raw)
        PluginHub.send_command_for_instance = staticmethod(controlled_send)
        class FixtureState(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state("unity_instance", "Project@fixture")
                return await call_next(context)
        server = FastMCP("shader-animation-contracts")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        server.enable(tags={"group:vfx", "group:animation"})
        async def main():
            global raw
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    tools = {tool.name: tool for tool in await client.list_tools()}
                    check("manage_shader" in tools and "manage_animation" in tools, "registered tools " + mode)
                    for action, contents in (("create", None), ("create", ""), ("create", 'Shader "Custom/Unicode" { /*한글*/ }'), ("update", ""), ("update", "Unicode λ\u2028line\n"), ("read", "ignored"), ("delete", None)):
                        raw = {"status": "success", "result": {"success": True, "message": "Done", "data": {"count": 0, "enabled": False, "reference": None}}}
                        payload = {"action": action, "name": "Fixture", "path": "Assets/Shaders", "contents": contents}
                        result = await client.call_tool("manage_shader", payload)
                        check(result.structured_content == raw["result"], "shader success values " + action + mode)
                        instance, command, wire = requests[-1]
                        expected = {"action": action, "name": "Fixture", "path": "Assets/Shaders"}
                        if contents is not None:
                            if action in ("create", "update"):
                                expected.update(encodedContents=base64.b64encode(contents.encode("utf-8")).decode("ascii"), contentsEncoded=True)
                            else:
                                expected["contents"] = contents
                        check((instance, command, wire) == ("Project@fixture", "manage_shader", expected), "shader wire " + action + mode)
                    for text in ("", "Shader Ω\n" + "x" * 10001):
                        raw = {"success": True, "message": "Read", "data": {"contents": "original", "encodedContents": base64.b64encode(text.encode("utf-8")).decode("ascii"), "contentsEncoded": True, "path": "Assets/Shaders/Fixture.shader"}}
                        result = await client.call_tool("manage_shader", {"action": "read", "name": "Fixture", "path": "Assets/Shaders"})
                        check(result.structured_content == {"success": True, "message": "Read", "data": {"contents": text, "path": "Assets/Shaders/Fixture.shader"}}, "encoded response precedence " + mode)
                    raw = {"status": "success", "result": {"success": True, "message": "Deleted"}}
                    result = await client.call_tool("manage_shader", {"action": "delete", "name": "Fixture", "path": "Assets/Shaders"})
                    check(result.structured_content == {"success": True, "message": "Deleted", "data": None}, "native omitted data remains success " + mode)
                    for tool, payload in (("manage_shader", {"action": "update", "name": "Fixture", "path": "Assets/Shaders"}), ("manage_animation", {"action": "controller_add_blend_tree_child"})):
                        raw = {"status": "success", "result": {"success": False, "code": "rejected", "error": "Native validation rejected", "data": {"count": 0, "enabled": False, "reference": None}}}
                        result = await client.call_tool(tool, payload)
                        check(result.structured_content == raw["result"], "native diagnostics " + tool + mode)
                    for payload in ({"action": "unknown", "name": "Fixture", "path": "Assets"}, {"action": "read", "name": 1, "path": "Assets"}):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool("manage_shader", payload)
                        except ToolError:
                            rejected = True
                        check(rejected and len(requests) == before, "shader schema rejects before transport " + mode)
                    raw = {"success": True, "data": {"contentsEncoded": True, "encodedContents": "/w=="}}
                    result = await client.call_tool("manage_shader", {"action": "read", "name": "Fixture", "path": "Assets"})
                    check(result.structured_content["success"] is False and "Python error managing shader" in result.structured_content["message"], "malformed consumer encoding fails " + mode)
                    raw = "not an object"
                    for tool, payload in (("manage_shader", {"action": "read", "name": "Fixture", "path": "Assets"}), ("manage_animation", {"action": "animator_get_info"})):
                        result = await client.call_tool(tool, payload)
                        check(result.structured_content == {"success": False, "message": raw}, "malformed transport object " + tool + mode)
                    for properties in ("[]", "null", "not json", "", "[object Object]"):
                        before = len(requests)
                        result = await client.call_tool("manage_animation", {"action": "controller_create_blend_tree_1d", "properties": properties})
                        check(result.structured_content["success"] is False and len(requests) == before, "animation malformed properties local " + mode)
                    for action in ("unknown", "controller_unknown", "animator_unknown", "clip_unknown"):
                        before = len(requests)
                        result = await client.call_tool("manage_animation", {"action": action})
                        check(result.structured_content["success"] is False and len(requests) == before, "unknown animation action local " + mode)
                    raw = {"success": True, "data": {"count": 0, "enabled": False, "reference": None}}
                    for action, properties in (("CONTROLLER_CREATE_BLEND_TREE_1D", {"stateName": "Walk", "blendParameter": "Speed", "layerIndex": 0}), ("controller_create_blend_tree_2d", {"stateName": "Walk", "blendParameterX": "X", "blendParameterY": "Y", "blendType": None, "layerIndex": 0}), ("controller_add_blend_tree_child", {"stateName": "Walk", "threshold": 0, "position": [0, 0], "timeScale": 0}), ("animator_set_enabled", {"enabled": False}), ("animator_set_parameter", {"value": False}), ("animator_set_speed", {"speed": 0})):
                        payload = {"action": action, "target": "0", "search_method": "by_name", "controller_path": "Assets/Fixture.controller", "clip_path": "Assets/Fixture.anim", "properties": properties}
                        result = await client.call_tool("manage_animation", payload)
                        check(result.structured_content == raw, "animation response false/zero/null " + mode)
                        check(requests[-1] == ("Project@fixture", "manage_animation", {"action": action.lower(), "target": "0", "searchMethod": "by_name", "controllerPath": "Assets/Fixture.controller", "clipPath": "Assets/Fixture.anim", "properties": properties}), "animation normalized wire " + mode)
                    for properties in (None, {}, '{"enabled":false,"value":0,"reference":null}'):
                        result = await client.call_tool("manage_animation", {"action": "animator_get_info", "properties": properties})
                        wire = requests[-1][2]
                        check(("properties" not in wire) if properties is None else wire["properties"] == ({} if properties == {} else {"enabled": False, "value": 0, "reference": None}), "animation properties omission/object string " + mode)
            print(f"actual shader/animation SDK checks={checks} failures={len(failures)}")
            assert not failures, failures
        anyio.run(main)
    """)
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "actual shader/animation SDK checks=" in result.stdout
