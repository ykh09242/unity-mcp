"""Fresh registered Volume/renderer-feature contracts at controlled transport seams."""

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
reply = {}
client_type = httpx.AsyncClient

def respond(request):
    requests.append(json.loads(request.content))
    response = copy.deepcopy(reply)
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

cases = [
    (['volume-create', '--name', 'Fixture', '--local', '--weight', '0', '--priority', '0',
      '--profile-path', 'Assets/Settings/Fixture.asset'],
     {'action': 'volume_create', 'name': 'Fixture', 'is_global': False, 'weight': 0.0,
      'priority': 0.0, 'profile_path': 'Assets/Settings/Fixture.asset'}),
    (['volume-create'], {'action': 'volume_create', 'is_global': True}),
    (['volume-add-effect', '--target', 'Fixture', '--effect', 'Bloom'],
     {'action': 'volume_add_effect', 'target': 'Fixture', 'effect': 'Bloom'}),
    (['volume-set-effect', '--target', 'Fixture', '--effect', 'Bloom', '--param', 'intensity', '0',
      '--param', 'unknown', '1'],
     {'action': 'volume_set_effect', 'target': 'Fixture', 'effect': 'Bloom',
      'parameters': {'intensity': '0', 'unknown': '1'}}),
    (['volume-remove-effect', '--target', 'Fixture', '--effect', 'Bloom'],
     {'action': 'volume_remove_effect', 'target': 'Fixture', 'effect': 'Bloom'}),
    (['volume-info', '--target', 'Fixture'], {'action': 'volume_get_info', 'target': 'Fixture'}),
    (['volume-set-properties', '--target', 'Fixture', '--local', '--weight', '0', '--priority', '0'],
     {'action': 'volume_set_properties', 'target': 'Fixture', 'is_global': False,
      'weight': 0.0, 'priority': 0.0}),
    (['volume-list-effects'], {'action': 'volume_list_effects'}),
    (['volume-create-profile', '--path', 'Assets/Settings/Fixture.asset', '--name', 'Fixture'],
     {'action': 'volume_create_profile', 'path': 'Assets/Settings/Fixture.asset', 'name': 'Fixture'}),
    (['feature-list'], {'action': 'feature_list'}),
    (['feature-add', '--type', 'FixtureFeature', '--name', 'Fixture'],
     {'action': 'feature_add', 'type': 'FixtureFeature', 'name': 'Fixture'}),
    (['feature-remove', '--index', '0', '--name', 'Second'],
     {'action': 'feature_remove', 'index': 0, 'name': 'Second'}),
    (['feature-configure', '--index', '0', '--prop', 'enabled', 'false', '--prop', 'count', '0',
      '--prop', 'label', ''],
     {'action': 'feature_configure', 'index': 0,
      'properties': {'enabled': False, 'count': 0, 'label': ''}}),
    (['feature-toggle', '--index', '0', '--name', 'Second', '--inactive'],
     {'action': 'feature_toggle', 'index': 0, 'name': 'Second', 'active': False}),
    (['feature-reorder', '--order', '1,0'], {'action': 'feature_reorder', 'order': [1, 0]}),
]
for args, wire in cases:
    for failed, wrapped in ((False, False), (False, True), (True, True)):
        reply = {'success': not failed, 'message': 'Controlled native postprocessing diagnostic',
                 'data': {'count': 0, 'active': False, 'profile': None, 'failed': []}}
        before = len(requests)
        result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', 'graphics', *args])
        sent = requests[before:]
        check(result.exit_code == (1 if failed else 0), 'CLI outcome ' + repr((args, failed, wrapped)))
        check(sent == [{'type': 'manage_graphics', 'params': wire, 'unity_instance': 'Project@fixture'}],
              'CLI literal request/instance ' + repr(args))
        check(json.loads(result.output) == reply, 'CLI single document with falsey response ' + repr(args))
        print('CLI_WIRE', json.dumps({'args': args, 'requests': sent}))
reply = {'success': True, 'data': {'count': 0}}
result = runner.invoke(cli, ['--format', 'text', 'graphics', 'volume-info', '--target', 'Fixture'])
check(result.exit_code == 0 and 'count' in result.output, 'text output preserved')
before = len(requests)
result = runner.invoke(cli, ['--format', 'json', 'graphics', 'feature-remove', '--index', 'bad'])
check(result.exit_code != 0 and len(requests) == before, 'invalid CLI integer before HTTP')
for command in (None, *graphics.commands):
    result = runner.invoke(cli, ['graphics', *([command] if command else []), '--help'])
    check(result.exit_code == 0, 'help ' + repr(command))
    print('FULL_HELP', json.dumps({'command': command, 'text': result.output}))
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

server = FastMCP('postprocessing-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
cases = [
    ({'action': 'VOLUME_CREATE', 'name': 'Fixture', 'profile_path': 'Assets/Settings/Fixture.asset',
      'is_global': False, 'weight': 0, 'priority': 0,
      'effects': [{'type': 'Bloom', 'intensity': 1, 'parameters': {'intensity': 0}}]},
     {'action': 'volume_create', 'name': 'Fixture', 'profile_path': 'Assets/Settings/Fixture.asset',
      'is_global': False, 'weight': 0.0, 'priority': 0.0,
      'effects': [{'type': 'Bloom', 'intensity': 1, 'parameters': {'intensity': 0}}]}),
    ({'action': 'volume_create', 'name': 'Fixture', 'profile_path': 'Assets/Settings/Fixture.asset', 'effects': []},
     {'action': 'volume_create', 'name': 'Fixture', 'profile_path': 'Assets/Settings/Fixture.asset', 'effects': []}),
    ({'action': 'volume_create', 'name': '', 'profile_path': None, 'effects': None},
     {'action': 'volume_create', 'name': ''}),
    ({'action': 'volume_add_effect', 'target': 'Fixture', 'effect': 'Bloom'},
     {'action': 'volume_add_effect', 'target': 'Fixture', 'effect': 'Bloom'}),
    ({'action': 'volume_add_effect', 'target': 'Fixture', 'effect': 'Unknown'},
     {'action': 'volume_add_effect', 'target': 'Fixture', 'effect': 'Unknown'}),
    ({'action': 'volume_set_effect', 'target': 'Fixture', 'effect': 'Bloom',
      'parameters': {'intensity': 0, 'unknown': 1}},
     {'action': 'volume_set_effect', 'target': 'Fixture', 'effect': 'Bloom',
      'parameters': {'intensity': 0, 'unknown': 1}}),
    ({'action': 'volume_set_effect', 'target': 'Fixture', 'effect': 'Bloom', 'parameters': {}},
     {'action': 'volume_set_effect', 'target': 'Fixture', 'effect': 'Bloom', 'parameters': {}}),
    ({'action': 'volume_remove_effect', 'target': 'Fixture', 'effect': 'Bloom'},
     {'action': 'volume_remove_effect', 'target': 'Fixture', 'effect': 'Bloom'}),
    ({'action': 'volume_get_info', 'target': 'Fixture'}, {'action': 'volume_get_info', 'target': 'Fixture'}),
    ({'action': 'volume_set_properties', 'target': 'Fixture', 'weight': 0, 'is_global': False,
      'properties': {'priority': 0}},
     {'action': 'volume_set_properties', 'target': 'Fixture', 'weight': 0.0, 'is_global': False,
      'properties': {'priority': 0}}),
    ({'action': 'volume_list_effects'}, {'action': 'volume_list_effects'}),
    ({'action': 'volume_create_profile', 'path': 'Assets/Settings/Fixture.asset'},
     {'action': 'volume_create_profile', 'path': 'Assets/Settings/Fixture.asset'}),
    ({'action': 'feature_list'}, {'action': 'feature_list'}),
    ({'action': 'feature_add', 'feature_type': 'FixtureFeature', 'name': 'Fixture',
      'properties': {'count': 0, 'enabled': False}, 'material': 'Assets/Fixture.mat'},
     {'action': 'feature_add', 'type': 'FixtureFeature', 'name': 'Fixture',
      'properties': {'count': 0, 'enabled': False}, 'material': 'Assets/Fixture.mat'}),
    ({'action': 'feature_remove', 'index': 0, 'name': 'Second'},
     {'action': 'feature_remove', 'index': 0, 'name': 'Second'}),
    ({'action': 'feature_configure', 'index': 0, 'properties': {'count': 0, 'enabled': False},
      'settings': {'count': 1}},
     {'action': 'feature_configure', 'index': 0, 'properties': {'count': 0, 'enabled': False},
      'settings': {'count': 1}}),
    ({'action': 'feature_toggle', 'index': 0, 'name': 'Second', 'active': False},
     {'action': 'feature_toggle', 'index': 0, 'name': 'Second', 'active': False}),
    *[({'action': 'feature_toggle', 'index': 1, **({'active': active} if active is not None else {})},
       {'action': 'feature_toggle', 'index': 1, **({'active': active} if active is not None else {})})
      for active in (None, False, True)],
    ({'action': 'feature_reorder', 'order': [1, 0]}, {'action': 'feature_reorder', 'order': [1, 0]}),
    ({'action': 'feature_reorder', 'order': []}, {'action': 'feature_reorder', 'order': []}),
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
            check('manage_graphics' in discovered, 'actual registry discovery ' + mode)
            print('FULL_SCHEMA', mode, json.dumps(discovered['manage_graphics'].inputSchema, sort_keys=True))
            for payload, wire in cases:
                for failed, wrapped in ((False, False), (True, True)):
                    response = {'success': not failed, 'message': 'Controlled native postprocessing diagnostic',
                                'data': {'count': 0, 'active': False, 'profile': None, 'failed': []}}
                    raw = {'status': 'success', 'result': response} if wrapped else response
                    result, sent = await invoke(client, payload)
                    check(sent == [('Project@fixture', 'manage_graphics', wire)], 'SDK literal wire ' + mode + repr(payload))
                    check(result.structured_content == response, 'native document/falsey data ' + mode)
            raw = {'success': True, 'data': {'count': 0}}
            for action in ('feature_remove', 'feature_toggle', 'feature_configure'):
                for value in (True, False, 0, 1, -1, '0', '1', '1e0', 1.0, 0.5, None):
                    payload = {'action': action, 'index': value, 'name': 'First'}
                    if action == 'feature_toggle':
                        payload['active'] = False
                    if action == 'feature_configure':
                        payload['properties'] = {'count': 0}
                    result, sent = await invoke(client, payload)
                    if isinstance(value, bool):
                        check(result.is_error and not sent,
                              'boolean selector rejected before transport ' + mode + repr(payload))
                        text = ' '.join(getattr(block, 'text', '') for block in result.content)
                        check('index' in text and 'valid integer' in text and 'int_type' in text,
                              'strict integer diagnostic ' + mode + repr(payload))
                        continue
                    if isinstance(value, (str, float)):
                        check(result.is_error and not sent,
                              'strict selector rejected before transport ' + mode + repr(payload))
                        continue
                    expected = {key: value for key, value in payload.items() if value is not None}
                    if value is not None:
                        expected['index'] = int(value)
                    check(sent == [('Project@fixture', 'manage_graphics', expected)],
                          'typed SDK selector mapping ' + mode + repr(payload))
            for payload in (
                {'action': 'invalid'},
                {'action': 'volume_set_effect', 'parameters': 'bad'},
                {'action': 'volume_create', 'effects': [1]},
                {'action': 'feature_configure', 'properties': []},
                {'action': 'feature_toggle', 'index': 0, 'active': False, 'renderer_index': True},
            ):
                result, sent = await invoke(client, payload)
                check(not sent and (result.is_error or result.structured_content.get('success') is False),
                      'schema/local rejection before transport ' + mode + repr(payload))
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
"""


def test_postprocessing_actual_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_postprocessing_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)
