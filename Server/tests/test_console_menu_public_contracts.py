"""Console/menu contracts through isolated registered SDK/resources and central CLI."""

import os
import subprocess
import sys


def _run(program, tmp_path):
    env = {
        **os.environ,
        'APPDATA': str(tmp_path),
        'XDG_DATA_HOME': str(tmp_path),
        'UNITY_MCP_DISABLE_TELEMETRY': 'true',
    }
    env.pop('PYTEST_CURRENT_TEST', None)
    result = subprocess.run(
        [sys.executable, '-B', '-c', program], env=env,
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
from cli.utils import connection

requests, failures = [], []
checks = 0
reply, wrapped = {}, False
client_type = httpx.AsyncClient

def respond(request):
    requests.append(json.loads(request.content))
    value = copy.deepcopy(reply)
    return httpx.Response(200, json={'status': 'success', 'result': value} if wrapped else value)

connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()

def check(condition, name):
    global checks
    checks += 1
    if not condition:
        failures.append(name)
        print('FAIL', name)

defaults = {'action': 'get', 'types': ['error', 'warning', 'log'], 'count': 10,
            'include_stacktrace': False}
cases = [
    (['console'], 'read_console', defaults),
    (['console', '--count', '0'], 'read_console', {**defaults, 'count': 0}),
    (['console', '--count', '-1'], 'read_console', {**defaults, 'count': -1}),
    (['console', '--type', 'all', '--count', '2'], 'read_console',
     {**defaults, 'types': ['all'], 'count': 2}),
    (['console', '--type', 'error', '--type', 'warning', '--filter', 'Owned', '--stacktrace'],
     'read_console', {**defaults, 'types': ['error', 'warning'], 'filter_text': 'Owned',
                      'include_stacktrace': True, 'format': 'detailed'}),
    (['console', '--filter', ''], 'read_console', defaults),
    (['console', '--clear'], 'read_console', {'action': 'clear'}),
    (['menu', 'Fixture/Action'], 'execute_menu_item', {'menu_path': 'Fixture/Action'}),
    (['menu', ''], 'execute_menu_item', {'menu_path': ''}),
    (['menu', 'File/Quit'], 'execute_menu_item', {'menu_path': 'File/Quit'}),
]
for args, command, expected in cases:
    for failed, wrapped in ((False, False), (False, True), (True, False), (True, True)):
        reply = {'success': not failed, 'message': 'Controlled outcome',
                 'data': {'count': 0, 'enabled': False, 'nextCursor': None, 'items': []}}
        if failed:
            reply['error'] = 'Controlled native diagnostic'
        before = len(requests)
        result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', 'editor', *args])
        sent = requests[before:]
        check(sent == [{'type': command, 'params': expected, 'unity_instance': 'Project@fixture'}],
              'exact CLI request ' + repr(args))
        check(result.exit_code == (1 if failed else 0), 'native failure exit ' + repr(args))
        check(json.loads(result.output) == reply, 'one JSON document/native diagnostic ' + repr(args))
        print('CLI_WIRE', json.dumps({'args': args, 'requests': sent, 'response': reply}))
for args in (['console', '--clear'], ['menu', 'Fixture/Action']):
    reply = {'success': True, 'message': 'Controlled text outcome'}
    result = runner.invoke(cli, ['--format', 'text', 'editor', *args])
    check(result.exit_code == 0 and '✅' in result.output, 'existing text notice')
for args in (['menu'], ['console', '--count', 'bad'], ['console', '--type', 'unknown']):
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', 'editor', *args])
    check(result.exit_code != 0 and len(requests) == before, 'CLI parse error/no HTTP')
for args in (['editor'], ['editor', 'console'], ['editor', 'menu']):
    result = runner.invoke(cli, [*args, '--help'])
    check(result.exit_code == 0, 'help compatibility ' + repr(args))
    print('FULL_HELP', json.dumps({'args': args, 'text': result.output}))
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
from services.resources import register_all_resources
from core.config import config
from transport.plugin_hub import PluginHub

config.transport_mode = 'http'
config.http_remote_hosted = False
requests, failures = [], []
checks = 0
raw = {}

def check(condition, name):
    global checks
    checks += 1
    if not condition:
        failures.append(name)
        print('FAIL', name)

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

server = FastMCP('console-menu-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
register_all_resources(server)
defaults = {'action': 'get', 'types': ['error', 'warning', 'log'], 'count': 10,
            'format': 'plain', 'includeStacktrace': False}
console_cases = [
    ({}, defaults),
    ({'action': None, 'count': None, 'format': None, 'types': None}, defaults),
    *[({'count': value}, {**defaults, 'count': expected})
      for value, expected in ((0, 0), ('5', 5), ('all', None), ('*', None), (' ALL ', None),
                              ('bad', 10), ('2.9', 2), (1.0, 1), (False, 0), (True, 1))],
    ({'action': 'clear'}, {**defaults, 'action': 'clear', 'count': None}),
    ({'count': 'all', 'page_size': 2, 'cursor': 1, 'format': 'detailed'},
     {**defaults, 'count': None, 'pageSize': 2, 'cursor': 1, 'format': 'detailed'}),
    ({'page_size': 0, 'cursor': 2147483647},
     {**defaults, 'pageSize': 0, 'cursor': 2147483647}),
    ({'page_size': '2', 'cursor': '-1', 'filter_text': ''},
     {**defaults, 'pageSize': 2, 'cursor': -1, 'filterText': ''}),
    ({'page_size': None, 'cursor': None, 'include_stacktrace': None}, defaults),
    ({'types': [], 'include_stacktrace': 'false'}, {**defaults, 'types': []}),
    ({'types': '[" ERROR ","warning"]', 'include_stacktrace': 'true', 'filter_text': 'Owned', 'format': 'json'},
     {**defaults, 'types': ['error', 'warning'], 'includeStacktrace': True,
      'filterText': 'Owned', 'format': 'json'}),
    ({'types': ['all'], 'include_stacktrace': False}, {**defaults, 'types': ['all']}),
]
menu_cases = [({}, {}), ({'menu_path': None}, {}),
              *[({'menu_path': value}, {'menuPath': value})
                for value in ('', ' ', 'Fixture/Action', 'File/Quit')]]

def model_response(response):
    return {'success': response['success'], 'message': response.get('message'),
            'error': response.get('error'), 'data': response.get('data'), 'hint': response.get('hint')}

async def invoke(client, tool, payload, mode):
    before = len(requests)
    result = await client.call_tool(tool, payload, raise_on_error=False)
    sent = requests[before:]
    print('SDK_WIRE', json.dumps({'mode': mode, 'tool': tool, 'payload': payload,
                                 'wires': sent, 'response': result.structured_content,
                                 'is_error': result.is_error}))
    return result, sent

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            discovered = {tool.name: tool for tool in await client.list_tools()}
            check('read_console' in discovered and 'execute_menu_item' in discovered, 'full tool registration')
            for tool in ('read_console', 'execute_menu_item'):
                print('FULL_SCHEMA', mode, tool, json.dumps(discovered[tool].inputSchema, sort_keys=True))
                print('FULL_ANNOTATIONS', mode, tool, discovered[tool].annotations.model_dump_json())
            resources = {str(resource.uri): resource for resource in await client.list_resources()}
            check('mcpforunity://menu-items' in resources, 'actual resource discovery')
            print('FULL_RESOURCE', mode, resources['mcpforunity://menu-items'].model_dump_json())
            for tool, cases in (('read_console', console_cases), ('execute_menu_item', menu_cases)):
                for payload, expected in cases:
                    for failed, wrapped in ((False, False), (True, True)):
                        response = {'success': not failed, 'message': 'Controlled outcome',
                                    'data': {'count': 0, 'enabled': False, 'nextCursor': None, 'items': []}}
                        if failed:
                            response['error'] = 'Controlled native diagnostic'
                        raw = {'status': 'success', 'result': response} if wrapped else response
                        result, sent = await invoke(client, tool, payload, mode)
                        check(sent == [('Project@fixture', tool, expected)], 'exact SDK/coercion ' + repr(payload))
                        wanted = response if tool == 'read_console' else model_response(response)
                        check(result.structured_content == wanted, 'existing response/error model')
            for include in (False, True):
                for shape in ('list', 'lines', 'items'):
                    entries = [{'message': 'Owned', 'stacktrace': 'Legacy owned trace',
                                'stackTrace': 'Owned native trace' if include else None,
                                'line': 0, 'enabled': False}, 'plain owned message']
                    data = entries if shape == 'list' else {shape: entries, 'nextCursor': None, 'total': 0}
                    raw = {'success': True, 'data': data}
                    wanted = copy.deepcopy(raw)
                    if not include:
                        selected = wanted['data'] if shape == 'list' else wanted['data'][shape]
                        selected[0].pop('stacktrace')
                    result, sent = await invoke(client, 'read_console', {'include_stacktrace': include}, mode)
                    check(result.structured_content == wanted, 'stack trace/native null and legacy compatibility')
            for value in ([], 0, False, None):
                raw = {'success': True, 'data': value}
                result, sent = await invoke(client, 'execute_menu_item', {'menu_path': 'Fixture/Action'}, mode)
                check(json.dumps(result.structured_content['data']) == json.dumps(value),
                      'menu falsey result/type fidelity')
            for tool, payload in (
                ('read_console', {'action': 'GET'}), ('read_console', {'format': 'unknown'}),
                ('read_console', {'types': ['ERROR']}), ('read_console', {'types': '{}'}),
                ('read_console', {'types': '[1]'}), ('read_console', {'types': '["unknown"]'}),
                ('execute_menu_item', {'menu_path': False}), ('execute_menu_item', {'menu_path': {}}),
            ):
                result, sent = await invoke(client, tool, payload, mode)
                check(not sent and (result.is_error or result.structured_content.get('success') is False),
                      'existing local/schema error before transport')
            for response in ({'success': True, 'message': 'Owned menus', 'data': ['Fixture/Action', 'Fixture/Second']},
                             {'success': True, 'data': []},
                             {'success': False, 'error': 'Owned discovery failure', 'data': None}):
                for wrapped in (False, True):
                    raw = {'status': 'success', 'result': response} if wrapped else response
                    before = len(requests)
                    content = await client.read_resource('mcpforunity://menu-items')
                    decoded = json.loads(content[0].text)
                    sent = requests[before:]
                    check(sent == [('Project@fixture', 'get_menu_items', {'refresh': True, 'search': ''})],
                          'resource exact fixed request')
                    check(decoded == model_response(response), 'registered typed resource/error serialization')
                    print('SDK_WIRE', json.dumps({'mode': mode, 'resource': 'mcpforunity://menu-items',
                                                 'wires': sent, 'response': decoded}))
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
'''


def test_console_menu_top_level_cli_when_transport_returns_native_documents(tmp_path):
    assert 'CLI_SUMMARY' in _run(CLI_PROGRAM, tmp_path)


def test_console_menu_registered_sdk_and_resource_when_inputs_use_supported_shapes(tmp_path):
    assert 'SDK_SUMMARY' in _run(SDK_PROGRAM, tmp_path)


def test_console_stacktrace_flag_displays_native_trace_when_console_defaults_to_plain(tmp_path):
    program = r'''
import json
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection

# Given the native console's plain-message and detailed-entry response formats.
requests = []
client_type = httpx.AsyncClient
fixture_mode = 'entries'

def respond(request):
    payload = json.loads(request.content)
    requests.append(payload)
    params = payload['params']
    if params.get('action') == 'clear':
        return httpx.Response(200, json={'success': True, 'message': 'Console cleared successfully.'})
    if fixture_mode == 'empty':
        return httpx.Response(200, json={'success': True, 'message': 'Retrieved 0 log entries.', 'data': []})
    if fixture_mode == 'error':
        return httpx.Response(200, json={'success': False, 'error': 'Owned console read diagnostic'})
    entry = {'type': 'Error', 'message': 'Owned console body', 'file': 'Assets/Owned.cs',
             'line': 12, 'stackTrace': 'Owned.Stack.Frame (at Assets/Owned.cs:12)\n' + 'x' * 200 + '\nOwned.Trace.Tail'
             if params.get('include_stacktrace') else None}
    data = [entry['message']] if params.get('format', 'plain') == 'plain' else [entry]
    return httpx.Response(200, json={'success': True, 'data': data})

connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()

for output_format in ('json', 'text', 'table'):
    fixture_mode = 'entries'
    # When requesting stack traces through the top-level CLI and central HTTP adapter.
    result = runner.invoke(cli, ['--format', output_format, 'editor', 'console', '--stacktrace'])
    # Then the requested trace survives the native format and CLI rendering.
    assert result.exit_code == 0, result.output
    assert 'Owned console body' in result.output
    assert 'Owned.Stack.Frame' in result.output, result.output
    assert 'Owned.Trace.Tail' in result.output, result.output
    assert requests[-1]['params']['format'] == 'detailed'

    result = runner.invoke(cli, ['--format', output_format, 'editor', 'console'])
    assert result.exit_code == 0, result.output
    assert 'Owned console body' in result.output and 'Owned.Stack.Frame' not in result.output
    assert 'format' not in requests[-1]['params']

    fixture_mode = 'empty'
    result = runner.invoke(cli, ['--format', output_format, 'editor', 'console', '--stacktrace'])
    assert result.exit_code == 0 and result.output.strip(), result.output
    if output_format == 'json':
        assert json.loads(result.output)['data'] == []

    fixture_mode = 'error'
    result = runner.invoke(cli, ['--format', output_format, 'editor', 'console', '--stacktrace'])
    assert result.exit_code == 1 and 'Owned console read diagnostic' in result.output, result.output

    result = runner.invoke(cli, ['--format', output_format, 'editor', 'console', '--stacktrace', '--clear'])
    assert result.exit_code == 0 and 'cleared' in result.output.lower(), result.output
    assert requests[-1]['params'] == {'action': 'clear'}
print('console stacktrace rendering controls passed')
'''
    assert 'console stacktrace rendering controls passed' in _run(program, tmp_path)
