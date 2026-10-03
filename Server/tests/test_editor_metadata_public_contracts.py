"""Registered metadata reads and tag/layer commands with outbound transport controlled."""
import os
from pathlib import Path
import subprocess
import sys


def _run(program, tmp_path):
    env = {**os.environ, 'APPDATA': str(tmp_path), 'XDG_DATA_HOME': str(tmp_path),
           'UNITY_MCP_DISABLE_TELEMETRY': 'true',
           'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    env.pop('PYTEST_CURRENT_TEST', None)
    result = subprocess.run([sys.executable, '-B', '-c', program], env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


SDK_PROGRAM = r'''
import copy
import json
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from services.tools import register_all_tools
from services.resources import register_all_resources
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
    async def on_read_resource(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)
server = FastMCP('public-editor-metadata-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
register_all_resources(server)
resources = [
    ('editor/selection', 'get_selection', {
        'activeObject': None, 'activeGameObject': None, 'activeTransform': None,
        'activeInstanceID': 0, 'count': 0, 'objects': [], 'gameObjects': [], 'assetGUIDs': []}),
    ('editor/windows', 'get_windows', [{'title': 'Fixture', 'typeName': 'Fixture.Window',
        'isFocused': False, 'position': {'x': 0.0, 'y': -1.0, 'width': 0.0, 'height': 2.5}, 'instanceID': -81840}]),
    ('editor/active-tool', 'get_active_tool', {'activeTool': 'Move', 'isCustom': False,
        'pivotMode': 'Pivot', 'pivotRotation': 'Local',
        'handleRotation': {'x': 0.0, 'y': 0.0, 'z': 0.0}, 'handlePosition': {'x': -1.0, 'y': 0.0, 'z': 2.5}}),
    ('project/tags', 'get_tags', ['Untagged', 'FixtureTag']),
    ('project/layers', 'get_layers', {'0': 'Default', '8': 'FixtureLayer', '31': 'Last'}),
    ('project/info', 'get_project_info', {'projectRoot': 'C:/Fixture', 'projectName': 'Fixture',
        'unityVersion': '6000.0.69f1', 'platform': 'WindowsEditor', 'assetsPath': 'C:/Fixture/Assets',
        'renderPipeline': 'Universal', 'activeInputHandler': 'Both',
        'packages': {'ugui': False, 'textmeshpro': False, 'inputsystem': True, 'uiToolkit': True, 'screenCapture': True}}),
]
async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            tool = next(item for item in await client.list_tools() if item.name == 'manage_editor')
            print('FULL_SCHEMA', mode, json.dumps(tool.input_schema, sort_keys=True))
            check(tool.annotations.read_only_hint is False, 'mutating editor annotation')
            print('FULL_RESOURCES', mode, json.dumps([item.model_dump(mode='json', by_alias=False) for item in await client.list_resources()], sort_keys=True))
            for action, key, value in (('add_tag', 'tag_name', 'FixtureTag'), ('remove_tag', 'tag_name', 'FixtureTag'),
                                       ('add_layer', 'layer_name', 'FixtureLayer'), ('remove_layer', 'layer_name', 'FixtureLayer'),
                                       ('add_tag', 'tag_name', ''), ('add_layer', 'layer_name', None)):
                payload = {'action': action, key: value}
                expected = {'action': action}
                if value is not None:
                    expected['tagName' if key == 'tag_name' else 'layerName'] = value
                for success in (True, False):
                    raw = {'success': success, 'message': 'Controlled editor reply', 'data': {'index': 0, 'changed': False}}
                    if not success:
                        raw['error'] = 'Controlled native rejection'
                    before = len(requests)
                    result = await client.call_tool('manage_editor', payload, raise_on_error=False)
                    expected_reply = raw if not success else {key: raw[key] for key in ('success', 'message', 'data')}
                    check(requests[before:] == [('Project@fixture', 'manage_editor', expected)], 'exact editor SDK request')
                    check(result.structured_content == expected_reply, 'native editor success/error and falsey fidelity')
                    print('SDK_WIRE', json.dumps({'mode': mode, 'payload': payload, 'wires': requests[before:], 'response': result.structured_content}))
            for suffix, command, data in resources:
                for success in (True, False):
                    raw = {'success': success, 'message': 'Controlled metadata reply', 'data': data if success else None}
                    if not success:
                        raw['error'] = 'Controlled native rejection'
                    before = len(requests)
                    result = json.loads((await client.read_resource('mcpforunity://' + suffix))[0].text)
                    check(requests[before:] == [('Project@fixture', command, {})], 'fixed URI exact native request')
                    check(json.dumps(result, sort_keys=True) == json.dumps({**raw, 'error': raw.get('error'), 'hint': None}, sort_keys=True), 'typed metadata values/native error envelope')
                    print('RESOURCE_WIRE', json.dumps({'mode': mode, 'uri': 'mcpforunity://' + suffix, 'wires': requests[before:], 'response': result}))
            for suffix in ('editor/windows', 'project/tags', 'project/layers'):
                raw = {'success': True, 'data': {} if suffix.endswith('layers') else []}
                result = json.loads((await client.read_resource('mcpforunity://' + suffix))[0].text)
                check(result['data'] == raw['data'], 'native empty collection preserved')
            project = resources[-1][2]
            for pipeline, input_handler, installed in (('BuiltIn', 'Old', False), ('HighDefinition', 'New', True)):
                raw = {'success': True, 'data': {**project, 'renderPipeline': pipeline,
                       'activeInputHandler': input_handler, 'packages': {key: installed for key in project['packages']}}}
                result = json.loads((await client.read_resource('mcpforunity://project/info'))[0].text)
                check(result['data'] == raw['data'], 'pipeline/input variants and all five package boolean flags')
            raw = {'success': True, 'data': {key: value for key, value in project.items()
                   if key not in ('renderPipeline', 'activeInputHandler', 'packages')}}
            result = json.loads((await client.read_resource('mcpforunity://project/info'))[0].text)
            check({key: result['data'][key] for key in raw['data']} == raw['data']
                  and all(result['data'].get(key, default) == default for key, default in
                          (('renderPipeline', ''), ('activeInputHandler', ''), ('packages', {}))),
                  'older project metadata empty defaults')
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures
anyio.run(main)
'''


CLI_PROGRAM = r'''
import copy
import json
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection

requests, failures = [], []
checks = 0
reply = {}
client_type = httpx.AsyncClient
def respond(request):
    requests.append(json.loads(request.content))
    return httpx.Response(200, json=copy.deepcopy(reply))
connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()
def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)
for command, name, key in (('add-tag', 'FixtureTag', 'tagName'), ('remove-tag', 'FixtureTag', 'tagName'),
                           ('add-layer', 'FixtureLayer', 'layerName'), ('remove-layer', 'FixtureLayer', 'layerName')):
    for success in (True, False):
        reply = {'success': success, 'message': 'Controlled editor reply', 'data': {'index': 0, 'changed': False}}
        if not success:
            reply['error'] = 'Controlled native rejection'
        before = len(requests)
        result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', 'editor', command, name])
        check(requests[before:] == [{'type': 'manage_editor', 'params': {'action': command.replace('-', '_'), key: name}, 'unity_instance': 'Project@fixture'}], 'CLI editor exact request')
        check(result.exit_code == (0 if success else 1), 'native failure CLI exit')
        check(json.loads(result.output) == reply, 'CLI single JSON response/no notice suffix')
        print('CLI_WIRE', json.dumps({'command': command, 'requests': requests[before:], 'response': reply}))
for command, data in (('get_selection', {'activeObject': None, 'activeInstanceID': 0, 'count': 0}),
                      ('get_tool_states', {'tools': [], 'groups': []}), ('get_layers', {'0': 'Default', '8': 'FixtureLayer'})):
    reply = {'success': True, 'data': data}
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', 'raw', command, '{}'])
    check(requests[before:] == [{'type': command, 'params': {}, 'unity_instance': 'Project@fixture'}], 'existing raw metadata route')
    check(result.exit_code == 0 and json.loads(result.output) == reply, 'raw metadata falsey fidelity')
    print('CLI_WIRE', json.dumps({'command': command, 'requests': requests[before:], 'response': reply}))
for command in ('add-tag', 'remove-tag', 'add-layer', 'remove-layer'):
    result = runner.invoke(cli, ['editor', command, '--help'])
    check(result.exit_code == 0, 'editor command help')
    print('FULL_HELP', command, json.dumps(result.output))
print('CLI_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
assert not failures, failures
'''


def test_editor_metadata_registered_sdk_contracts(tmp_path):
    assert 'SDK_SUMMARY' in _run(SDK_PROGRAM, tmp_path)


def test_editor_metadata_top_level_cli_contracts(tmp_path):
    assert 'CLI_SUMMARY' in _run(CLI_PROGRAM, tmp_path)
