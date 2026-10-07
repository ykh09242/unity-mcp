"""VFX renderer contracts through fresh actual SDK/CLI transport boundaries."""

import os
import subprocess
import sys
import textwrap


def _run(code, tmp_path):
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(code)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_vfx_cli_renderer_wire_and_json_contracts(tmp_path):
    output = _run(
        r"""
        import copy
        import json
        import httpx
        from click.testing import CliRunner
        from cli.main import cli
        from cli.utils import connection
        requests, failures = [], []
        checks = 0
        raw = {}
        client_type = httpx.AsyncClient
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=raw)
        connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
        connection._auth_headers = lambda config: {}
        def check(condition, label):
            global checks
            checks += 1
            if not condition:
                failures.append(label)
                print("FAIL", label)
        cases = [
            (["particle", "play", "0"], {"action": "particle_play", "target": "0", "properties": {"withChildren": False}}),
            (["particle", "stop", "Effects", "--with-children"], {"action": "particle_stop", "target": "Effects", "properties": {"withChildren": True}}),
            (["particle", "restart", "Effects"], {"action": "particle_restart", "target": "Effects", "properties": {"withChildren": False}}),
            (["particle", "clear", "Effects"], {"action": "particle_clear", "target": "Effects", "properties": {"withChildren": False}}),
            (["line", "info", "Root/Line", "--search-method", "by_path", "--component-index", "0"], {"action": "line_get_info", "target": "Root/Line", "searchMethod": "by_path", "componentIndex": 0}),
            (["line", "set-positions", "Line", "--positions", "[]"], {"action": "line_set_positions", "target": "Line", "properties": {"positions": []}}),
            (["line", "set-positions", "Line", "--positions", "[[0,-1,0],[1,0,2]]", "--component-index", "0"], {"action": "line_set_positions", "target": "Line", "componentIndex": 0, "properties": {"positions": [[0,-1,0],[1,0,2]]}}),
            (["line", "set-positions", "Line", "--positions", "[[0,1,2],false]"], {"action": "line_set_positions", "target": "Line", "properties": {"positions": [[0,1,2],False]}}),
            (["line", "clear", "Line"], {"action": "line_clear", "target": "Line"}),
            (["trail", "info", "Trail", "--search-method", "by_name", "--component-index", "0"], {"action": "trail_get_info", "target": "Trail", "searchMethod": "by_name", "componentIndex": 0}),
            (["trail", "set-time", "Trail", "0"], {"action": "trail_set_time", "target": "Trail", "properties": {"time": 0.0}}),
            (["trail", "set-time", "Trail", "--", "-1"], {"action": "trail_set_time", "target": "Trail", "properties": {"time": -1.0}}),
            (["trail", "clear", "Trail"], {"action": "trail_clear", "target": "Trail"}),
            (["raw", "trail_set_properties", "0", "--component-index", "0", "--params", '{"emitting":false,"time":0,"reference":null,"properties":{"width":0}}'], {"action": "trail_set_properties", "target": "0", "componentIndex": 0, "properties": {"emitting":False,"time":0,"reference":None,"width":0}}),
        ]
        runner = CliRunner()
        for failed in (False, True):
            for wrapped in (False, True):
                expected = {"success": not failed, "message": "Native rejected" if failed else "Done", "data": {"count": 0, "enabled": False, "reference": None}}
                raw = {"status": "success", "result": copy.deepcopy(expected)} if wrapped else copy.deepcopy(expected)
                for command, wire in cases:
                    before = len(requests)
                    result = runner.invoke(cli, ["--format", "json", "--instance", "Project@fixture", "vfx", *command])
                    label = str(command) + f" failure={failed} wrapped={wrapped}"
                    check(result.exit_code == (1 if failed else 0), "exit " + label)
                    try:
                        parsed = json.loads(result.stdout)
                    except ValueError:
                        parsed = None
                    check(parsed == expected, "one JSON document " + label)
                    check(len(requests) == before + 1 and requests[-1] == {"type": "manage_vfx", "params": wire, "unity_instance": "Project@fixture"}, "exact wire " + label)
        raw = {"success": True, "message": "Done"}
        for action, notice in (("play", "Playing"), ("stop", "Stopped")):
            result = runner.invoke(cli, ["vfx", "particle", action, "Effects"])
            check(result.exit_code == 0 and notice in result.stdout, "text notice " + action)
        for command in (["line", "set-positions", "Line", "--positions", "{}"], ["line", "set-positions", "Line", "--positions", "[bad"], ["raw", "trail_emit", "--params", "[]"]):
            before = len(requests)
            result = runner.invoke(cli, ["vfx", *command])
            check(result.exit_code != 0 and len(requests) == before, "malformed local rejection " + str(command))
        print(f"fresh actual VFX CLI checks={checks} failures={len(failures)} requests={len(requests)}")
        assert not failures, failures
    """,
        tmp_path,
    )
    assert "fresh actual VFX CLI checks=" in output


def test_vfx_renderer_at_actual_sdk_registry_and_transport(tmp_path):
    output = _run(
        r"""
        import copy
        import anyio
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from services.tools.manage_vfx import LINE_ACTIONS, TRAIL_ACTIONS
        from core.config import config
        from transport.plugin_hub import PluginHub
        config.transport_mode = "http"
        config.http_remote_hosted = False
        requests, failures = [], []
        checks = 0
        raw = {"success": True, "data": {"count": 0, "enabled": False, "reference": None}}
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
        server = FastMCP("vfx-renderer-contracts")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        server.enable(tags={"group:vfx"})
        async def main():
            global raw
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    tools = {tool.name: tool for tool in await client.list_tools()}
                    check("manage_vfx" in tools, "registry discovery " + mode)
                    index_schema = tools["manage_vfx"].input_schema["properties"]["component_index"]
                    check(index_schema == {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None, "description": "Zero-based index to select which component when multiple of the same type exist (e.g., multiple ParticleSystems). If omitted, targets the first instance."}, "full optional integer schema/help unchanged " + mode)
                    for value in (True, False, 1.0, 0.5, '0', '1', '1e0'):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool("manage_vfx", {"action": "line_get_info", "component_index": value})
                        except ToolError:
                            rejected = True
                        check(rejected and len(requests) == before, "strict index rejected before transport " + mode + repr(value))
                    for value, expected in ((0, 0), (-1, -1), (1, 1), (None, None)):
                        await client.call_tool("manage_vfx", {"action": "line_get_info", "component_index": value})
                        wire = requests[-1][2]
                        check(wire == ({"action": "line_get_info"} if expected is None else {"action": "line_get_info", "componentIndex": expected}), "index compatible conversion " + mode + repr(value))
                    for action in LINE_ACTIONS + TRAIL_ACTIONS:
                        properties = {"positions": [], "position": [0,-1,0], "emitting": False, "time": 0, "reference": None}
                        result = await client.call_tool("manage_vfx", {"action": action.upper(), "target": "0", "search_method": "by_name", "component_index": 0, "properties": properties})
                        check(result.structured_content == raw, "success values " + action + mode)
                        check(requests[-1] == ("Project@fixture", "manage_vfx", {"action": action, "target": "0", "searchMethod": "by_name", "componentIndex": 0, "properties": properties}), "wire " + action + mode)
                    for properties in (None, {}, '{"enabled":false,"time":0,"reference":null}'):
                        await client.call_tool("manage_vfx", {"action": "trail_get_info", "properties": properties})
                        wire = requests[-1][2]
                        check(("properties" not in wire) if properties is None else wire["properties"] == ({} if properties == {} else {"enabled": False,"time":0,"reference":None}), "properties omission/object string " + mode)
                    for malformed in ("[]", "null", "[bad", "1", ""):
                        before = len(requests)
                        result = await client.call_tool("manage_vfx", {"action": "line_set_positions", "properties": malformed})
                        check(result.structured_content["success"] is False and len(requests) == before, "malformed properties local " + mode + malformed)
                    for action in ("unknown", "line_unknown", "trail_unknown"):
                        before = len(requests)
                        result = await client.call_tool("manage_vfx", {"action": action})
                        check(result.structured_content["success"] is False and len(requests) == before, "unknown action local " + mode + action)
                    raw = {"status":"success","result":{"success":False,"code":"invalid_vector","message":"Native renderer rejected","data":{"count":0,"enabled":False,"reference":None}}}
                    for action, properties in (("line_set_positions", {"positions":[[0,1,2],False]}), ("trail_set_properties", {"emitting":False,"time":0})):
                        result = await client.call_tool("manage_vfx", {"action":action,"properties":properties})
                        check(result.structured_content == raw["result"], "native failure diagnostics " + action + mode)
                        check(requests[-1][2]["properties"] == properties, "native validation input intact " + action + mode)
                    raw = "malformed transport response"
                    result = await client.call_tool("manage_vfx", {"action":"line_get_info"})
                    check(result.structured_content == {"success":False,"message":raw}, "nonobject response " + mode)
                    raw = {"success":True,"data":{"count":0,"enabled":False,"reference":None}}
                    for payload in ({"action":"line_get_info","search_method":"invalid"}, {"action":"line_get_info","target":1}):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool("manage_vfx", payload)
                        except ToolError:
                            rejected = True
                        check(rejected and len(requests) == before, "schema rejects before transport " + mode)
            print(f"fresh actual VFX SDK checks={checks} failures={len(failures)} requests={len(requests)}")
            assert not failures, failures
        anyio.run(main)
    """,
        tmp_path,
    )
    assert "fresh actual VFX SDK checks=" in output
