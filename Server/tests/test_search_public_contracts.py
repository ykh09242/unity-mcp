"""Public search contracts with only outbound Unity/HTTP controlled."""

import os
from pathlib import Path
import subprocess
import sys


def _run(program, tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program], env=env, capture_output=True, text=True, timeout=90
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


CLI_PROGRAM = r"""
import copy
import json
import shlex
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
defaults = {'searchMethod': 'by_name', 'searchTerm': 'Fixture',
            'includeInactive': False, 'pageSize': 50, 'cursor': 0}
cases = [(['Fixture'], defaults),
         (['--method', 'by_id', '--', '-81840'], {**defaults, 'searchMethod': 'by_id', 'searchTerm': '-81840'}),
         (['321'], {**defaults, 'searchTerm': '321'}),
         (['Fixture', '--limit', '0', '--cursor', '0'], {**defaults, 'pageSize': 0}),
         (['Fixture', '--limit', '2147483647', '--cursor', '2147483647'],
          {**defaults, 'pageSize': 2147483647, 'cursor': 2147483647}),
         *[(['Parent/Child', '--method', 'by_path', *(['--include-inactive'] if inactive else [])],
            {**defaults, 'searchMethod': 'by_path', 'searchTerm': 'Parent/Child', 'includeInactive': inactive})
           for inactive in (False, True)],
         *[(['Fixture', '--method', method], {**defaults, 'searchMethod': method})
           for method in ('by_tag', 'by_layer', 'by_component')]]
for args, expected in cases:
    for success in (False, True):
        reply = {'success': success, 'message': 'Controlled search',
                 'data': {'instanceIDs': [], 'total': 0, 'nextCursor': None, 'enabled': False}}
        if not success:
            reply['error'] = 'Controlled native rejection'
        before = len(requests)
        result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', 'gameobject', 'find', *args])
        sent = requests[before:]
        check(sent == [{'type': 'find_gameobjects', 'params': expected, 'unity_instance': 'Project@fixture'}], 'CLI exact wire')
        check(result.exit_code == (0 if success else 1), 'CLI result exit')
        check(json.loads(result.output) == reply, 'single JSON document with native falsey fields')
        print('CLI_WIRE', json.dumps({'args': args, 'requests': sent, 'response': reply}))
for args in (['-81840', '--method', 'by_id'], ['Fixture', '--method', 'unknown'], ['Fixture', '--cursor', 'bad']):
    before = len(requests)
    result = runner.invoke(cli, ['gameobject', 'find', *args])
    check(result.exit_code != 0 and len(requests) == before, 'Click rejection before HTTP')
help_result = runner.invoke(cli, ['gameobject', 'find', '--help'])
check(help_result.exit_code == 0, 'find help available')
print('FULL_HELP', json.dumps(help_result.output))
# Execute exactly the advertised negative-ID example, including Click separator.
example = next(line.strip() for line in help_result.output.splitlines() if '-81840' in line)
reply = {'success': True, 'data': {'instanceIDs': [-81840]}}
before = len(requests)
result = runner.invoke(cli, shlex.split(example)[1:])
print('DOCUMENTED_EXAMPLE', json.dumps({'command': example, 'exit': result.exit_code, 'requests': requests[before:]}))
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
async def send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    if command != 'find_gameobjects':
        return {'success': False, 'message': 'Controlled unavailable editor state'}
    return copy.deepcopy(raw)
PluginHub.send_command_for_instance = staticmethod(send)
class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)
server = FastMCP('public-search-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
defaults = {'searchTerm': 'Fixture', 'searchMethod': 'by_name', 'includeInactive': False, 'pageSize': 50, 'cursor': 0}
cases = [({'search_term': 'Fixture'}, defaults),
         ({'search_term': '321'}, {**defaults, 'searchTerm': '321'}),
         ({'search_term': '-81840', 'search_method': 'by_id'}, {**defaults, 'searchTerm': '-81840', 'searchMethod': 'by_id'}),
         ({'search_term': 'Fixture', 'page_size': 0, 'cursor': 0}, {**defaults, 'pageSize': 0}),
         ({'search_term': 'Fixture', 'page_size': '2', 'cursor': '1', 'include_inactive': 'false'},
          {**defaults, 'pageSize': 2, 'cursor': 1}),
         ({'search_term': 'Fixture', 'page_size': None, 'cursor': None, 'include_inactive': None}, defaults),
         ({'search_term': 'Fixture', 'page_size': 1, 'cursor': 0, 'include_inactive': 'true'},
          {**defaults, 'pageSize': 1, 'includeInactive': True}),
         ({'search_term': 'Fixture', 'page_size': 0, 'cursor': 1},
          {**defaults, 'pageSize': 0, 'cursor': 1}),
         ({'search_term': 'Fixture', 'page_size': 2147483647, 'cursor': 2147483647},
          {**defaults, 'pageSize': 2147483647, 'cursor': 2147483647}),
         *[({'search_term': 'Parent/Child', 'search_method': 'by_path', 'include_inactive': inactive},
            {**defaults, 'searchTerm': 'Parent/Child', 'searchMethod': 'by_path', 'includeInactive': inactive})
           for inactive in (False, True)],
         *[({'search_term': 'Fixture', 'search_method': method}, {**defaults, 'searchMethod': method})
           for method in ('by_tag', 'by_layer', 'by_component')]]
async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            tool = next(tool for tool in await client.list_tools() if tool.name == 'find_gameobjects')
            print('FULL_SCHEMA', mode, json.dumps(tool.input_schema, sort_keys=True))
            check(tool.annotations.read_only_hint is False, 'refresh-aware annotation')
            for payload, expected in cases:
                for success in (False, True):
                    raw = {'success': success, 'message': 'Controlled search',
                           'data': {'instanceIDs': [], 'total': 0, 'nextCursor': None, 'enabled': False}}
                    if not success:
                        raw['error'] = 'Controlled native rejection'
                    before = len(requests)
                    result = await client.call_tool('find_gameobjects', payload, raise_on_error=False)
                    sent = [wire for wire in requests[before:] if wire[1] == 'find_gameobjects']
                    check(sent == [('Project@fixture', 'find_gameobjects', expected)], 'registered exact SDK wire')
                    check(result.structured_content == raw, 'SDK native document/type fidelity')
                    print('SDK_WIRE', json.dumps({'mode': mode, 'payload': payload, 'wires': sent, 'response': result.structured_content}))
            for value in (0, False, None, [], {}):
                raw = {'success': True, 'data': value}
                result = await client.call_tool('find_gameobjects', {'search_term': 'Fixture'})
                check(json.dumps(result.structured_content['data']) == json.dumps(value), 'falsey data retains JSON type')
            for payload in ({}, {'search_term': None}, {'search_term': ''},
                            {'search_term': 'Fixture', 'search_method': 'unknown'}):
                before = len(requests)
                result = await client.call_tool('find_gameobjects', payload, raise_on_error=False)
                check(not requests[before:] and (result.is_error or result.structured_content['success'] is False), 'invalid required/schema values before preflight')
            for key, values in (('page_size', (True, False, 1.0, 0.5, '1.0', '1e0')),
                                ('cursor', (True, False, 1.0, 0.5, '1.0', '1e0')),
                                ('include_inactive', (0, 1, '0', '1'))):
                for value in values:
                    before = len(requests)
                    result = await client.call_tool('find_gameobjects', {'search_term': 'Fixture', key: value}, raise_on_error=False)
                    check(not requests[before:] and (result.is_error or result.structured_content.get('success') is False),
                          'invalid scalar before preflight ' + mode + key + repr(value))
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures
anyio.run(main)
"""


def test_search_top_level_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_search_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)


FILE_PROGRAM = r"""
import base64
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
async def send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    return copy.deepcopy(raw)
PluginHub.send_command_for_instance = staticmethod(send)
class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)
server = FastMCP('public-file-search-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
text = 'a😀\nNeedle\nneedle'
expected_matches = [{'line': 2, 'content': 'Needle', 'match': 'Needle', 'start': 3, 'end': 9},
                    {'line': 3, 'content': 'needle', 'match': 'needle', 'start': 10, 'end': 16}]
async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            for uri in ('file:///C:/Fixture/Assets/A%2520B/Foo.cs', 'mcpforunity://path/Assets/A%2520B/Foo.cs', 'Assets/A%2520B/Foo.cs'):
                raw = {'success': True, 'data': {'contents': text}}
                before = len(requests)
                result = await client.call_tool('find_in_file', {'uri': uri, 'pattern': 'needle'}, raise_on_error=False)
                sent = requests[before:]
                check(sent == [('Project@fixture', 'manage_script', {'action': 'read', 'name': 'Foo', 'path': 'Assets/A%20B'})], 'one percent decode across URI forms')
                check(result.structured_content == {'success': True, 'data': {'matches': expected_matches, 'count': 2, 'total_matches': 2}}, 'real regex/Unicode offsets/excerpts')
                print('FILE_WIRE', json.dumps({'mode': mode, 'payload': {'uri': uri, 'pattern': 'needle'}, 'wires': sent, 'response': result.structured_content}))
            for data in ({'contentsEncoded': True, 'encodedContents': base64.b64encode(text.encode()).decode()}, {'contents': ''}):
                raw = {'success': True, 'data': data}
                result = await client.call_tool('find_in_file', {'uri': 'Assets/Fixture.cs', 'pattern': 'needle', 'max_results': 1, 'ignore_case': False})
                wanted = expected_matches[1:] if data.get('contentsEncoded') else []
                check(result.structured_content == {'success': True, 'data': {'matches': wanted, 'count': len(wanted), 'total_matches': len(wanted)}}, 'encoded text/empty/case/limit')
            raw = {'success': False, 'message': 'Controlled read failure', 'data': None}
            result = await client.call_tool('find_in_file', {'uri': 'Assets/Fixture.cs', 'pattern': 'needle'})
            check(result.structured_content == raw, 'native read rejection preserved')
    print('FILE_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures
anyio.run(main)
"""


def test_file_search_registered_sdk_contracts(tmp_path):
    assert "FILE_SUMMARY" in _run(FILE_PROGRAM, tmp_path)
