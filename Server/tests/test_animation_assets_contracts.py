"""Fresh registered SDK and CLI animation asset contract regressions."""

import os
import subprocess
import sys
import textwrap


def _run(code, tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
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


def test_animation_assets_cli_values_merging_and_results(tmp_path):
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
        runner = CliRunner()
        path, controller = "Assets/Anim/Nested/Fixture.anim", "Assets/Fixture.controller"
        cases = [
            (["clip","create",path], {"action":"clip_create","clipPath":path,"properties":{"length":1,"loop":False,"frameRate":60}}),
            (["clip","create",r"Assets\Anim\Fixture","--name","Fixture","--length","0","--frame-rate","0","--loop"], {"action":"clip_create","clipPath":r"Assets\Anim\Fixture","properties":{"name":"Fixture","length":0,"loop":True,"frameRate":0}}),
            (["clip","info",path], {"action":"clip_get_info","clipPath":path}),
            (["clip","add-curve",path,"--property","m_LocalPosition.x","--keys",'[{"time":0,"value":0},[1,2,3]]'], {"action":"clip_add_curve","clipPath":path,"properties":{"propertyPath":"m_LocalPosition.x","type":"Transform","keys":[{"time":0,"value":0},[1,2,3]]}}),
            (["clip","set-curve",path,"--property","m_LocalPosition.x","--keys","[]"], {"action":"clip_set_curve","clipPath":path,"properties":{"propertyPath":"m_LocalPosition.x","type":"Transform","keys":[]}}),
            (["clip","set-vector-curve",path,"--property","localPosition","--keys",'[{"time":0,"value":[0,-1,0]}]'], {"action":"clip_set_vector_curve","clipPath":path,"properties":{"property":"localPosition","type":"Transform","keys":[{"time":0,"value":[0,-1,0]}]}}),
            (["clip","create-preset",path,"bounce","--duration","0","--amplitude","0","--no-loop"], {"action":"clip_create_preset","clipPath":path,"properties":{"preset":"bounce","duration":0,"amplitude":0,"loop":False}}),
            (["clip","assign","0",path,"--search-method","by_name"], {"action":"clip_assign","target":"0","clipPath":path,"searchMethod":"by_name"}),
            (["clip","add-event",path,"--function","OnEvent","--time","0"], {"action":"clip_add_event","clipPath":path,"properties":{"functionName":"OnEvent","time":0,"stringParameter":"","floatParameter":0,"intParameter":0}}),
            (["clip","remove-event",path,"--event-index","0","--function","OnEvent","--time","0"], {"action":"clip_remove_event","clipPath":path,"properties":{"eventIndex":0,"functionName":"OnEvent","time":0}}),
            (["controller","create",controller], {"action":"controller_create","controllerPath":controller}),
            (["controller","add-state",controller,"Fixture","--clip-path",path,"--speed","0","--layer-index","0"], {"action":"controller_add_state","controllerPath":controller,"clipPath":path,"properties":{"stateName":"Fixture","speed":0,"isDefault":False,"layerIndex":0}}),
            (["controller","add-transition",controller,"AnyState","Fixture","--no-exit-time","--duration","0","--conditions",'[{"parameter":"Enabled","mode":"if_not","threshold":0}]'], {"action":"controller_add_transition","controllerPath":controller,"properties":{"fromState":"AnyState","toState":"Fixture","hasExitTime":False,"duration":0,"layerIndex":0,"conditions":[{"parameter":"Enabled","mode":"if_not","threshold":0}]}}),
            (["controller","add-parameter",controller,"Enabled","--type","bool","--default-value","false"], {"action":"controller_add_parameter","controllerPath":controller,"properties":{"parameterName":"Enabled","parameterType":"bool","defaultValue":False}}),
            (["controller","add-parameter",controller,"Count","--type","int","--default-value","0"], {"action":"controller_add_parameter","controllerPath":controller,"properties":{"parameterName":"Count","parameterType":"int","defaultValue":0}}),
            (["controller","info",controller], {"action":"controller_get_info","controllerPath":controller}),
            (["controller","assign",controller,"Root/Child","--search-method","by_path"], {"action":"controller_assign","controllerPath":controller,"target":"Root/Child","searchMethod":"by_path"}),
            (["controller","add-layer",controller,"Extra","--weight","0","--blending-mode","additive"], {"action":"controller_add_layer","controllerPath":controller,"properties":{"layerName":"Extra","weight":0,"blendingMode":"additive"}}),
            (["controller","remove-layer",controller,"--layer-index","0","--layer-name","Extra"], {"action":"controller_remove_layer","controllerPath":controller,"properties":{"layerIndex":0,"layerName":"Extra"}}),
            (["controller","set-layer-weight",controller,"0","--layer-index","0","--layer-name","Extra"], {"action":"controller_set_layer_weight","controllerPath":controller,"properties":{"weight":0,"layerIndex":0,"layerName":"Extra"}}),
        ]
        for args, wire in cases:
            for failed in (False, True):
                for wrapped in (False, True):
                    expected = {"success":not failed,"message":"Controlled native diagnostic" if failed else "Done","data":{"count":0,"enabled":False,"reference":None,"items":[]}}
                    raw = {"status":"success","result":copy.deepcopy(expected)} if wrapped else copy.deepcopy(expected)
                    before = len(requests)
                    result = runner.invoke(cli,["--format","json","--instance","Project@fixture","animation",*args])
                    label = repr(args)+str(failed)+str(wrapped)
                    check(result.exit_code == (1 if failed else 0),"exit "+label)
                    try:
                        document = json.loads(result.stdout)
                    except ValueError:
                        document = None
                    check(document == expected,"single native JSON/falsey result "+label)
                    check(len(requests) == before+1 and requests[-1] == {"type":"manage_animation","unity_instance":"Project@fixture","params":wire},"asset/action wire "+label)
                    if not failed and not wrapped:
                        print("DOMAIN_WIRE",json.dumps({"input":args,"wire":requests[-1]["params"]},sort_keys=True))
        nested = {"stateName":"Nested","speed":0,"isDefault":False,"reference":None}
        raw = {"success":True,"message":"Controlled normalization response","data":{"speed":0}}
        for existing in (nested, json.dumps(nested), "  "+json.dumps(nested)+"  ", {}, "{}", None):
            params = {"controllerPath":controller,"stateName":"Flat","layerIndex":0,"properties":existing}
            result = runner.invoke(cli,["--format","json","--instance","Project@fixture","animation","raw","controller_add_state","--params",json.dumps(params)])
            expected_properties = {"stateName":"Flat","layerIndex":0,**(nested if existing in (nested,json.dumps(nested),"  "+json.dumps(nested)+"  ") else {})}
            expected_wire = {"action":"controller_add_state","controllerPath":controller,"properties":expected_properties}
            check(result.exit_code == 0 and json.loads(result.stdout) == raw,"valid merge JSON "+repr(existing))
            check(requests[-1]["params"] == expected_wire,"nested precedence/false/zero/null/string merge "+repr(existing))
            print("MERGE_WIRE",json.dumps({"existing":existing,"wire":requests[-1]["params"]},sort_keys=True))
        raw = {"success":False,"error":"'properties' must be a JSON object or a JSON string containing an object."}
        for existing in ("bad", "[]", "null", "true", "", "   ", [], [1], True, 12):
            result = runner.invoke(cli,["--format","json","animation","raw","controller_add_state","--params",json.dumps({"controllerPath":controller,"stateName":"Flat","properties":existing})])
            check(result.exit_code == 1 and json.loads(result.stdout) == raw,"malformed native diagnostic "+repr(existing))
            check(requests[-1]["params"] == {"action":"controller_add_state","controllerPath":controller,"properties":existing},"invalid container must remain invalid "+repr(existing))
        raw = {"success":True,"message":"Done"}
        standalone = json.dumps(nested)
        runner.invoke(cli,["--format","json","animation","raw","controller_add_state","--params",json.dumps({"controllerPath":controller,"properties":standalone})])
        check(requests[-1]["params"]["properties"] == standalone,"standalone string representation unchanged")
        runner.invoke(cli,["--format","json","animation","raw","clip_create","--clip-path","Assets/Flag.anim","--params",json.dumps({"clipPath":"Assets/Canonical.anim","clip_path":"Assets/Alias.anim","properties":{"clip_path":"Assets/Nested.anim","loop":False}})])
        check(requests[-1]["params"] == {"action":"clip_create","clipPath":"Assets/Canonical.anim","properties":{"clip_path":"Assets/Nested.anim","loop":False}},"canonical flags/raw and nested alias precedence unchanged")
        for args in (["clip","set-curve",path,"--property","x","--keys","{}"],["controller","add-transition",controller,"Idle","Walk","--conditions","{}"],["raw","clip_create","--params","[]"]):
            before = len(requests)
            result = runner.invoke(cli,["animation",*args])
            check(result.exit_code == 1 and len(requests) == before,"existing JSON shape guard "+repr(args))
        for group, command in (("clip","create"),("clip","add-event"),("clip","remove-event"),("controller","add-state"),("controller","add-transition"),("controller","add-parameter"),("controller","add-layer"),("controller","remove-layer"),("controller","set-layer-weight")):
            result = runner.invoke(cli,["animation",group,command,"--help"])
            check(result.exit_code == 0,"help "+group+command)
            print("FULL_HELP",group+"/"+command,json.dumps(result.stdout))
        print(f"fresh animation assets CLI checks={checks} failures={len(failures)} requests={len(requests)}")
        assert not failures, failures
    """,
        tmp_path,
    )
    assert "fresh animation assets CLI checks=" in output


def test_animation_assets_registered_sdk_contracts(tmp_path):
    output = _run(
        r"""
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
        server = FastMCP("animation-assets")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        server.enable(tags={"group:animation"})
        path, controller = "Assets/Anim/Fixture.anim", "Assets/Fixture.controller"
        cases = [
            {"action":"clip_create","clip_path":r"Assets\Anim\Nested\Fixture","properties":{"name":"Fixture","length":0,"frame_rate":0,"loop":False}},
            {"action":"CLIP_GET_INFO","clip_path":path},
            {"action":"clip_add_curve","clip_path":path,"properties":{"property_path":"m_LocalPosition.x","keys":[{"time":0,"value":0},[1,2,3]],"relative_path":"","type":"Transform"}},
            {"action":"clip_set_curve","clip_path":path,"properties":{"propertyPath":"x","keys":[]}},
            {"action":"clip_set_vector_curve","clip_path":path,"properties":{"property":"localPosition","keys":[{"time":0,"value":[0,-1,0]}]}},
            {"action":"clip_create_preset","clip_path":path,"properties":{"preset":"bounce","duration":0,"amplitude":0,"loop":False}},
            {"action":"clip_assign","target":"0","search_method":"by_name","clip_path":path},
            {"action":"clip_add_event","clip_path":path,"properties":{"function_name":"OnEvent","time":0,"string_parameter":"","float_parameter":0,"int_parameter":0}},
            {"action":"clip_remove_event","clip_path":path,"properties":{"event_index":0,"function_name":"OnEvent","time":0}},
            {"action":"controller_create","controller_path":controller},
            {"action":"controller_add_state","controller_path":controller,"clip_path":path,"properties":{"state_name":"Nested","speed":0,"is_default":False,"layer_index":0}},
            {"action":"controller_add_state","controller_path":controller,"properties":'{"stateName":"Nested","speed":0,"isDefault":false,"reference":null}'},
            {"action":"controller_add_transition","controller_path":controller,"properties":{"from_state":"AnyState","to_state":"Nested","has_exit_time":False,"duration":0,"exit_time":0,"conditions":[{"parameter":"Enabled","mode":"if_not","threshold":0}]}},
            {"action":"controller_add_parameter","controller_path":controller,"properties":{"parameter_name":"Enabled","parameter_type":"bool","default_value":False}},
            {"action":"controller_get_info","controller_path":controller},
            {"action":"controller_assign","controller_path":controller,"target":"Root/Child","search_method":"by_path"},
            {"action":"controller_add_layer","controller_path":controller,"properties":{"layer_name":"Extra","weight":0,"blending_mode":"additive"}},
            {"action":"controller_remove_layer","controller_path":controller,"properties":{"layer_index":0,"layer_name":"Extra"}},
            {"action":"controller_set_layer_weight","controller_path":controller,"properties":{"layer_index":0,"layer_name":"Extra","weight":0}},
            {"action":"clip_create","clip_path":"","controller_path":None,"target":None,"search_method":None,"properties":None},
            {"action":"clip_create","clip_path":path,"properties":{}},
            {"action":"clip_create","clip_path":path,"properties":"{}"},
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
                                value = json.loads(value) if value else None
                            if value is not None:
                                wire[{"clip_path":"clipPath","controller_path":"controllerPath","search_method":"searchMethod"}.get(key,key)] = value
                        for failed in (False, True):
                            for wrapped in (False, True):
                                expected = {"success":not failed,"message":"Controlled native diagnostic" if failed else "Done","data":{"speed":0,"hasMotion":False,"reference":None,"states":[]}}
                                raw = {"status":"success","result":copy.deepcopy(expected)} if wrapped else copy.deepcopy(expected)
                                before = len(requests)
                                result = await client.call_tool("manage_animation",payload)
                                label = mode+repr(payload)+str(failed)+str(wrapped)
                                check(result.structured_content == expected,"result/falsey native document "+label)
                                check(len(requests) == before+1 and requests[-1] == ("Project@fixture","manage_animation",wire),"asset/alias/properties wire "+label)
                                if not failed and not wrapped:
                                    print("DOMAIN_WIRE",json.dumps({"mode":mode,"input":payload,"wire":requests[-1][2]},sort_keys=True))
                    for properties in ("bad","[]","null","true",""):
                        before = len(requests)
                        result = await client.call_tool("manage_animation",{"action":"clip_create","properties":properties})
                        check(result.structured_content["success"] is False and len(requests) == before,"invalid object string local rejection "+mode+properties)
                    for payload in ({"action":"clip_unknown"},{"action":"controller_unknown"},{"action":"other"}):
                        before = len(requests)
                        result = await client.call_tool("manage_animation",payload)
                        check(result.structured_content["success"] is False and len(requests) == before,"unknown action local rejection "+mode+repr(payload))
                    for payload in ({"action":"clip_create","properties":[]},{"action":"clip_create","properties":True},{"action":"clip_assign","search_method":"bad"}):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool("manage_animation",payload)
                        except ToolError:
                            rejected = True
                        check(rejected and len(requests) == before,"schema guard before dispatch "+mode+repr(payload))
            print(f"fresh animation assets SDK checks={checks} failures={len(failures)} requests={len(requests)}")
            assert not failures, failures
        anyio.run(main)
    """,
        tmp_path,
    )
    assert "fresh animation assets SDK checks=" in output
