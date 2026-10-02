"""Focused physics material, settings and simulation public contracts."""
import os
import subprocess
import sys
import textwrap


def _run(code, tmp_path):
    env = {**os.environ, "APPDATA": str(tmp_path), "XDG_DATA_HOME": str(tmp_path), "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    result = subprocess.run([sys.executable, "-B", "-c", textwrap.dedent(code)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_physics_assets_settings_cli_payloads_and_diagnostics(tmp_path):
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
        def check(condition, label):
            global checks
            checks += 1
            if not condition:
                failures.append(label)
                print("FAIL",label)
        runner = CliRunner()
        cases = [
            (["create-material","--name","Fixture"],{"action":"create_physics_material","name":"Fixture","dimension":"3d"},{"path":"Assets/Physics Materials/Fixture.physicMaterial","dynamicFriction":0.6,"staticFriction":0.6,"bounciness":0,"dimension":"3d"}),
            (["create-material","--name","Fixture.physicMaterial","--path","Assets/Physics/Nested","--bounciness","0","--dynamic-friction","0","--static-friction","0"],{"action":"create_physics_material","name":"Fixture.physicMaterial","path":"Assets/Physics/Nested","dimension":"3d","bounciness":0,"dynamic_friction":0,"static_friction":0},{"path":"Assets/Physics/Nested/Fixture.physicMaterial.physicMaterial","bounciness":0}),
            (["create-material","--name","Fixture","--dimension","2d","--path",r"Assets\Physics\Nested","--friction","0","--bounciness","0"],{"action":"create_physics_material","name":"Fixture","path":r"Assets\Physics\Nested","dimension":"2d","friction":0,"bounciness":0},{"path":"Assets/Physics/Nested/Fixture.physicsMaterial2D","friction":0,"bounciness":0,"dimension":"2d"}),
            (["create-material","--name","Fixture","--path",""],{"action":"create_physics_material","name":"Fixture","dimension":"3d"},{"path":"Assets/Physics Materials/Fixture.physicMaterial"}),
            (["configure-material","--path","Assets/Physics/Fixture.physicMaterial","dynamic_friction=0","bounceCombine=Maximum"],{"action":"configure_physics_material","path":"Assets/Physics/Fixture.physicMaterial","dimension":"3d","properties":{"dynamic_friction":0,"bounceCombine":"Maximum"}},{"path":"Assets/Physics/Fixture.physicMaterial","changed":["dynamicFriction","bounceCombine"]}),
            (["configure-material","--path","Assets/Physics/Fixture.physicsMaterial2D","--dimension","2d","friction=0","bounciness=0"],{"action":"configure_physics_material","path":"Assets/Physics/Fixture.physicsMaterial2D","dimension":"2d","properties":{"friction":0,"bounciness":0}},{"path":"Assets/Physics/Fixture.physicsMaterial2D","changed":["friction","bounciness"]}),
            (["assign-material","--target","Root/Body","--material-path","Assets/Physics/Fixture.physicMaterial","--collider-type","BoxCollider","--component-index","1"],{"action":"assign_physics_material","target":"Root/Body","material_path":"Assets/Physics/Fixture.physicMaterial","collider_type":"BoxCollider","componentIndex":1},{"target":"Root/Body","collider":"BoxCollider","materialPath":"Assets/Physics/Fixture.physicMaterial","dimension":"3d"}),
            (["assign-material","--target","0","--material-path","Assets/Physics/Fixture.physicsMaterial2D","--component-index","0"],{"action":"assign_physics_material","target":"0","material_path":"Assets/Physics/Fixture.physicsMaterial2D","componentIndex":0},{"target":"0","collider":"BoxCollider2D","materialPath":"Assets/Physics/Fixture.physicsMaterial2D","dimension":"2d"}),
            (["get-settings","--dimension","2d"],{"action":"get_settings","dimension":"2d"},{"dimension":"2d","gravity":[0,-9.81],"queriesHitTriggers":False,"velocityIterations":0,"positionIterations":0}),
            (["set-settings","queriesHitTriggers","false"],{"action":"set_settings","dimension":"3d","settings":{"queriesHitTriggers":False}},{"changed":["queriesHitTriggers"]}),
            (["set-settings","defaultContactOffset","0"],{"action":"set_settings","dimension":"3d","settings":{"defaultContactOffset":0}},{"changed":["defaultContactOffset"]}),
            (["set-settings","simulationMode","Script"],{"action":"set_settings","dimension":"3d","settings":{"simulationMode":"Script"}},{"changed":["simulationMode"]}),
            (["simulate"],{"action":"simulate_step","steps":1,"dimension":"3d"},{"steps_executed":1,"step_size":0.02,"dimension":"3d","rigidbodies":[]}),
            (["simulate","--dimension","2d","--steps","0","--step-size","0"],{"action":"simulate_step","steps":0,"dimension":"2d","step_size":0},{"steps_executed":1,"step_size":0,"dimension":"2d","rigidbodies":[]}),
            (["simulate","--steps","101","--step-size","-1"],{"action":"simulate_step","steps":101,"dimension":"3d","step_size":-1},{"steps_executed":100,"step_size":-1,"dimension":"3d","rigidbodies":[]}),
        ]
        for args, wire, data in cases:
            for failed in (False, True):
                for wrapped in (False, True):
                    expected = {"success":not failed,"message":"Native operation rejected" if failed else "Done","data":{"count":0,"reference":None,"enabled":False} if failed else data}
                    raw = {"status":"success","result":copy.deepcopy(expected)} if wrapped else copy.deepcopy(expected)
                    before = len(requests)
                    result = runner.invoke(cli,["--format","json","--instance","Project@fixture","physics",*args])
                    label = repr(args)+str(failed)+str(wrapped)
                    check(result.exit_code == (1 if failed else 0),"exit "+label)
                    try:
                        document = json.loads(result.stdout)
                    except ValueError:
                        document = None
                    check(document == expected,"single JSON/falsey/native diagnostic "+label)
                    check(len(requests) == before+1 and requests[-1] == {"type":"manage_physics","unity_instance":"Project@fixture","params":wire},"wire "+label)
                    if not failed and not wrapped:
                        print("DOMAIN_WIRE",json.dumps({"surface":"cli","input":args,"wire":requests[-1]["params"]},sort_keys=True))
        raw = {"success":True,"message":"Updated gravity","data":{"changed":["gravity"]}}
        for dimension, key, text, vector in (("3d","gravity","[0,-9.81,0]",[0,-9.81,0]),("2d","gravity","[0,-9.81]",[0,-9.81]),("3d","GRAVITY",' ["0", "-1.5", "0", 9] ',["0","-1.5","0",9]),("2d","Gravity","[0,0,0]",[0,0,0])):
            result = runner.invoke(cli,["--format","json","physics","set-settings","--dimension",dimension,key,text])
            check(result.exit_code == 0 and json.loads(result.stdout) == raw,"gravity JSON output "+key+dimension)
            check(requests[-1]["params"] == {"action":"set_settings","dimension":dimension,"settings":{key:vector}},"gravity array wire "+key+dimension)
            print("GRAVITY_WIRE",json.dumps({"dimension":dimension,"input":text,"wire":requests[-1]["params"]},sort_keys=True))
        for value in ("0,-9.81,0","[bad","{}","null","true","0",""):
            before = len(requests)
            result = runner.invoke(cli,["--format","json","physics","set-settings","gravity",value])
            check(result.exit_code == 2 and "JSON array" in result.output,"invalid gravity local diagnostic "+repr(value))
            check(len(requests) == before,"invalid gravity before HTTP "+repr(value))
        raw = {"success":False,"error":"3D gravity requires [x, y, z] array."}
        for value, vector in (("[]",[]),("[0]",[0]),('["bad",0,0]',["bad",0,0])):
            result = runner.invoke(cli,["--format","json","physics","set-settings","gravity",value])
            check(result.exit_code == 1 and json.loads(result.stdout) == raw,"invalid array native diagnostic retained "+value)
            check(requests[-1]["params"]["settings"] == {"gravity":vector},"array dimension/value validation remains native "+value)
        raw = {"success":True,"message":"Done","data":{"changed":[]}}
        for key, value, expected_value in (("defaultContactOffset","1e-3","1e-3"),("queriesHitTriggers","TRUE",True),("simulationMode","0",0),("unknown","[0,1]","[0,1]"),("simulationMode","","")):
            runner.invoke(cli,["--format","json","physics","set-settings",key,value])
            check(requests[-1]["params"]["settings"] == {key:expected_value},"non-gravity scalar policy "+key)
        before = len(requests)
        result = runner.invoke(cli,["physics","configure-material","--path","Assets/Physics/Fixture.physicMaterial","friction=0","bad"])
        check(result.exit_code == 2 and len(requests) == before,"prior malformed-pair guard retained")
        for command in ("create-material","configure-material","assign-material","set-settings","get-settings","simulate"):
            result = runner.invoke(cli,["physics",command,"--help"])
            check(result.exit_code == 0,"help "+command)
            print("FULL_HELP",command,json.dumps(result.stdout))
        print(f"fresh physics assets/settings CLI checks={checks} failures={len(failures)} requests={len(requests)}")
        assert not failures, failures
    ''', tmp_path)
    assert "fresh physics assets/settings CLI checks=" in output


def test_physics_assets_settings_registered_sdk_payloads_and_results(tmp_path):
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
        server = FastMCP("physics-assets-settings")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        cases = [
            {"action":"create_physics_material","name":"Fixture"},
            {"action":"create_physics_material","name":"Fixture","dimension":"2d","path":r"Assets\Physics\Nested","friction":0,"bounciness":0},
            {"action":"create_physics_material","name":"Fixture.physicMaterial","path":"Assets/Physics/Nested","dynamic_friction":0,"static_friction":0,"friction_combine":"Average","bounce_combine":"Maximum"},
            {"action":"create_physics_material","name":"Fixture","path":"","dimension":None,"dynamic_friction":None},
            {"action":"configure_physics_material","path":"Assets/Physics/Fixture.physicMaterial","properties":{"dynamic_friction":0,"bounceCombine":"Maximum","reference":None}},
            {"action":"configure_physics_material","path":"Assets/Physics/Fixture.physicsMaterial2D","dimension":"2d","properties":{"friction":0,"bounciness":0}},
            {"action":"configure_physics_material","path":"Assets/Physics/Fixture.physicMaterial","properties":{}},
            {"action":"assign_physics_material","target":"0","search_method":"by_name","material_path":"Assets/Physics/Fixture.physicMaterial","collider_type":"BoxCollider","component_index":0},
            {"action":"assign_physics_material","target":"Root/Body","search_method":"by_path","material_path":"Assets/Physics/Fixture.physicsMaterial2D","component_index":1},
            {"action":"get_settings","dimension":"2d"},
            {"action":"set_settings","dimension":"3d","settings":{"gravity":[0,-9.81,0],"queriesHitTriggers":False,"defaultContactOffset":0,"simulationMode":"Script"}},
            {"action":"set_settings","dimension":"2d","settings":{"gravity":["0","-1.5"],"queriesStartInColliders":False,"velocityIterations":0}},
            {"action":"set_settings","settings":{}},
            {"action":"set_settings","settings":None},
            {"action":"set_settings","settings":{"gravity":None,"queriesHitTriggers":"false"}},
            {"action":"simulate_step"},
            {"action":"simulate_step","dimension":"2d","steps":0,"step_size":0,"target":"0","search_method":"by_name"},
            {"action":"simulate_step","dimension":"3d","steps":101,"step_size":-1,"target":"Root/Body","search_method":"by_path"},
            {"action":"simulate_step","steps":None,"step_size":None,"target":None,"search_method":None},
        ]
        async def main():
            global raw
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    tools = {tool.name:tool for tool in await client.list_tools()}
                    check("manage_physics" in tools,"registered discovery "+mode)
                    print("FULL_SCHEMA",mode,json.dumps(tools["manage_physics"].inputSchema,sort_keys=True))
                    for payload in cases:
                        for failed in (False, True):
                            for wrapped in (False, True):
                                expected = {"success":not failed,"message":"Native operation rejected" if failed else "Done","data":{"changed":[],"path":None,"steps_executed":0,"step_size":0,"rigidbodies":[],"enabled":False}}
                                raw = {"status":"success","result":copy.deepcopy(expected)} if wrapped else copy.deepcopy(expected)
                                before = len(requests)
                                result = await client.call_tool("manage_physics",payload)
                                wire = {"componentIndex" if key == "component_index" else key:value for key,value in payload.items() if value is not None}
                                label = mode+repr(payload)+str(failed)+str(wrapped)
                                check(result.structured_content == expected,"native document/falsey result "+label)
                                check(len(requests) == before+1 and requests[-1] == ("Project@fixture","manage_physics",wire),"path/default/opaque settings wire "+label)
                                if not failed and not wrapped:
                                    print("DOMAIN_WIRE",json.dumps({"surface":"sdk","mode":mode,"input":payload,"wire":requests[-1][2]},sort_keys=True))
                    raw = {"success":True,"message":"Done"}
                    for payload in ({"action":"assign_physics_material","component_index":True},{"action":"assign_physics_material","component_index":False},{"action":"set_settings","settings":[]},{"action":"configure_physics_material","properties":"bad"},{"action":"simulate_step","steps":1.5}):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool("manage_physics",payload)
                        except ToolError:
                            rejected = True
                        check(rejected and len(requests) == before,"schema/prior bool guard before dispatch "+mode+repr(payload))
            print(f"fresh physics assets/settings SDK checks={checks} failures={len(failures)} requests={len(requests)}")
            assert not failures, failures
        anyio.run(main)
    ''', tmp_path)
    assert "fresh physics assets/settings SDK checks=" in output
