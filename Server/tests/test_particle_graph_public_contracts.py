"""Particle/VFX Graph public contracts through fresh real SDK and CLI paths."""

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


def test_particle_graph_cli_payloads_and_responses(tmp_path):
    output = _run(
        r"""
        import copy
        import json
        import httpx
        from click.testing import CliRunner
        from cli.main import cli
        from cli.utils import connection
        from services.tools.manage_vfx import PARTICLE_ACTIONS, VFX_ACTIONS
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
        runner = CliRunner()
        success = {"success":True,"message":"Done","target":"Effects","targetId":0,"createdGameObject":False,"addedParticleSystem":False,"materialReplaced":False,"replacementReason":"","burstIndex":0,"isPaused":False,"data":None}
        for failed in (False, True):
            for wrapped in (False, True):
                expected = {"success":False,"message":"VFX Graph package (com.unity.visualeffectgraph) not installed","data":{"count":0,"enabled":False,"reference":None}} if failed else copy.deepcopy(success)
                raw = {"status":"success","result":copy.deepcopy(expected)} if wrapped else copy.deepcopy(expected)
                for action in PARTICLE_ACTIONS + VFX_ACTIONS:
                    properties = {"parameter":"Enabled","value":False,"rateOverTime":0,"bursts":[],"reference":None}
                    command = ["--format","json","--instance","Project@fixture","vfx","raw",action,"0","--search-method","by_name","--component-index","0","--params",json.dumps(properties)]
                    before = len(requests)
                    result = runner.invoke(cli, command)
                    label = action + f" failed={failed} wrapped={wrapped}"
                    check(result.exit_code == (1 if failed else 0), "exit " + label)
                    try:
                        parsed = json.loads(result.stdout)
                    except ValueError:
                        parsed = None
                    check(parsed == expected, "JSON/diagnostic " + label)
                    check(len(requests) == before+1 and requests[-1] == {"type":"manage_vfx","unity_instance":"Project@fixture","params":{"action":action,"target":"0","searchMethod":"by_name","componentIndex":0,"properties":properties}}, "wire " + label)
        raw = copy.deepcopy(success)
        raw.pop("data")
        result = runner.invoke(cli, ["--format","json","vfx","raw","particle_create","Effects","--params",'{"playOnAwake":false}'])
        check(result.exit_code == 0 and json.loads(result.stdout) == raw, "native flat success/omitted data retained")
        raw = copy.deepcopy(success)
        for action in ("play","stop","restart","clear","pause","info"):
            for with_children in (False, True) if action in ("play","stop","restart","clear") else (None,):
                command = ["--format","json","vfx","particle",action,"Effects"]
                if with_children:
                    command.append("--with-children")
                result = runner.invoke(cli, command)
                check(result.exit_code == 0 and json.loads(result.stdout) == success, "particle JSON " + action + str(with_children))
                wire = {"action":"particle_get_info" if action == "info" else "particle_"+action,"target":"Effects"}
                if with_children is not None:
                    wire["properties"] = {"withChildren":with_children}
                check(requests[-1]["params"] == wire, "particle child flag " + action + str(with_children))
        for action, nested, flat in (
            ("vfx_set_bool", {"value":False}, {"parameter":"Enabled"}),
            ("vfx_set_float", {"value":0}, {"parameter":"Rate"}),
            ("vfx_send_event", {"position":[0,-1,0],"size":0,"lifetime":None}, {"eventName":"OnPlay"}),
            ("particle_create", {"playOnAwake":False,"looping":False}, {"position":[0,0,0]}),
            ("particle_set_emission", {"bursts":[],"enabled":False}, {"rateOverTime":0}),
        ):
            for serialized in (False, True):
                params = {**flat,"properties":json.dumps(nested) if serialized else nested}
                result = runner.invoke(cli, ["--format","json","vfx","raw",action,"Effects","--params",json.dumps(params)])
                check(result.exit_code == 0, "mixed properties exit " + action + str(serialized))
                check(requests[-1]["params"] == {"action":action,"target":"Effects","properties":{**flat,**nested}}, "mixed properties intact " + action + str(serialized))
        for serialized in (False, True):
            params = {"value":True,"parameter":"Flat","properties":json.dumps({"value":False,"parameter":"Nested"}) if serialized else {"value":False,"parameter":"Nested"}}
            result = runner.invoke(cli, ["--format","json","vfx","raw","vfx_set_bool","Effects","--params",json.dumps(params)])
            check(result.exit_code == 0 and requests[-1]["params"]["properties"] == {"value":False,"parameter":"Nested"}, "nested precedence " + str(serialized))
        result = runner.invoke(cli, ["--format","json","vfx","raw","vfx_set_bool","Effects","--params",'{"parameter":"Enabled","value":false,"properties":null}'])
        check(result.exit_code == 0 and requests[-1]["params"]["properties"] == {"parameter":"Enabled","value":False}, "null properties keeps flat values")
        for properties in (' \n {"value":false} \t ', ' {} '):
            result = runner.invoke(cli, ["--format","json","vfx","raw","vfx_set_bool","Effects","--params",json.dumps({"parameter":"Enabled","properties":properties})])
            check(result.exit_code == 0 and requests[-1]["params"]["properties"] == {"parameter":"Enabled",**json.loads(properties)},"whitespace object properties merged")
        for properties in (None, {}, "{}", '{"value":false,"reference":null}'):
            result = runner.invoke(cli, ["--format","json","vfx","raw","vfx_get_info","Effects","--params",json.dumps({"properties":properties})])
            expected = {"action":"vfx_get_info","target":"Effects"}
            if properties is not None:
                expected["properties"] = properties
            check(result.exit_code == 0 and requests[-1]["params"] == expected, "standalone property representation " + repr(properties))
        for action, value in (("vfx_set_vector2",[2,3]),("vfx_set_vector3",[2,3,4]),("vfx_set_vector2",[2,3,4,5]),("vfx_set_vector3",[2,3,4,5,6]),("vfx_set_vector2",None),("vfx_set_vector3",None)):
            result = runner.invoke(cli, ["--format","json","vfx","raw",action,"Effects","--params",json.dumps({"parameter":"Direction","value":value})])
            check(result.exit_code == 0 and requests[-1]["params"]["properties"] == {"parameter":"Direction","value":value},"vector dimension/null wire " + action + repr(value))
        for malformed in ("[bad", "[]", "null", "1", "", " ", [], 1, False):
            before = len(requests)
            result = runner.invoke(cli, ["--format","json","vfx","raw","vfx_set_bool","Effects","--params",json.dumps({"parameter":"Enabled","value":False,"properties":malformed})])
            check(result.exit_code == 1 and result.stdout == "" and "properties" in result.stderr, "malformed properties local diagnostic " + repr(malformed))
            check(len(requests) == before, "malformed properties rejected before transport " + repr(malformed))
        for malformed in ("[]", "[bad", "null", "1"):
            before = len(requests)
            result = runner.invoke(cli, ["vfx","raw","vfx_set_bool","Effects","--params",malformed])
            check(result.exit_code != 0 and len(requests) == before, "outer params local rejection " + malformed)
        raw = copy.deepcopy(success)
        for action, notice in (("play","Playing"),("stop","Stopped")):
            result = runner.invoke(cli, ["vfx","particle",action,"Effects"])
            check(result.exit_code == 0 and notice in result.stdout, "text notice " + action)
        print(f"fresh particle/graph CLI checks={checks} failures={len(failures)} requests={len(requests)}")
        assert not failures, failures
    """,
        tmp_path,
    )
    assert "fresh particle/graph CLI checks=" in output


def test_particle_graph_registered_sdk_payloads_and_diagnostics(tmp_path):
    output = _run(
        r"""
        import copy
        import json
        import anyio
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from services.tools.manage_vfx import PARTICLE_ACTIONS, VFX_ACTIONS
        from core.config import config
        from transport.plugin_hub import PluginHub
        config.transport_mode = "http"
        config.http_remote_hosted = False
        requests, failures = [], []
        checks = 0
        raw = {}
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
        server = FastMCP("particle-graph-contracts")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        server.enable(tags={"group:vfx"})
        async def main():
            global raw
            for mode in ("2026-07-28","legacy"):
                async with Client(server, mode=mode) as client:
                    tools = {tool.name:tool for tool in await client.list_tools()}
                    check("manage_vfx" in tools, "registered discovery " + mode)
                    print("FULL_SCHEMA", mode, json.dumps(tools["manage_vfx"].inputSchema,sort_keys=True))
                    for failed in (False, True):
                        raw = {"status":"success","result":{"success":False,"message":"Native property rejected","data":{"count":0,"enabled":False,"reference":None}}} if failed else {"success":True,"targetId":0,"createdGameObject":False,"addedParticleSystem":False,"burstIndex":0,"isPaused":False,"replacementReason":"","data":None}
                        expected = raw["result"] if failed else raw
                        for action in PARTICLE_ACTIONS + VFX_ACTIONS:
                            properties = {"parameter":"Enabled","value":False,"rate_over_time":0,"bursts":[],"reference":None,"gradient":{"alpha_keys":[{"alpha":0,"time":0}]}}
                            payload = {"action":action.upper(),"target":"0","search_method":"by_name","component_index":0,"properties":properties}
                            result = await client.call_tool("manage_vfx",payload)
                            check(result.structured_content == expected,"response " + action + mode + str(failed))
                            check(requests[-1] == ("Project@fixture","manage_vfx",{"action":action,"target":"0","searchMethod":"by_name","componentIndex":0,"properties":properties}), "wire " + action + mode + str(failed))
                    raw = {"success":True,"data":None}
                    raw = {"success":True,"targetId":0,"createdGameObject":False,"assignedMaterial":None}
                    result = await client.call_tool("manage_vfx",{"action":"particle_create","target":"Effects"})
                    check(result.structured_content == raw,"native flat success/omitted data " + mode)
                    raw = {"success":True,"data":None}
                    for properties in (None, {}, '{"value":false,"playOnAwake":false,"rateOverTime":0,"bursts":[],"reference":null}'):
                        await client.call_tool("manage_vfx",{"action":"particle_set_main","properties":properties})
                        wire = requests[-1][2]
                        check(("properties" not in wire) if properties is None else wire["properties"] == ({} if properties == {} else json.loads(properties)),"properties representation " + mode)
                    for selector in ("by_id","by_name","by_path","by_tag","by_layer"):
                        await client.call_tool("manage_vfx",{"action":"vfx_get_info","target":"Root/Effects","search_method":selector,"component_index":-1})
                        check(requests[-1][2] == {"action":"vfx_get_info","target":"Root/Effects","searchMethod":selector,"componentIndex":-1},"selector/index preservation " + mode + selector)
                    for action, properties in (("vfx_set_vector2",{"parameter":"Direction","value":[2,3]}),("vfx_set_vector3",{"parameter":"Direction","value":[2,3,4]}),("vfx_set_vector2",{"parameter":"Direction","value":[2,3,4,5]}),("vfx_set_vector3",{"parameter":"Direction","value":[2,3,4,5,6]}),("vfx_set_vector2",{"parameter":"Direction"}),("vfx_set_vector3",{"parameter":"Direction","value":None})):
                        await client.call_tool("manage_vfx",{"action":action,"properties":properties})
                        check(requests[-1][2] == {"action":action,"properties":properties},"vector dimension/omission/null wire " + action + mode)
                    for malformed in ("[]","null","1","[bad",""):
                        before = len(requests)
                        result = await client.call_tool("manage_vfx",{"action":"vfx_set_bool","properties":malformed})
                        check(result.structured_content["success"] is False and len(requests) == before,"malformed local " + mode + malformed)
                    for action in ("particle_unknown","vfx_unknown","unknown"):
                        before = len(requests)
                        result = await client.call_tool("manage_vfx",{"action":action})
                        check(result.structured_content["success"] is False and len(requests) == before,"unknown action local " + mode + action)
                    for value in (True,False):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool("manage_vfx",{"action":"vfx_get_info","component_index":value})
                        except ToolError:
                            rejected = True
                        check(rejected and len(requests) == before,"prior bool index guard " + mode + str(value))
                    raw = {"success":False,"message":"VFX Graph package (com.unity.visualeffectgraph) not installed"}
                    result = await client.call_tool("manage_vfx",{"action":"vfx_get_info"})
                    check(result.structured_content == raw,"package unavailable diagnostic " + mode)
                    raw = "nonobject transport response"
                    result = await client.call_tool("manage_vfx",{"action":"particle_get_info"})
                    check(result.structured_content == {"success":False,"message":raw},"nonobject response " + mode)
            print(f"fresh particle/graph SDK checks={checks} failures={len(failures)} requests={len(requests)}")
            assert not failures, failures
        anyio.run(main)
    """,
        tmp_path,
    )
    assert "fresh particle/graph SDK checks=" in output
