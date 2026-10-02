"""Fresh public Animator control/read request and result contracts."""
import os
import subprocess
import sys
import textwrap


def _run(code, tmp_path):
    env = {**os.environ, "APPDATA": str(tmp_path), "XDG_DATA_HOME": str(tmp_path), "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    result = subprocess.run([sys.executable, "-B", "-c", textwrap.dedent(code)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_animator_control_read_actual_cli_transport(tmp_path):
    output = _run(r'''
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
            return httpx.Response(200,json=raw)
        connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
        connection._auth_headers = lambda config: {}
        def check(condition,label):
            global checks
            checks += 1
            if not condition:
                failures.append(label)
                print("FAIL",label)
        runner = CliRunner()
        cases = [
            (["info","Fixture"],{"action":"animator_get_info","target":"Fixture"}),
            (["info","Root/Fixture","--search-method","by_path"],{"action":"animator_get_info","target":"Root/Fixture","searchMethod":"by_path"}),
            (["play","Fixture","Idle"],{"action":"animator_play","target":"Fixture","properties":{"stateName":"Idle","layer":-1}}),
            (["play","0","Idle","--layer","0","--search-method","by_id"],{"action":"animator_play","target":"0","searchMethod":"by_id","properties":{"stateName":"Idle","layer":0}}),
            (["crossfade","Fixture","Idle"],{"action":"animator_crossfade","target":"Fixture","properties":{"stateName":"Idle","duration":0.25,"layer":-1}}),
            (["crossfade","Root/Fixture","Idle","--duration","0","--layer","0","--search-method","by_path"],{"action":"animator_crossfade","target":"Root/Fixture","searchMethod":"by_path","properties":{"stateName":"Idle","duration":0,"layer":0}}),
            (["set-parameter","Fixture","Speed","0"],{"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Speed","value":0}}),
            (["set-parameter","Fixture","Enabled","false","--type","bool"],{"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Enabled","value":False,"parameterType":"bool"}}),
            (["set-parameter","--type","int","--","Fixture","Count","-1"],{"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Count","value":-1,"parameterType":"int"}}),
            (["set-parameter","Fixture","Jump","","--type","trigger"],{"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Jump","value":"","parameterType":"trigger"}}),
            (["set-parameter","Fixture","Speed","null","--type","float"],{"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Speed","value":None,"parameterType":"float"}}),
            (["set-parameter","Fixture","Speed","0","--type","int"],{"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Speed","value":0,"parameterType":"int"}}),
            (["set-parameter","Fixture","Missing","0","--type","float"],{"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Missing","value":0,"parameterType":"float"}}),
            (["get-parameter","Fixture","Speed"],{"action":"animator_get_parameter","target":"Fixture","properties":{"parameterName":"Speed"}}),
            (["get-parameter","0","Enabled","--search-method","by_name"],{"action":"animator_get_parameter","target":"0","searchMethod":"by_name","properties":{"parameterName":"Enabled"}}),
            (["set-speed","Fixture","0"],{"action":"animator_set_speed","target":"Fixture","properties":{"speed":0}}),
            (["set-speed","--","Fixture","-1"],{"action":"animator_set_speed","target":"Fixture","properties":{"speed":-1}}),
            (["set-enabled","Fixture","false"],{"action":"animator_set_enabled","target":"Fixture","properties":{"enabled":False}}),
            (["set-enabled","Fixture","true"],{"action":"animator_set_enabled","target":"Fixture","properties":{"enabled":True}}),
        ]
        documents = [
            {"success":True,"data":{"enabled":False,"speed":0,"controllerName":None,"parameters":[],"clips":[],"layers":[{"index":0,"weight":0,"currentStateHash":0,"currentStateNormalizedTime":0,"isInTransition":True,"nextStateHash":1}]}},
            {"success":True,"data":{"name":"Enabled","type":"Bool","value":False}},
            {"success":True,"data":{"name":"Speed","type":"Float","value":0}},
            {"success":True,"message":"Request issued"},
            {"success":False,"message":"Target GameObject not found"},
            {"success":False,"error":"Parameter type does not match","data":{"value":None,"count":0,"enabled":False}},
        ]
        for args, wire in cases:
            for document in documents:
                for wrapped in (False, True):
                    raw = {"status":"success","result":copy.deepcopy(document)} if wrapped else copy.deepcopy(document)
                    before = len(requests)
                    result = runner.invoke(cli,["--format","json","--instance","Project@fixture","animation","animator",*args])
                    label = repr(args)+repr(document)+str(wrapped)
                    check(result.exit_code == (0 if document["success"] else 1),"exit "+label)
                    try:
                        actual = json.loads(result.stdout)
                    except ValueError:
                        actual = None
                    check(actual == document,"single native JSON/falsey/read result "+label)
                    check(len(requests) == before+1 and requests[-1] == {"type":"manage_animation","unity_instance":"Project@fixture","params":wire},"control/read wire "+label)
            print("DOMAIN_WIRE",json.dumps({"surface":"cli","input":args,"wire":requests[-1]["params"]},sort_keys=True))
        raw = {"success":True,"message":"Controlled response"}
        for properties in ({"state_name":"Nested","layer":0},'{"state_name":"Nested","layer":0}'):
            extra = {"stateName":"Flat","layer":-1,"properties":properties}
            result = runner.invoke(cli,["--format","json","animation","raw","animator_play","Fixture","--params",json.dumps(extra)])
            check(result.exit_code == 0,"previous string merge success")
            check(requests[-1]["params"] == {"action":"animator_play","target":"Fixture","properties":{"stateName":"Flat","state_name":"Nested","layer":0}},"existing literal-key merge precedence/aliases")
        for target in (0,-10,{"name":"Fixture"},{"instanceID":0},{"path":"Root/Fixture"}):
            extra = {"target":target,"search_method":"by_name","parameter_name":"Enabled","value":False}
            runner.invoke(cli,["--format","json","animation","raw","animator_set_parameter","Ignored","--params",json.dumps(extra)])
            check(requests[-1]["params"] == {"action":"animator_set_parameter","target":target,"properties":{"search_method":"by_name","parameter_name":"Enabled","value":False}},"raw scalar/structured target not reinterpreted "+repr(target))
            print("DOMAIN_WIRE",json.dumps({"surface":"cli-raw","input":extra,"wire":requests[-1]["params"]},sort_keys=True))
        before = len(requests)
        result = runner.invoke(cli,["animation","animator","set-parameter","Fixture","Speed","0","--type","bad"])
        check(result.exit_code == 2 and len(requests) == before,"invalid CLI type before transport")
        result = runner.invoke(cli,["animation","animator","set-enabled","Fixture","bad"])
        check(result.exit_code == 2 and len(requests) == before,"invalid CLI enabled before transport")
        for command in ("info","play","crossfade","set-parameter","get-parameter","set-speed","set-enabled"):
            result = runner.invoke(cli,["animation","animator",command,"--help"])
            check(result.exit_code == 0,"help "+command)
            print("FULL_HELP",command,json.dumps(result.stdout))
        print(f"fresh Animator CLI checks={checks} failures={len(failures)} requests={len(requests)}")
        assert not failures, failures
    ''', tmp_path)
    assert "fresh Animator CLI checks=" in output


def test_animator_control_read_registered_sdk(tmp_path):
    output = _run(r'''
        import copy
        import json
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
        raw = {}
        def check(condition,label):
            global checks
            checks += 1
            if not condition:
                failures.append(label)
                print("FAIL",label)
        async def controlled_send(instance,command,params,**kwargs):
            requests.append((instance,command,copy.deepcopy(params)))
            return copy.deepcopy(raw)
        PluginHub.send_command_for_instance = staticmethod(controlled_send)
        class FixtureState(Middleware):
            async def on_call_tool(self,context,call_next):
                await context.fastmcp_context.set_state("unity_instance","Project@fixture")
                return await call_next(context)
        server = FastMCP("animator-control-read")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        server.enable(tags={"group:animation"})
        cases = [
            {"action":"animator_get_info","target":"Fixture"},
            {"action":"ANIMATOR_GET_INFO","target":"Root/Fixture","search_method":"by_path"},
            {"action":"animator_play","target":"Fixture","properties":{"state_name":"Idle","layer":-1}},
            {"action":"animator_play","target":"0","search_method":"by_id","properties":{"stateName":"Idle","layer":0}},
            {"action":"animator_crossfade","target":"Fixture","properties":{"stateName":"Idle"}},
            {"action":"animator_crossfade","target":"Fixture","properties":{"state_name":"Idle","duration":0,"layer":0}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Speed","value":0}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameter_name":"Enabled","parameter_type":"bool","value":False}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Count","parameterType":"integer","value":"0"}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Enabled","parameterType":"boolean","value":"false"}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Jump","parameterType":"trigger","value":""}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Speed","parameterType":"float"}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Speed","parameterType":"float","value":None}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Speed","parameterType":"int","value":0}},
            {"action":"animator_set_parameter","target":"Fixture","properties":{"parameterName":"Missing","parameterType":"float","value":0}},
            {"action":"animator_get_parameter","target":"Fixture","properties":{"parameter_name":"Enabled"}},
            {"action":"animator_get_parameter","target":"0","search_method":"by_name","properties":'{"parameterName":"Speed"}'},
            {"action":"animator_set_speed","target":"Fixture","properties":{"speed":0}},
            {"action":"animator_set_speed","target":"Fixture","properties":{"speed":-1}},
            {"action":"animator_set_enabled","target":"Fixture","properties":{"enabled":False}},
            {"action":"animator_set_enabled","target":"Fixture","properties":{"enabled":True}},
            {"action":"animator_set_enabled","target":"Fixture","properties":{}},
            {"action":"animator_set_enabled","target":"Fixture","properties":None},
            {"action":"animator_get_info","target":None,"search_method":None},
            {"action":"animator_get_info","target":"","search_method":"by_tag"},
            {"action":"animator_get_info","target":"0","search_method":"by_layer"},
        ]
        documents = [
            {"success":True,"data":{"enabled":False,"speed":0,"controllerName":None,"layers":[{"currentStateHash":0,"nextStateHash":1,"isInTransition":True}],"parameters":[],"clips":[]}},
            {"success":True,"data":{"name":"Enabled","type":"Bool","value":False}},
            {"success":True,"data":{"name":"Speed","type":"Float","value":0}},
            {"success":True,"message":"Request issued"},
            {"success":False,"message":"Parameter not found"},
            {"success":False,"error":"Type mismatch","data":{"count":0,"enabled":False,"reference":None}},
        ]
        async def main():
            global raw
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    tools = {tool.name:tool for tool in await client.list_tools()}
                    check("manage_animation" in tools,"registered discovery "+mode)
                    print("FULL_SCHEMA",mode,json.dumps(tools["manage_animation"].inputSchema,sort_keys=True))
                    for payload in cases:
                        wire = {"action":payload["action"].lower()}
                        for key,value in payload.items():
                            if key == "action" or value is None:
                                continue
                            if key == "properties" and isinstance(value,str):
                                value = json.loads(value)
                            wire[{"search_method":"searchMethod","clip_path":"clipPath","controller_path":"controllerPath"}.get(key,key)] = value
                        for document in documents:
                            for wrapped in (False, True):
                                raw = {"status":"success","result":copy.deepcopy(document)} if wrapped else copy.deepcopy(document)
                                before = len(requests)
                                result = await client.call_tool("manage_animation",payload)
                                label = mode+repr(payload)+repr(document)+str(wrapped)
                                check(result.structured_content == document,"native read/control/falsey document "+label)
                                check(len(requests) == before+1 and requests[-1] == ("Project@fixture","manage_animation",wire),"value/default/selector wire "+label)
                        print("DOMAIN_WIRE",json.dumps({"surface":"sdk","mode":mode,"input":payload,"wire":requests[-1][2]},sort_keys=True))
                    for payload in ({"action":"animator_unknown"},{"action":"animator_play","properties":"bad"},{"action":"animator_play","properties":"[]"}):
                        before = len(requests)
                        result = await client.call_tool("manage_animation",payload)
                        check(result.structured_content["success"] is False and len(requests) == before,"local validation "+mode+repr(payload))
                    for target in (True,0,-10,{"name":"Fixture"},["Fixture"]):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool("manage_animation",{"action":"animator_get_info","target":target})
                        except ToolError:
                            rejected = True
                        check(rejected and len(requests) == before,"existing string target schema "+mode+repr(target))
            print(f"fresh Animator SDK checks={checks} failures={len(failures)} requests={len(requests)}")
            assert not failures, failures
        anyio.run(main)
    ''', tmp_path)
    assert "fresh Animator SDK checks=" in output
