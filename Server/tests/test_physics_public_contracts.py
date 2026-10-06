"""Physics contracts exercised through isolated real SDK and CLI transports."""
import os
import subprocess
import sys
import textwrap


def _run(code, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true", "APPDATA": str(tmp_path), "XDG_DATA_HOME": str(tmp_path)}
    result = subprocess.run([sys.executable, "-B", "-c", textwrap.dedent(code)], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_physics_cli_wire_validation_and_native_responses(tmp_path):
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
        cases = [
            (["ping"], {"action":"ping"}),
            (["get-settings","--dimension","2d"], {"action":"get_settings","dimension":"2d"}),
            (["set-settings","queriesHitTriggers","false"], {"action":"set_settings","dimension":"3d","settings":{"queriesHitTriggers":False}}),
            (["get-collision-matrix"], {"action":"get_collision_matrix","dimension":"3d"}),
            (["set-collision-matrix","0","1","--ignore"], {"action":"set_collision_matrix","dimension":"3d","layer_a":"0","layer_b":"1","collide":False}),
            (["create-material","--name","Fixture","--bounciness","0","--dynamic-friction","0","--static-friction","0","--friction","0"], {"action":"create_physics_material","dimension":"3d","name":"Fixture","bounciness":0,"dynamic_friction":0,"static_friction":0,"friction":0}),
            (["configure-material","--path","Assets/Fixture.physicMaterial","bounciness=0"], {"action":"configure_physics_material","dimension":"3d","path":"Assets/Fixture.physicMaterial","properties":{"bounciness":0}}),
            (["assign-material","--target","0","--material-path","Assets/Fixture.physicMaterial","--component-index","0"], {"action":"assign_physics_material","target":"0","material_path":"Assets/Fixture.physicMaterial","componentIndex":0}),
            (["add-joint","--target","Root/Body","--joint-type","hinge","--connected-body","0","--dimension","2d"], {"action":"add_joint","target":"Root/Body","joint_type":"hinge","connected_body":"0","dimension":"2d"}),
            (["configure-joint","--target","Body","--joint-type","hinge","--component-index","0","enableCollision=false"], {"action":"configure_joint","target":"Body","joint_type":"hinge","componentIndex":0,"properties":{"enableCollision":False}}),
            (["remove-joint","--target","Body","--component-index","0"], {"action":"remove_joint","target":"Body","componentIndex":0}),
            (["raycast","--origin","0,-1,0","--direction","0,0,0","--max-distance","0"], {"action":"raycast","dimension":"3d","origin":[0,-1,0],"direction":[0,0,0],"max_distance":0}),
            (["raycast-all","--origin","0,0","--direction","1,0","--dimension","2d","--max-distance","0"], {"action":"raycast_all","dimension":"2d","origin":[0,0],"direction":[1,0],"max_distance":0}),
            (["linecast","--start","0,0,0","--end","0,0,0"], {"action":"linecast","dimension":"3d","start":[0,0,0],"end":[0,0,0]}),
            (["shapecast","--shape","box","--origin","0,0","--direction","0,-1","--size","0,1","--dimension","2d","--max-distance","0"], {"action":"shapecast","dimension":"2d","shape":"box","origin":[0,0],"direction":[0,-1],"size":[0,1],"max_distance":0}),
            (["overlap","--shape","sphere","--position","0,0,0","--size","0"], {"action":"overlap","dimension":"3d","shape":"sphere","position":[0,0,0],"size":0}),
            (["validate"], {"action":"validate","dimension":"both"}),
            (["simulate","--steps","0","--step-size","0"], {"action":"simulate_step","dimension":"3d","steps":0,"step_size":0}),
            (["apply-force","--target","0","--force","0,0","--dimension","2d","--torque","0","--position","0,0"], {"action":"apply_force","target":"0","force":[0,0],"dimension":"2d","force_mode":"Force","torque":[0],"position":[0,0]}),
            (["get-rigidbody","0","--search-method","by_name"], {"action":"get_rigidbody","target":"0","search_method":"by_name"}),
            (["configure-rigidbody","--target","Body","mass=0","useGravity=false"], {"action":"configure_rigidbody","target":"Body","properties":{"mass":0,"useGravity":False}}),
        ]
        for failed in (False, True):
            for wrapped in (False, True):
                expected = {"success":not failed,"message":"Native rejected property" if failed else "Done","data":{"count":0,"enabled":False,"reference":None,"hits":[]}}
                raw = {"status":"success","result":copy.deepcopy(expected)} if wrapped else copy.deepcopy(expected)
                for args, wire in cases:
                    before = len(requests)
                    result = runner.invoke(cli,["--format","json","--instance","Project@fixture","physics",*args])
                    label = args[0]+f" failed={failed} wrapped={wrapped}"
                    check(result.exit_code == (1 if failed else 0),"exit "+label)
                    try:
                        document = json.loads(result.stdout)
                    except ValueError:
                        document = None
                    check(document == expected,"single JSON/native diagnostic "+label)
                    check(len(requests) == before+1 and requests[-1] == {"type":"manage_physics","unity_instance":"Project@fixture","params":wire},"wire "+label)
        configure = [
            (["configure-material","--path","Assets/Fixture.physicMaterial"], {"action":"configure_physics_material","path":"Assets/Fixture.physicMaterial","dimension":"3d"}),
            (["configure-joint","--target","Body"], {"action":"configure_joint","target":"Body"}),
            (["configure-rigidbody","--target","Body"], {"action":"configure_rigidbody","target":"Body"}),
        ]
        raw = {"success":True,"message":"Configured","data":{"count":0,"enabled":False}}
        for command, base in configure:
            for fmt in ("json","text"):
                for token in ("missingEquals","=value","="):
                    before = len(requests)
                    result = runner.invoke(cli,["--format",fmt,"physics",*command,"mass=2",token])
                    label = command[0]+" "+fmt+" "+token
                    check(result.exit_code == 2,"invalid pair exit "+label)
                    check("key=value" in result.output,"invalid pair diagnostic "+label)
                    check(len(requests) == before,"invalid pair no transport "+label)
            for tokens, props in (([],{}),(["flag=false","zero=0","empty=","value=a=b","number=-1.5","number=2","scientific=1e3"],{"flag":False,"zero":0,"empty":"","value":"a=b","number":2,"scientific":"1e3"}),([" =value"],{" ":"value"})):
                result = runner.invoke(cli,["--format","json","physics",*command,*tokens])
                check(result.exit_code == 0 and json.loads(result.stdout) == raw,"valid pairs JSON "+command[0]+repr(tokens))
                check(requests[-1]["params"] == {**base,"properties":props},"valid pair compatibility "+command[0]+repr(tokens))
            result = runner.invoke(cli,["physics",*command,"flag=false","zero=0"])
            check(result.exit_code == 0 and result.stdout == "count: 0\nenabled: False\n","plain success retained "+command[0])
            raw = {"success":False,"message":"Empty properties rejected","data":{"count":0}}
            result = runner.invoke(cli,["--format","json","physics",*command])
            check(result.exit_code == 1 and json.loads(result.stdout) == raw,"no-properties native rejection retained "+command[0])
            raw = {"success":True,"message":"Configured","data":{"count":0,"enabled":False}}
        for command in ([], *[[args[0]] for args, wire in cases]):
            result = runner.invoke(cli,["physics",*command,"--help"])
            check(result.exit_code == 0,"help "+repr(command))
            print("FULL_HELP",json.dumps(command),json.dumps(result.stdout))
        print(f"fresh physics CLI checks={checks} failures={len(failures)} requests={len(requests)}")
        assert not failures, failures
    ''', tmp_path)
    assert "fresh physics CLI checks=" in output


def test_physics_registered_sdk_payloads_schema_and_diagnostics(tmp_path):
    output = _run(r'''
        import copy
        import json
        import anyio
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from services.tools.manage_physics import ALL_ACTIONS, manage_physics
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
            requests.append((instance,command,copy.deepcopy(params)))
            return copy.deepcopy(raw)
        PluginHub.send_command_for_instance = staticmethod(controlled_send)
        class FixtureState(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state("unity_instance","Project@fixture")
                return await call_next(context)
        server = FastMCP("physics-public-contracts")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        fields = {"dimension":"2d","settings":{"queriesHitTriggers":False,"defaultContactOffset":0},"layer_a":"0","layer_b":"1","collide":False,"name":"Fixture","path":"Assets/Fixture","dynamic_friction":0,"static_friction":0,"bounciness":0,"friction":0,"friction_combine":"Average","bounce_combine":"Maximum","material_path":"Assets/Fixture.physicMaterial","target":"0","collider_type":"BoxCollider","search_method":"by_name","joint_type":"hinge","connected_body":"Root/Body","motor":{"force":0,"freeSpin":False},"limits":{},"spring":{"spring":0},"drive":{"enabled":False},"properties":{"enabled":False,"reference":None,"mass":0},"origin":[0,-1,0],"direction":[0,0,0],"max_distance":0,"layer_mask":"0","query_trigger_interaction":"Ignore","shape":"box","position":[0,0],"size":[0,1],"start":[0,0],"end":[0,0],"point1":[0,0,0],"point2":[0,0,0],"height":0,"capsule_direction":0,"angle":0,"force":[0,0],"force_mode":"Impulse","force_type":"normal","torque":[0],"explosion_position":[0,0,0],"explosion_radius":0,"explosion_force":0,"upwards_modifier":0,"steps":0,"step_size":0,"page_size":0,"cursor":0,"component_index":0}
        async def main():
            global raw
            fields['page_size'] = 1
            for mode in ("2026-07-28","legacy"):
                async with Client(server,mode=mode) as client:
                    tools = {tool.name:tool for tool in await client.list_tools()}
                    check("manage_physics" in tools,"registered discovery "+mode)
                    schema = tools["manage_physics"].inputSchema
                    print("FULL_SCHEMA",mode,json.dumps(schema,sort_keys=True))
                    check(set(schema["properties"]["action"]["enum"]) == set(ALL_ACTIONS),"all native actions/schema "+mode)
                    for failed in (False, True):
                        for wrapped in (False, True):
                            expected = {"success":not failed,"message":"Native rejected input" if failed else "Done","data":{"count":0,"enabled":False,"reference":None,"hits":[]}}
                            raw = {"status":"success","result":copy.deepcopy(expected)} if wrapped else copy.deepcopy(expected)
                            for action in ALL_ACTIONS:
                                payload = {"action":action,**copy.deepcopy(fields)}
                                before = len(requests)
                                result = await client.call_tool("manage_physics",payload)
                                wire = {"action":action,**{key:value for key,value in fields.items() if key != "component_index"},"componentIndex":0}
                                label = action+mode+str(failed)+str(wrapped)
                                check(result.structured_content == expected,"native response "+label)
                                check(len(requests) == before+1 and requests[-1] == ("Project@fixture","manage_physics",wire),"all-field/false-zero-empty wire "+label)
                    raw = {"success":True,"message":"Done"}
                    before = len(requests)
                    result = await client.call_tool("manage_physics", {"action":"validate","page_size":0})
                    check(result.structured_content['success'] is False and len(requests) == before,
                          "nonpositive page size rejected before transport " + mode)
                    for payload in ({"action":"ping"},{"action":"ping",**dict.fromkeys(fields)}):
                        result = await client.call_tool("manage_physics",payload)
                        check(result.structured_content == raw and requests[-1][2] == {"action":"ping"},"omitted/null parameters "+mode)
                    for dimension in ("3d","2d"):
                        for trigger in ("UseGlobal","Ignore","Collide"):
                            payload = {"action":"raycast","dimension":dimension,"query_trigger_interaction":trigger,"origin":[0,0],"direction":[0,1],"layer_mask":"-1"}
                            await client.call_tool("manage_physics",payload)
                            check(requests[-1][2] == payload,"dimension/trigger unchanged "+mode+dimension+trigger)
                    for action, joint_type in (("configure_joint","fixed"),("configure_joint",None),("remove_joint","fixed"),("remove_joint",None)):
                        for index, normalized in ((0,0),(1,1),(-1,-1),(None,None)):
                            payload = {"action":action,"target":"Body","component_index":index}
                            if joint_type is not None:
                                payload["joint_type"] = joint_type
                            if action == "configure_joint":
                                payload["properties"] = {"breakForce":2}
                            await client.call_tool("manage_physics",payload)
                            expected_wire = {key:value for key,value in payload.items() if key != "component_index"}
                            if normalized is not None:
                                expected_wire["componentIndex"] = normalized
                            check(requests[-1][2] == expected_wire,"index native-default/coercion "+mode+action+str(joint_type)+repr(index))
                            print("SELECTOR_WIRE",json.dumps({"mode":mode,"input":index,"input_type":type(index).__name__,"wire":requests[-1][2]},sort_keys=True))
                        for index in (True, False, 1.0, 0.5, '0', '1', '1e0'):
                            payload = {"action":action,"target":"Body","component_index":index}
                            if joint_type is not None:
                                payload["joint_type"] = joint_type
                            if action == "configure_joint":
                                payload["properties"] = {"breakForce":2}
                            before = len(requests)
                            error = ""
                            try:
                                await client.call_tool("manage_physics",payload)
                            except ToolError as exc:
                                error = str(exc)
                            check(bool(error),"strict selector rejected "+mode+action+str(joint_type)+repr(index))
                            if isinstance(index, bool):
                                check("component_index must be an integer, not a boolean" in error,"boolean selector diagnostic "+mode+action+str(joint_type)+repr(index))
                            check(len(requests) == before,"strict selector no transport "+mode+action+str(joint_type)+repr(index))
                            print("SELECTOR_WIRE",json.dumps({"mode":mode,"input":index,"input_type":type(index).__name__,"rejected":bool(error),"error":error,"wire":None if len(requests) == before else requests[-1][2]},sort_keys=True))
                    for payload in ({"action":"unknown"},{"action":"PING"},{"action":"configure_joint","properties":[]},{"action":"add_joint","motor":"bad"},{"action":"raycast","origin":["bad"]},{"action":"ping","component_index":1.5}):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool("manage_physics",payload)
                        except ToolError:
                            rejected = True
                        check(rejected and len(requests) == before,"schema failure before transport "+mode+repr(payload))
            class DirectContext:
                async def get_state(self, key):
                    return "Project@fixture"
            for action, joint_type in (("configure_joint","fixed"),("configure_joint",None),("remove_joint","fixed"),("remove_joint",None)):
                for index in (True, False):
                    payload = {"action":action,"target":"Body","component_index":index}
                    if joint_type is not None:
                        payload["joint_type"] = joint_type
                    if action == "configure_joint":
                        payload["properties"] = {"breakForce":2}
                    await manage_physics(DirectContext(),**payload)
                    check(requests[-1][2]["componentIndex"] is index,"direct internal wrapper preserves raw bool "+action+str(joint_type)+repr(index))
                    print("SELECTOR_WIRE",json.dumps({"mode":"direct_internal","input":index,"input_type":type(index).__name__,"wire":requests[-1][2]},sort_keys=True))
            print(f"fresh physics SDK checks={checks} failures={len(failures)} requests={len(requests)}")
            assert not failures, failures
        anyio.run(main)
    ''', tmp_path)
    assert "fresh physics SDK checks=" in output
