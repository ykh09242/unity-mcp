"""Actual public ProBuilder routing and JSON contracts with isolated transport seams."""

import os
import subprocess
import sys


def _run(program, tmp_path):
    env = {
        **os.environ, "APPDATA": str(tmp_path), "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program], env=env,
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


CLI_PROGRAM = r'''
import copy
import json
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.commands.probuilder import probuilder
from cli.utils import connection

requests, failures = [], []
checks = 0
reply = {}
wrapped = False
client_type = httpx.AsyncClient

def respond(request):
    requests.append(json.loads(request.content))
    value = copy.deepcopy(reply)
    return httpx.Response(200, json={'status': 'success', 'result': value} if wrapped else value)

connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()

def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)

def wire(action, properties=None, target='Fixture', selector=None):
    value = {'action': action}
    if target is not None:
        value['target'] = target
    if properties is not None:
        value['properties'] = properties
    if selector is not None:
        value['searchMethod'] = selector
    return value

cases = [
    (['create-shape', 'Cube', '--name', 'Fixture', '--position', '0', '-1', '2',
      '--params', '{"width":2,"height":3,"depth":1}'],
     wire('create_shape', {'shapeType': 'Cube', 'name': 'Fixture', 'position': [0.0, -1.0, 2.0],
                           'width': 2, 'height': 3, 'depth': 1}, None)),
    (['create-poly', '--points', '[[0,0,0],[1,0,0],[0,0,1]]', '--height', '0', '--name', 'Fixture'],
     wire('create_poly_shape', {'points': [[0, 0, 0], [1, 0, 0], [0, 0, 1]],
                                'extrudeHeight': 0.0, 'name': 'Fixture'}, None)),
    (['extrude-faces', 'Fixture', '--faces', '[0]', '--distance', '0'],
     wire('extrude_faces', {'faceIndices': [0], 'distance': 0.0, 'method': 'FaceNormal'})),
    (['extrude-edges', 'Fixture', '--edges', '[{"a":"0","b":1.0}]', '--distance', '0', '--no-group'],
     wire('extrude_edges', {'edges': [{'a': '0', 'b': 1.0}], 'distance': 0.0, 'asGroup': False})),
    (['bevel-edges', 'Fixture', '--edges', '[0,1]', '--amount', '0'],
     wire('bevel_edges', {'edgeIndices': [0, 1], 'amount': 0.0})),
    (['delete-faces', 'Fixture', '--faces', '[]'], wire('delete_faces', {'faceIndices': []})),
    (['subdivide', 'Fixture'], wire('subdivide')),
    (['select-faces', 'Fixture', '--direction', 'up', '--tolerance', '0', '--grow-angle', '0'],
     wire('select_faces', {'direction': 'up', 'tolerance': 0.0, 'growAngle': 0.0})),
    (['move-vertices', 'Fixture', '--vertices', '[0]', '--offset', '0', '-1', '2'],
     wire('move_vertices', {'vertexIndices': [0], 'offset': [0.0, -1.0, 2.0]})),
    (['weld-vertices', 'Fixture', '--vertices', '[0]', '--radius', '0'],
     wire('weld_vertices', {'vertexIndices': [0], 'radius': 0.0})),
    (['set-material', 'Fixture', '--faces', '[0]', '--material', 'Assets/Fixture.mat'],
     wire('set_face_material', {'faceIndices': [0], 'materialPath': 'Assets/Fixture.mat'})),
    (['info', '123', '--include', 'all', '--search-method', 'by_name'],
     wire('get_mesh_info', {'include': 'all'}, '123', 'by_name')),
    (['auto-smooth', 'Fixture', '--angle', '0'], wire('auto_smooth', {'angleThreshold': 0.0})),
    (['set-smoothing', 'Fixture', '--faces', '[0]', '--group', '0'],
     wire('set_smoothing', {'faceIndices': [0], 'smoothingGroup': 0})),
    (['center-pivot', 'Fixture'], wire('center_pivot')),
    (['set-pivot', 'Fixture', '--position', '0', '-1', '2'],
     wire('set_pivot', {'position': [0.0, -1.0, 2.0]})),
    (['freeze-transform', 'Fixture'], wire('freeze_transform')),
    (['validate', 'Fixture'], wire('validate_mesh')),
    (['repair', 'Fixture'], wire('repair_mesh')),
    (['raw', 'convert_to_probuilder', 'Fixture', '--params', '{"preserve":false}'],
     wire('convert_to_probuilder', {'preserve': False})),
]
for args, expected in cases:
    for failed, wrapped in ((False, False), (False, True), (True, False), (True, True)):
        reply = {'success': not failed, 'message': 'Controlled ProBuilder diagnostic',
                 'data': {'count': 0, 'changed': False, 'selection': [], 'material': None}}
        before = len(requests)
        result = runner.invoke(
            cli, ['--format', 'json', '--instance', 'Project@fixture', 'probuilder', *args],
        )
        sent = requests[before:]
        check(result.exit_code == (1 if failed else 0), 'CLI exit ' + repr(args))
        check(sent == [{'type': 'manage_probuilder', 'params': expected, 'unity_instance': 'Project@fixture'}],
              'CLI values/selector ' + repr(args))
        try:
            document = json.loads(result.output)
        except json.JSONDecodeError:
            document = None
        check(document == reply, 'single JSON diagnostic document ' + repr(args))
        print('CLI_WIRE', json.dumps({'args': args, 'requests': sent, 'response': reply}))
raw_cases = [
    ({'size': 1, 'name': 'Flat', 'properties': {'shapeType': 'Cube', 'name': 'Nested', 'size': 2}},
     {'size': 2, 'name': 'Nested', 'shapeType': 'Cube'}),
    ({'size': 1, 'name': 'Flat', 'properties': '{"shapeType":"Cube","name":"Nested","size":2}'},
     {'size': 2, 'name': 'Nested', 'shapeType': 'Cube'}),
    ({'size': 1, 'name': 'Flat', 'properties': '  {"shapeType":"Cube","name":"Nested","size":2}  '},
     {'size': 2, 'name': 'Nested', 'shapeType': 'Cube'}),
    ({'size': 0, 'enabled': False, 'properties': '{"reference":null}'},
     {'size': 0, 'enabled': False, 'reference': None}),
    ({'size': 0, 'properties': None}, {'size': 0}),
    ({'size': 0, 'properties': {}}, {'size': 0}),
    *[({'size': 1, 'properties': value}, value) for value in ('bad', '[]', 'null', '')],
]
for payload, expected_props in raw_cases:
    reply = {'success': False, 'message': 'Controlled native property diagnostic'}
    result = runner.invoke(
        cli, ['--format', 'json', 'probuilder', 'raw', 'create_shape', '--params', json.dumps(payload)],
    )
    sent = requests[-1]['params']
    check(sent == {'action': 'create_shape', 'properties': expected_props},
          'nested object/string precedence or native malformed preservation ' + repr(payload))
    check(result.exit_code == 1 and json.loads(result.output) == reply, 'raw error preserved')
    print('CLI_WIRE', json.dumps({'args': ['raw', 'create_shape'], 'payload': payload,
                                 'requests': [requests[-1]], 'response': reply}))
for args in (
    ['create-shape', 'Cube', '--params', '[]'],
    ['create-poly', '--points', '{}'],
    ['set-smoothing', 'Fixture', '--faces', 'bad', '--group', '0'],
):
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', 'probuilder', *args])
    check(result.exit_code != 0 and len(requests) == before, 'invalid JSON no HTTP ' + repr(args))
reply = {'success': True, 'data': {'count': 0}}
result = runner.invoke(cli, ['--format', 'text', 'probuilder', 'create-shape', 'Cube'])
check(result.exit_code == 0 and 'Created ProBuilder Cube' in result.output, 'text notice retained')
for command in (None, *probuilder.commands):
    result = runner.invoke(cli, ['probuilder', *([command] if command else []), '--help'])
    check(result.exit_code == 0, 'help ' + repr(command))
    print('FULL_HELP', json.dumps({'command': command, 'text': result.output}))
print('CLI_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
assert not failures, failures
'''


SDK_PROGRAM = r'''
import copy
import json
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from services.tools import register_all_tools
from services.tools.manage_probuilder import ALL_ACTIONS
from core.config import config
from transport.plugin_hub import PluginHub

config.transport_mode = 'http'
config.http_remote_hosted = False
requests, failures = [], []
checks = 0
raw = {}

def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)

async def send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    return copy.deepcopy(raw)

PluginHub.send_command_for_instance = staticmethod(send)

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

server = FastMCP('probuilder-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
server.enable(tags={'group:probuilder'}, components={'tool'})
cases = [
    {'action': 'CREATE_SHAPE', 'properties': {'shapeType': 'Cube', 'name': 'Fixture', 'size': 2,
                                           'position': [0, -1, 2]}},
    {'action': 'create_poly_shape', 'properties': {'name': 'Fixture', 'points': [[0, 0, 0], [1, 0, 0], [0, 0, 1]],
                                                'extrudeHeight': 0, 'flipNormals': False}},
    {'action': 'convert_to_probuilder', 'target': 'Fixture'},
    {'action': 'set_smoothing', 'target': 'Fixture', 'properties': {'faceIndices': [0], 'smoothingGroup': 0}},
    {'action': 'auto_smooth', 'target': 'Fixture', 'properties': {'angleThreshold': 0}},
    {'action': 'auto_smooth', 'target': 'Fixture', 'properties': {'faceIndices': [1], 'angleThreshold': 30}},
    {'action': 'auto_smooth', 'target': 'Fixture', 'properties': {'faceIndices': [], 'angleThreshold': 0}},
    {'action': 'set_pivot', 'target': 'Fixture', 'properties': {'position': [0, -1, 2]}},
    {'action': 'set_pivot', 'target': 'Fixture', 'properties': {'position': 'bad'}},
    {'action': 'freeze_transform', 'target': 'Fixture'},
    {'action': 'set_smoothing', 'target': 'Fixture', 'properties': {'faceIndices': None, 'smoothingGroup': 0}},
    {'action': 'subdivide', 'target': 'Fixture', 'properties': {'faceIndices': []}},
    {'action': 'move_vertices', 'target': '123', 'search_method': 'by_name',
     'properties': {'vertexIndices': ['0', 0], 'offset': ['1', '0', '0']}},
    {'action': 'extrude_edges', 'target': 'Fixture', 'properties': {'edges': [{'a': '0', 'b': 1.0}],
                                                               'distance': 0, 'asGroup': False}},
    *[{'action': 'ping', 'target': target, 'properties': properties}
      for target, properties in ((None, None), ('', {}), ('0', ''), ('Root/Fixture', '{"enabled":false,"count":0,"ref":null}'))],
    *[{'action': action, 'target': 'Fixture', 'properties': {}} for action in ALL_ACTIONS],
]

async def invoke(client, payload, mode):
    before = len(requests)
    result = await client.call_tool('manage_probuilder', payload, raise_on_error=False)
    sent = requests[before:]
    print('SDK_WIRE', json.dumps({'mode': mode, 'payload': payload, 'wires': sent,
                                 'response': result.structured_content, 'is_error': result.is_error}))
    return result, sent

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            discovered = {tool.name: tool for tool in await client.list_tools()}
            check('manage_probuilder' in discovered, 'actual registry discovery ' + mode)
            print('FULL_SCHEMA', mode, json.dumps(discovered['manage_probuilder'].inputSchema, sort_keys=True))
            for original in cases:
                representations = [original]
                if isinstance(original.get('properties'), dict):
                    representations.append({**original, 'properties': json.dumps(original['properties'])})
                for payload in representations:
                    for failed, wrapped in ((False, False), (True, True)):
                        response = {'success': not failed, 'message': 'Controlled ProBuilder diagnostic',
                                    'data': {'count': 0, 'changed': False, 'selection': [], 'material': None}}
                        raw = {'status': 'success', 'result': response} if wrapped else response
                        result, sent = await invoke(client, payload, mode)
                        expected = {key: value for key, value in payload.items() if value is not None}
                        expected['action'] = payload['action'].lower()
                        if 'search_method' in expected:
                            expected['searchMethod'] = expected.pop('search_method')
                        check(sent == [('Project@fixture', 'manage_probuilder', expected)],
                              'literal properties/target representation ' + mode)
                        check(result.structured_content == response, 'native result/falsey fidelity ' + mode)
            raw = {'success': True, 'data': {}}
            for payload in (
                {'action': 'unknown'}, {'action': ' create_shape '},
                {'action': 'freeze_transform', 'target': 1},
                {'action': 'set_smoothing', 'properties': []},
                {'action': 'get_mesh_info', 'search_method': 'invalid'},
            ):
                result, sent = await invoke(client, payload, mode)
                check(not sent and (result.is_error or result.structured_content.get('success') is False),
                      'local/schema rejection before dispatch ' + mode + repr(payload))
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
'''


def test_probuilder_top_level_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_probuilder_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)
