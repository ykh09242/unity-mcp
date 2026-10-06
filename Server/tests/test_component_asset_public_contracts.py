"""Fresh registered SDK and central CLI contracts; native results stay opaque."""

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
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


CLI_PROGRAM = r'''
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
    return httpx.Response(200, json=copy.deepcopy(raw))

connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()

def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)

component_cases = [
    (['add', 'Fixture', 'BoxCollider'],
     {'action': 'add', 'target': 'Fixture', 'componentType': 'BoxCollider'}),
    (['add', '321', 'BoxCollider', '--search-method', 'by_id',
      '--properties', '{"isTrigger":false,"size":[0,0,0]}'],
     {'action': 'add', 'target': '321', 'componentType': 'BoxCollider', 'searchMethod': 'by_id',
      'properties': {'isTrigger': False, 'size': [0, 0, 0]}}),
    (['remove', 'Fixture', 'BoxCollider', '--force'],
     {'action': 'remove', 'target': 'Fixture', 'componentType': 'BoxCollider'}),
    (['remove', 'Root/Fixture', 'BoxCollider', '--force', '--search-method', 'by_path',
      '--component-index', '1'],
     {'action': 'remove', 'target': 'Root/Fixture', 'componentType': 'BoxCollider',
      'searchMethod': 'by_path', 'componentIndex': 1}),
    (['set', 'Fixture', 'BoxCollider', 'isTrigger', 'false', '--component-index', '0'],
     {'action': 'set_property', 'target': 'Fixture', 'componentType': 'BoxCollider',
      'property': 'isTrigger', 'value': False, 'componentIndex': 0}),
    (['set', 'Fixture', 'Renderer', 'sharedMaterial', 'null'],
     {'action': 'set_property', 'target': 'Fixture', 'componentType': 'Renderer',
      'property': 'sharedMaterial', 'value': None}),
    (['set', 'Fixture', 'Owned', 'label', ''],
     {'action': 'set_property', 'target': 'Fixture', 'componentType': 'Owned',
      'property': 'label', 'value': ''}),
    (['set', 'Fixture', 'BoxCollider', 'size', '[0,-1,2]'],
     {'action': 'set_property', 'target': 'Fixture', 'componentType': 'BoxCollider',
      'property': 'size', 'value': [0, -1, 2]}),
    (['modify', 'Fixture', 'BoxCollider', '--properties', '{"isTrigger":false,"size":[0,0,0]}',
      '--component-index', '1'],
     {'action': 'set_property', 'target': 'Fixture', 'componentType': 'BoxCollider',
      'properties': {'isTrigger': False, 'size': [0, 0, 0]}, 'componentIndex': 1}),
]
asset_cases = [
    (['search', 't:Material', '--path', 'Assets', '--type', 'Material', '--limit', '0', '--page', '0'],
     {'action': 'search', 'path': 'Assets', 'searchPattern': 't:Material', 'filterType': 'Material',
      'pageSize': 0, 'pageNumber': 0}),
    (['info', 'Assets/Fixture.mat'],
     {'action': 'get_info', 'path': 'Assets/Fixture.mat', 'generatePreview': False}),
    (['info', 'Assets/Fixture.mat', '--preview'],
     {'action': 'get_info', 'path': 'Assets/Fixture.mat', 'generatePreview': True}),
    (['create', 'Assets/Fixture.mat', 'Material', '--properties', '{"_Float":0,"_Color":[0,0,0,0]}'],
     {'action': 'create', 'path': 'Assets/Fixture.mat', 'assetType': 'Material',
      'properties': {'_Float': 0, '_Color': [0, 0, 0, 0]}}),
    (['delete', 'Assets/Fixture.mat', '--force'],
     {'action': 'delete', 'path': 'Assets/Fixture.mat'}),
    (['duplicate', 'Assets/Fixture.mat', 'Assets/Owned/Copy.mat'],
     {'action': 'duplicate', 'path': 'Assets/Fixture.mat', 'destination': 'Assets/Owned/Copy.mat'}),
    (['move', 'Assets/Fixture.mat', 'Assets/Owned/Moved.mat'],
     {'action': 'move', 'path': 'Assets/Fixture.mat', 'destination': 'Assets/Owned/Moved.mat'}),
    (['rename', 'Assets/Fixture.mat', 'Renamed.mat'],
     {'action': 'rename', 'path': 'Assets/Fixture.mat', 'destination': 'Assets/Renamed.mat'}),
    (['import', 'Assets/Fixture.mat'], {'action': 'import', 'path': 'Assets/Fixture.mat'}),
    (['mkdir', 'Assets/Owned/Nested'], {'action': 'create_folder', 'path': 'Assets/Owned/Nested'}),
]
for group, cases in (('component', component_cases), ('asset', asset_cases)):
    for args, wire in cases:
        for failed in (False, True):
            for wrapped in (False, True):
                expected = {
                    'success': not failed,
                    'message': 'Controlled native diagnostic' if failed else 'Done',
                    'data': {'componentAdded': False, 'componentIndex': 0, 'assets': [],
                             'reference': None, 'errors': []},
                }
                raw = {'status': 'success', 'result': expected} if wrapped else expected
                before = len(requests)
                result = runner.invoke(
                    cli, ['--format', 'json', '--instance', 'Project@fixture', group, *args]
                )
                label = group + repr(args) + str(failed) + str(wrapped)
                check(result.exit_code == (1 if failed else 0), 'exit ' + label)
                try:
                    document = json.loads(result.stdout)
                except ValueError:
                    document = None
                check(document == expected, 'one JSON/native diagnostic/falsey ' + label)
                check(requests[before:] == [{
                    'type': 'manage_components' if group == 'component' else 'manage_asset',
                    'params': wire, 'unity_instance': 'Project@fixture',
                }], 'literal central request ' + label)
                if not failed and not wrapped:
                    print('DOMAIN_WIRE', json.dumps({
                        'surface': 'cli', 'group': group, 'input': args, 'requests': requests[before:],
                    }))

raw = {'success': True, 'message': 'Done', 'data': {}}
for group, args, notice in (
    ('component', ['add', 'Fixture', 'BoxCollider'], 'Added'),
    ('component', ['remove', 'Fixture', 'BoxCollider', '--force'], 'Removed'),
    ('component', ['set', 'Fixture', 'BoxCollider', 'isTrigger', 'false'], 'Set'),
    ('component', ['modify', 'Fixture', 'BoxCollider', '--properties', '{"isTrigger":false}'], 'Modified'),
    ('asset', ['create', 'Assets/Fixture.mat', 'Material'], 'Created'),
    ('asset', ['delete', 'Assets/Fixture.mat', '--force'], 'Deleted'),
    ('asset', ['duplicate', 'Assets/Fixture.mat', 'Assets/Copy.mat'], 'Duplicated'),
    ('asset', ['move', 'Assets/Fixture.mat', 'Assets/Moved.mat'], 'Moved'),
    ('asset', ['rename', 'Assets/Fixture.mat', 'Renamed.mat'], 'Renamed'),
    ('asset', ['import', 'Assets/Fixture.mat'], 'Imported'),
    ('asset', ['mkdir', 'Assets/Owned'], 'Created folder'),
):
    result = runner.invoke(cli, [group, *args])
    check(result.exit_code == 0 and notice in result.stdout, 'plain notice ' + group + notice)
for group, args in (
    ('component', ['add', 'Fixture', 'BoxCollider', '--properties', '[]']),
    ('component', ['modify', 'Fixture', 'BoxCollider', '--properties', '{']),
    ('component', ['remove', 'Fixture', 'BoxCollider', '--force', '--component-index', 'true']),
    ('component', ['set', 'Fixture', 'BoxCollider', 'isTrigger', 'false', '--search-method', 'bad']),
    ('asset', ['create', 'Assets/Fixture.mat', 'Material', '--properties', '[]']),
    ('asset', ['search', '*', '--limit', 'bad']),
):
    before = len(requests)
    result = runner.invoke(cli, [group, *args])
    check(result.exit_code != 0 and len(requests) == before, 'invalid before HTTP ' + repr(args))
for group, commands in (
    ('component', ('add', 'remove', 'set', 'modify')),
    ('asset', ('search', 'info', 'create', 'delete', 'duplicate', 'move', 'rename', 'import', 'mkdir')),
):
    for command in commands:
        result = runner.invoke(cli, [group, command, '--help'])
        check(result.exit_code == 0, 'help ' + group + command)
        print('FULL_HELP', group + '.' + command, json.dumps(result.stdout))
print(f'fresh component/asset CLI checks={checks} failures={len(failures)} requests={len(requests)}')
assert not failures, failures
'''


SDK_PROGRAM = r'''
import copy
import json
import anyio
from fastmcp import FastMCP, Client
from fastmcp.server.middleware import Middleware
from services.tools import register_all_tools
from services.tools.manage_components import manage_components
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
    if command == 'get_editor_state':
        return {'success': True, 'data': {'compilation': {'is_compiling': False}}}
    if command == 'get_project_info':
        return {'success': True, 'data': {}}
    return copy.deepcopy(raw)

PluginHub.send_command_for_instance = staticmethod(controlled_send)

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

class DirectContext:
    async def get_state(self, key):
        return 'Project@fixture' if key == 'unity_instance' else None

server = FastMCP('component-asset-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
component_cases = [
    ({'action': 'add', 'target': 'Fixture', 'component_type': 'BoxCollider'},
     {'action': 'add', 'target': 'Fixture', 'componentType': 'BoxCollider'}),
    ({'action': 'add', 'target': 321, 'component_type': 'BoxCollider', 'search_method': 'by_id',
      'properties': '{"isTrigger":false,"size":[0,0,0]}'},
     {'action': 'add', 'target': 321, 'componentType': 'BoxCollider', 'searchMethod': 'by_id',
      'properties': {'isTrigger': False, 'size': [0, 0, 0]}}),
    ({'action': 'add', 'target': 'Fixture', 'component_type': 'BoxCollider', 'properties': {}},
     {'action': 'add', 'target': 'Fixture', 'componentType': 'BoxCollider'}),
    ({'action': 'set_property', 'target': 'Fixture', 'component_type': 'Renderer',
      'property': 'sharedMaterial', 'value': None},
     {'action': 'set_property', 'target': 'Fixture', 'componentType': 'Renderer',
      'property': 'sharedMaterial', 'value': None}),
    ({'action': 'set_property', 'target': 'Fixture', 'component_type': 'BoxCollider',
      'properties': {'isTrigger': False, 'size': [0, 0, 0], 'reference': None}},
     {'action': 'set_property', 'target': 'Fixture', 'componentType': 'BoxCollider',
      'properties': {'isTrigger': False, 'size': [0, 0, 0], 'reference': None}}),
    ({'action': 'set_property', 'target': 'Root/Fixture', 'component_type': 'BoxCollider',
      'search_method': 'by_path', 'property': 'isTrigger', 'value': False,
      'properties': {'isTrigger': True}},
     {'action': 'set_property', 'target': 'Root/Fixture', 'componentType': 'BoxCollider',
      'searchMethod': 'by_path', 'property': 'isTrigger', 'value': False,
      'properties': {'isTrigger': True}}),
]
for value in (0, False, '', [0, -1, 2], {'path': 'Assets/Fixture.mat'}, '[0,0,0]'):
    component_cases.append((
        {'action': 'set_property', 'target': 'Fixture', 'component_type': 'Owned',
         'property': 'value', 'value': value},
        {'action': 'set_property', 'target': 'Fixture', 'componentType': 'Owned',
         'property': 'value', 'value': value},
    ))
for method in (None, 'by_name', 'by_id'):
    payload = {'action': 'set_property', 'target': '321', 'component_type': 'BoxCollider',
               'property': 'isTrigger', 'value': False}
    wire = {'action': 'set_property', 'target': '321', 'componentType': 'BoxCollider',
            'property': 'isTrigger', 'value': False}
    if method is not None:
        payload['search_method'] = method
        wire['searchMethod'] = method
    component_cases.append((payload, wire))
for action in ('remove', 'set_property'):
    for index in (0, 1, -1, None):
        payload = {'action': action, 'target': 'Fixture', 'component_type': 'BoxCollider',
                   'component_index': index}
        wire = {'action': action, 'target': 'Fixture', 'componentType': 'BoxCollider'}
        if action == 'set_property':
            payload.update(property='isTrigger', value=False)
            wire.update(property='isTrigger', value=False)
        if index is not None:
            wire['componentIndex'] = int(index)
        component_cases.append((payload, wire))
asset_cases = [
    ({'action': 'create', 'path': 'Assets/Fixture.mat', 'asset_type': 'Material',
      'properties': '{"_Float":0,"_Color":[0,0,0,0]}'},
     {'action': 'create', 'path': 'Assets/Fixture.mat', 'assetType': 'Material',
      'properties': {'_Float': 0, '_Color': [0, 0, 0, 0]}}),
    ({'action': 'modify', 'path': 'Assets/Fixture.prefab',
      'properties': {'BoxCollider': {'isTrigger': False, 'reference': None}}},
     {'action': 'modify', 'path': 'Assets/Fixture.prefab',
      'properties': {'BoxCollider': {'isTrigger': False, 'reference': None}}}),
    ({'action': 'search', 'path': 't:Material', 'asset_type': 'Material',
      'page_size': '1', 'page_number': 1},
     {'action': 'search', 'path': 'Assets', 'assetType': 'Material', 'searchPattern': 't:Material',
      'filterType': 'Material', 'pageSize': 1, 'pageNumber': 1}),
    ({'action': 'search', 'path': 't:Material', 'search_pattern': '*.prefab',
      'asset_type': 'Material', 'filter_type': 'Prefab', 'filter_date_after': ''},
     {'action': 'search', 'path': 't:Material', 'assetType': 'Material',
      'searchPattern': '*.prefab', 'filterType': 'Prefab', 'filterDateAfter': '',
      'pageSize': 50, 'pageNumber': 1}),
    ({'action': 'search', 'path': '', 'search_pattern': '', 'page_size': 1,
      'page_number': '1', 'destination': ''},
     {'action': 'search', 'path': '', 'searchPattern': '', 'pageSize': 1,
      'pageNumber': 1, 'destination': ''}),
    ({'action': 'get_info', 'path': 'Assets/Fixture.mat', 'generate_preview': True},
     {'action': 'get_info', 'path': 'Assets/Fixture.mat', 'generatePreview': True}),
    ({'action': 'create_folder', 'path': 'Assets/Owned/Nested'},
     {'action': 'create_folder', 'path': 'Assets/Owned/Nested'}),
]
for action in ('import', 'delete', 'get_info', 'get_components'):
    asset_cases.append(({'action': action, 'path': 'Assets/Fixture.mat'},
                        {'action': action, 'path': 'Assets/Fixture.mat'}))
for action in ('duplicate', 'move', 'rename'):
    asset_cases.append(({'action': action, 'path': 'Assets/Fixture.mat',
                         'destination': 'Assets/Owned/Destination.mat'},
                        {'action': action, 'path': 'Assets/Fixture.mat',
                         'destination': 'Assets/Owned/Destination.mat'}))

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            for name, cases in (('manage_components', component_cases), ('manage_asset', asset_cases)):
                check(name in tools, 'registered discovery ' + mode + name)
                print('FULL_SCHEMA', mode, name, json.dumps(tools[name].inputSchema, sort_keys=True))
                for payload, wire in cases:
                    for failed in (False, True):
                        for wrapped in (False, True):
                            expected = {
                                'success': not failed,
                                'message': 'Controlled native diagnostic' if failed else 'Done',
                                'data': {'componentAdded': False, 'componentIndex': 0, 'assets': [],
                                         'reference': None, 'errors': []},
                            }
                            raw = {'status': 'success', 'result': expected} if wrapped else expected
                            before = len(requests)
                            result = await client.call_tool(name, payload)
                            actual = requests[before:]
                            domain = [row for row in actual if row[1] == name]
                            expected_wire = wire if name == 'manage_components' else {
                                **wire, 'generatePreview': wire.get('generatePreview', False),
                            }
                            label = mode + name + repr(payload) + str(failed) + str(wrapped)
                            check(result.structured_content == expected, 'response fidelity ' + label)
                            check(domain == [('Project@fixture', name, expected_wire)], 'wire fidelity ' + label)
                            check(any(row[1] == 'get_editor_state' for row in actual), 'actual preflight ' + label)
                            if not failed and not wrapped:
                                print('DOMAIN_WIRE', json.dumps({
                                    'surface': 'sdk', 'mode': mode, 'tool': name,
                                    'input': payload, 'wire': domain[-1][2] if domain else None,
                                }))
            for action in ('remove', 'set_property'):
                for value in (True, False, '0', '1', '1e0', 1.0, 0.5):
                    payload = {'action': action, 'target': 'Fixture', 'component_type': 'BoxCollider',
                               'component_index': value}
                    if action == 'set_property':
                        payload.update(property='isTrigger', value=False)
                    before = len(requests)
                    result = await client.call_tool('manage_components', payload, raise_on_error=False)
                    check(result.is_error and len(requests) == before,
                          'strict index no dispatch ' + mode + action + repr(value))
                    print('SELECTOR_WIRE', json.dumps({
                        'mode': mode, 'input': payload, 'rejected': result.is_error,
                        'requests': requests[before:],
                    }))
            for name, payload in (
                ('manage_components', {'action': 'set_property', 'target': 'Fixture',
                                       'component_type': 'Renderer', 'property': 'sharedMaterial'}),
                ('manage_components', {'action': 'add', 'target': '', 'component_type': 'BoxCollider'}),
                ('manage_components', {'action': 'add', 'target': 0, 'component_type': 'BoxCollider'}),
                ('manage_components', {'action': 'add', 'target': 'Fixture', 'component_type': ''}),
                ('manage_components', {'action': 'add', 'target': 'Fixture', 'component_type': 'BoxCollider',
                                       'properties': '[]'}),
                ('manage_components', {'action': 'set_property', 'target': 'Fixture',
                                       'component_type': 'Owned', 'property': 'value', 'value': 'undefined'}),
                ('manage_asset', {'action': 'modify', 'path': 'Assets/Fixture.mat', 'properties': '[]'}),
                ('manage_asset', {'action': 'search', 'path': 'Assets', 'page_size': 0}),
                ('manage_asset', {'action': 'search', 'path': 'Assets', 'page_number': 0}),
                ('manage_asset', {'action': 'search', 'path': 'Assets', 'page_number': 'bad'}),
                *[('manage_asset', {'action': 'search', 'path': 'Assets', key: value})
                  for key in ('page_size', 'page_number') for value in (1.0, 1.5, '1.0', '1e0')],
            ):
                before = len(requests)
                result = await client.call_tool(name, payload, raise_on_error=False)
                check((result.is_error or result.structured_content.get('success') is False) and len(requests) == before,
                      'local rejection before preflight ' + mode + repr(payload))
            for name, payload in (
                ('manage_components', {'action': 'bad', 'target': 'Fixture', 'component_type': 'BoxCollider'}),
                ('manage_components', {'action': 'remove', 'target': 'Fixture', 'component_type': 'BoxCollider',
                                       'component_index': 'bad'}),
                ('manage_components', {'action': 'remove', 'target': 'Fixture', 'component_type': 'BoxCollider',
                                       'component_index': []}),
                ('manage_components', {'action': 'remove', 'target': 'Fixture', 'component_type': 'BoxCollider',
                                       'component_index': 1.9}),
                ('manage_asset', {'action': 'bad', 'path': 'Assets'}),
                ('manage_asset', {'action': 'get_info', 'path': None}),
                *[('manage_asset', {'action': 'search', 'path': 'Assets', key: value})
                  for key in ('page_size', 'page_number') for value in (True, False)],
                *[('manage_asset', {'action': 'get_info', 'path': 'Assets/Fixture.mat', 'generate_preview': value})
                  for value in (0, 1)],
            ):
                before = len(requests)
                result = await client.call_tool(name, payload, raise_on_error=False)
                check(result.is_error and len(requests) == before, 'SDK rejection ' + mode + repr(payload))
    raw = {'success': True, 'message': 'Done', 'data': {}}
    for action in ('remove', 'set_property'):
        for value in (True, False, 0, 1, None, 'bad'):
            payload = {'action': action, 'target': 'Fixture', 'component_type': 'BoxCollider',
                       'component_index': value}
            if action == 'set_property':
                payload.update(property='isTrigger', value=False)
            before = len(requests)
            await manage_components(DirectContext(), **payload)
            print('SELECTOR_WIRE', json.dumps({
                'mode': 'direct_internal', 'input': payload, 'requests': requests[before:],
            }))
    print(f'fresh component/asset SDK checks={checks} failures={len(failures)} requests={len(requests)}')
    assert not failures, failures

anyio.run(main)
'''


def test_component_asset_cli_requests_json_and_failures(tmp_path):
    assert "fresh component/asset CLI checks=" in _run(CLI_PROGRAM, tmp_path)


def test_component_asset_registered_sdk_falsey_values_and_selectors(tmp_path):
    assert "fresh component/asset SDK checks=" in _run(SDK_PROGRAM, tmp_path)
