"""Public lighting/environment contracts with fresh, isolated SDK and CLI children."""

import os
import subprocess
import sys


def _run(program, tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


CLI_PROGRAM = r"""
import copy
import json
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.commands.graphics import graphics
from cli.utils import connection

requests, failures = [], []
checks = 0
wrapped = False
fail_at = None
step = 0
reply = {}
client_type = httpx.AsyncClient
created_id = 321
created = {'success': True, 'data': {'instanceID': created_id, 'count': 0, 'enabled': False}}

def respond(request):
    global step
    value = json.loads(request.content)
    requests.append(value)
    step += 1
    if reply:
        response = copy.deepcopy(reply)
    elif step == fail_at:
        response = {'success': False, 'error': 'Native light step rejected',
                    'data': {'count': 0, 'enabled': False, 'reference': None}}
    elif value['type'] == 'manage_gameobject':
        response = copy.deepcopy(created)
    else:
        response = {'success': True, 'data': {'changed': False, 'count': 0}}
    return httpx.Response(200, json={'status': 'success', 'result': response} if wrapped else response)

connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()

def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)

for created_id in (321, -321):
    created['data']['instanceID'] = created_id
    wires = [
        ('manage_gameobject', {'action': 'create', 'name': 'Fixture', 'position': [0.0, 3.0, 0.0]}),
        ('manage_components', {'action': 'add', 'target': created_id, 'search_method': 'by_id',
                               'componentType': 'Light'}),
        ('manage_components', {'action': 'set_property', 'target': created_id, 'search_method': 'by_id',
                               'componentType': 'Light', 'property': 'type', 'value': 'Directional'}),
        ('manage_components', {'action': 'set_property', 'target': created_id, 'search_method': 'by_id',
                               'componentType': 'Light', 'property': 'color',
                               'value': {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 1}}),
        ('manage_components', {'action': 'set_property', 'target': created_id, 'search_method': 'by_id',
                               'componentType': 'Light', 'property': 'intensity', 'value': 0.0}),
    ]
    for wrapped in (False, True):
        for fail_at in (None, 1, 2, 3, 4, 5):
            step = 0
            before = len(requests)
            args = ['create', 'Fixture', '--type', 'Directional', '--color', '0', '0', '0', '--intensity', '0']
            result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', 'lighting', *args])
            sent = requests[before:]
            label = repr((created_id, wrapped, fail_at))
            expected_count = fail_at if fail_at else len(wires)
            check([(row['type'], row['params']) for row in sent] == wires[:expected_count],
                  'created ID and no sends after failure ' + label)
            check(all(row['unity_instance'] == 'Project@fixture' for row in sent), 'light instance ' + label)
            check(result.exit_code == (1 if fail_at else 0), 'light exit ' + label)
            try:
                document = json.loads(result.output)
            except ValueError:
                document = None
            expected = {'success': False, 'error': 'Native light step rejected',
                        'data': {'count': 0, 'enabled': False, 'reference': None}} if fail_at else created
            check(document == expected, 'light one JSON document ' + label)
            print('CLI_WIRE', json.dumps({'group': 'lighting', 'args': args, 'fail_at': fail_at, 'requests': sent}))
fail_at = None
step = 0
result = runner.invoke(cli, ['--format', 'text', 'lighting', 'create', 'Fixture'])
check(result.exit_code == 0 and 'Created Point light: Fixture' in result.output, 'text success notice retained')
reply = {'success': True, 'data': {}}
before = len(requests)
result = runner.invoke(cli, ['--format', 'json', 'lighting', 'create', 'Fixture'])
check(result.exit_code == 1 and len(requests) == before + 1 and 'instanceID' in result.output,
      'missing created identity prevents followups')

cases = [
    (['bake-start'], {'action': 'bake_start', 'async': True}),
    (['bake-start', '--sync'], {'action': 'bake_start', 'async': False}),
    *[([command], {'action': action}) for command, action in (
        ('bake-cancel', 'bake_cancel'), ('bake-status', 'bake_status'),
        ('bake-clear', 'bake_clear'), ('bake-settings', 'bake_get_settings'), ('skybox-info', 'skybox_get'))],
    (['bake-set-settings', '--setting', 'bakedGI', 'false', '--setting', 'directSampleCount', '0'],
     {'action': 'bake_set_settings', 'settings': {'bakedGI': False, 'directSampleCount': 0}}),
    (['bake-reflection-probe', '--target', 'Fixture'], {'action': 'bake_reflection_probe', 'target': 'Fixture'}),
    (['bake-create-probes', '--name', 'Fixture', '--spacing', '0'],
     {'action': 'bake_create_light_probe_group', 'name': 'Fixture', 'spacing': 0.0}),
    (['bake-create-reflection', '--name', 'Fixture', '--resolution', '128', '--mode', 'Baked'],
     {'action': 'bake_create_reflection_probe', 'name': 'Fixture', 'resolution': 128, 'mode': 'Baked'}),
    (['skybox-set-material', '--material', 'Assets/Fixture.mat'],
     {'action': 'skybox_set_material', 'material': 'Assets/Fixture.mat'}),
    (['skybox-set-properties', '--prop', '_Count', '16777217', '--prop', '_Exposure', '0'],
     {'action': 'skybox_set_properties', 'properties': {'_Count': 16777217, '_Exposure': 0}}),
    (['skybox-set-ambient', '--mode', 'Flat', '--intensity', '0', '--color', '0,0,0,0'],
     {'action': 'skybox_set_ambient', 'ambient_mode': 'Flat', 'intensity': 0.0, 'color': [0.0, 0.0, 0.0, 0.0]}),
    (['skybox-set-fog', '--disable', '--mode', 'Linear', '--density', '0', '--start', '0', '--end', '0'],
     {'action': 'skybox_set_fog', 'fog_enabled': False, 'fog_mode': 'Linear', 'fog_density': 0.0,
      'fog_start': 0.0, 'fog_end': 0.0}),
    (['skybox-set-reflection', '--intensity', '0', '--bounces', '0', '--mode', 'Custom',
      '--cubemap', 'Assets/Missing.cubemap'],
     {'action': 'skybox_set_reflection', 'intensity': 0.0, 'bounces': 0,
      'reflection_mode': 'Custom', 'path': 'Assets/Missing.cubemap'}),
    (['skybox-set-sun', '--target', 'Fixture'], {'action': 'skybox_set_sun', 'target': 'Fixture'}),
]
for args, wire in cases:
    for failed, wrapped in ((False, False), (True, True)):
        reply = {'success': not failed, 'message': 'Controlled environment diagnostic',
                 'data': {'count': 0, 'enabled': False, 'reference': None}}
        before = len(requests)
        result = runner.invoke(cli, ['--format', 'json', 'graphics', *args])
        check(result.exit_code == (1 if failed else 0), 'graphics exit ' + repr(args))
        check(len(requests) == before + 1 and requests[-1]['params'] == wire, 'graphics wire ' + repr(args))
        check(json.loads(result.output) == reply, 'graphics one document ' + repr(args))
        print('CLI_WIRE', json.dumps({'group': 'graphics', 'args': args, 'requests': requests[before:]}))
for group, commands in (('graphics', [None, *graphics.commands]), ('lighting', [None, 'create'])):
    for command in commands:
        result = runner.invoke(cli, [group, *([command] if command else []), '--help'])
        check(result.exit_code == 0, 'help ' + repr((group, command)))
        print('FULL_HELP', json.dumps({'group': group, 'command': command, 'text': result.output}))
print('CLI_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
assert not failures, failures
"""


SDK_PROGRAM = r"""
import copy
import json
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from services.tools import register_all_tools
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

async def controlled_send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    return copy.deepcopy(raw)

PluginHub.send_command_for_instance = staticmethod(controlled_send)

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

server = FastMCP('lighting-environment-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
cases = [
    ({'action': action}, {'action': action})
    for action in ('bake_cancel', 'bake_status', 'bake_clear', 'bake_get_settings', 'skybox_get')
] + [
    ({'action': 'BAKE_START', 'async_bake': False}, {'action': 'bake_start', 'async': False}),
    ({'action': 'bake_start', 'async_bake': None}, {'action': 'bake_start'}),
    ({'action': 'bake_reflection_probe', 'target': 'Fixture'}, {'action': 'bake_reflection_probe', 'target': 'Fixture'}),
    ({'action': 'bake_set_settings', 'settings': {'bakedGI': False, 'directSampleCount': 0}},
     {'action': 'bake_set_settings', 'settings': {'bakedGI': False, 'directSampleCount': 0}}),
    ({'action': 'bake_set_settings', 'settings': {}}, {'action': 'bake_set_settings', 'settings': {}}),
    ({'action': 'bake_set_settings', 'settings': None}, {'action': 'bake_set_settings'}),
    *[({'action': 'bake_set_settings', 'settings': settings},
       {'action': 'bake_set_settings', 'settings': settings})
      for settings in ({'unknown': 1}, {'lightmapper': 'bad'}, {'bakedGI': False, 'unknown': 1})],
    ({'action': 'bake_create_light_probe_group', 'name': 'Fixture', 'grid_size': [2, 1, 2],
      'spacing': 0, 'position': [0, -1, 2]},
     {'action': 'bake_create_light_probe_group', 'name': 'Fixture', 'grid_size': [2, 1, 2],
      'spacing': 0.0, 'position': [0.0, -1.0, 2.0]}),
    ({'action': 'bake_create_reflection_probe', 'name': 'Fixture', 'resolution': 256,
      'position': [0, 0, 0], 'size': [4, 5, 6], 'hdr': False, 'box_projection': False, 'mode': 'Baked'},
     {'action': 'bake_create_reflection_probe', 'name': 'Fixture', 'resolution': 256,
      'position': [0.0, 0.0, 0.0], 'size': [4.0, 5.0, 6.0],
      'hdr': False, 'box_projection': False, 'mode': 'Baked'}),
    ({'action': 'bake_set_probe_positions', 'target': 'Fixture', 'positions': []},
     {'action': 'bake_set_probe_positions', 'target': 'Fixture', 'positions': []}),
    *[({'action': 'bake_set_probe_positions', 'target': 'Fixture', 'positions': positions},
       {'action': 'bake_set_probe_positions', 'target': 'Fixture',
        'positions': [[float(value) for value in row] for row in positions]})
      for positions in ([[0, -1, 2], [0, 0, 0]], [[0, 0, 0], [1, 2]])],
    ({'action': 'skybox_set_material', 'material': 'Assets/Fixture.mat', 'path': 'Assets/Other.mat'},
     {'action': 'skybox_set_material', 'material': 'Assets/Fixture.mat', 'path': 'Assets/Other.mat'}),
    ({'action': 'skybox_set_properties', 'properties': {'_Count': 16777217}, 'parameters': {'_Count': 0}},
     {'action': 'skybox_set_properties', 'properties': {'_Count': 16777217}, 'parameters': {'_Count': 0}}),
    ({'action': 'skybox_set_ambient', 'ambient_mode': 'Flat', 'intensity': 0, 'color': [0, 0, 0, 0],
      'equator_color': [], 'ground_color': None},
     {'action': 'skybox_set_ambient', 'ambient_mode': 'Flat', 'intensity': 0.0,
      'color': [0.0, 0.0, 0.0, 0.0], 'equator_color': []}),
    ({'action': 'skybox_set_ambient', 'ambient_mode': '99', 'color': [0, 0, 0]},
     {'action': 'skybox_set_ambient', 'ambient_mode': '99', 'color': [0.0, 0.0, 0.0]}),
    ({'action': 'skybox_set_fog', 'fog_enabled': False, 'fog_mode': 'Linear',
      'fog_density': 0, 'fog_start': 0, 'fog_end': 0},
     {'action': 'skybox_set_fog', 'fog_enabled': False, 'fog_mode': 'Linear',
      'fog_density': 0.0, 'fog_start': 0.0, 'fog_end': 0.0}),
    ({'action': 'skybox_set_reflection', 'intensity': 0, 'bounces': 0,
      'reflection_mode': 'Custom', 'path': 'Assets/Missing.cubemap'},
     {'action': 'skybox_set_reflection', 'intensity': 0.0, 'bounces': 0,
      'reflection_mode': 'Custom', 'path': 'Assets/Missing.cubemap'}),
    ({'action': 'skybox_set_sun', 'target': 'Fixture'}, {'action': 'skybox_set_sun', 'target': 'Fixture'}),
]

async def invoke(client, payload):
    before = len(requests)
    result = await client.call_tool('manage_graphics', payload, raise_on_error=False)
    sent = requests[before:]
    print('SDK_WIRE', json.dumps({'mode': client._fixture_mode, 'payload': payload, 'wires': sent,
                                  'response': result.structured_content, 'is_error': result.is_error}))
    return result, sent

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            client._fixture_mode = mode
            discovered = {tool.name: tool for tool in await client.list_tools()}
            check('manage_graphics' in discovered, 'real registered discovery ' + mode)
            print('FULL_SCHEMA', mode, json.dumps(discovered['manage_graphics'].inputSchema, sort_keys=True))
            for payload, wire in cases:
                for failed, wrapped in ((False, False), (True, True)):
                    response = {'success': not failed, 'message': 'Controlled environment diagnostic',
                                'data': {'count': 0, 'enabled': False, 'reference': None}}
                    raw = {'status': 'success', 'result': response} if wrapped else response
                    result, sent = await invoke(client, payload)
                    check(sent == [('Project@fixture', 'manage_graphics', wire)], 'SDK wire ' + mode + repr(payload))
                    check(result.structured_content == response, 'SDK native error and falsey data ' + mode)
            for payload in (
                {'action': 'invalid'},
                {'action': 'bake_set_settings', 'settings': 'bad'},
                {'action': 'skybox_set_properties', 'properties': []},
                {'action': 'skybox_set_ambient', 'color': [0, 'bad', 0]},
                {'action': 'skybox_set_fog', 'fog_color': [0, 'bad', 0]},
            ):
                result, sent = await invoke(client, payload)
                check(not sent and (result.is_error or result.structured_content.get('success') is False),
                      'invalid container/vector before transport ' + mode + repr(payload))
            raw = {'success': True, '_mcp_status': 'pending', '_mcp_poll_interval': 2.0,
                   'data': {'mode': 'async', 'count': 0}}
            result, sent = await invoke(client, {'action': 'bake_start', 'async_bake': True})
            check(result.structured_content == raw and sent[0][2] == {'action': 'bake_start', 'async': True},
                  'pending bake metadata preserved ' + mode)
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
"""


def test_lighting_environment_actual_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_lighting_environment_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)
