"""Reflection/execution request and output contracts through fresh public boundaries."""

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
from cli.commands.code import code
from cli.commands.reflect import reflect
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

cases = [
    ('reflect', ['type', 'System.String'], 'unity_reflect', {'action': 'get_type', 'class_name': 'System.String'}),
    ('reflect', ['type', 'Dictionary<string,List<int>>'], 'unity_reflect',
     {'action': 'get_type', 'class_name': 'Dictionary<string,List<int>>'}),
    ('reflect', ['member', 'System.String', 'Chars'], 'unity_reflect',
     {'action': 'get_member', 'class_name': 'System.String', 'member_name': 'Chars'}),
    ('reflect', ['member', 'ReflectionProof.Fixture', 'Item'], 'unity_reflect',
     {'action': 'get_member', 'class_name': 'ReflectionProof.Fixture', 'member_name': 'Item'}),
    ('reflect', ['search', 'String'], 'unity_reflect', {'action': 'search', 'query': 'String', 'scope': 'unity'}),
    ('reflect', ['search', 'Fixture', '--scope', 'all'], 'unity_reflect',
     {'action': 'search', 'query': 'Fixture', 'scope': 'all'}),
    ('code', ['execute', 'return 0;'], 'execute_code',
     {'action': 'execute', 'code': 'return 0;', 'safety_checks': True}),
    ('code', ['execute', 'return false;', '--no-safety-checks'], 'execute_code',
     {'action': 'execute', 'code': 'return false;', 'safety_checks': False}),
    ('code', ['replay', '0'], 'execute_code', {'action': 'replay', 'index': 0}),
    ('code', ['replay', '--', '-1'], 'execute_code', {'action': 'replay', 'index': -1}),
    ('code', ['history'], 'execute_code', {'action': 'get_history', 'limit': 10}),
    ('code', ['history', '--limit', '0'], 'execute_code', {'action': 'get_history', 'limit': 0}),
    ('code', ['history', '--limit', '100'], 'execute_code', {'action': 'get_history', 'limit': 100}),
    ('code', ['clear-history'], 'execute_code', {'action': 'clear_history'}),
]
for group, args, command, expected in cases:
    for failed, wrapped in ((False, False), (False, True), (True, False), (True, True)):
        reply = {'success': not failed, 'message': 'Controlled native diagnostic',
                 'data': {'count': 0, 'enabled': False, 'entries': [], 'result': None}}
        before = len(requests)
        result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', group, *args])
        sent = requests[before:]
        check(result.exit_code == (1 if failed else 0), 'native failure exit ' + repr(args))
        check(sent == [{'type': command, 'params': expected, 'unity_instance': 'Project@fixture'}],
              'literal CLI payload ' + repr(args))
        check(json.loads(result.output) == reply, 'opaque one-document JSON ' + repr(args))
        print('CLI_WIRE', json.dumps({'group': group, 'args': args, 'requests': sent, 'response': reply}))

for args in (['execute', 'return 0;'], ['replay', '0']):
    for value in (0, False, '', None, {'enabled': False, 'count': 0}):
        for wrapped in (False, True):
            reply = {'success': True, 'message': 'Benign scalar result', 'data': {'result': value}}
            result = runner.invoke(cli, ['--format', 'json', 'code', *args])
            try:
                decoded = json.loads(result.output)
            except json.JSONDecodeError:
                decoded = None
            check(result.exit_code == 0 and decoded == reply, 'result single JSON ' + repr((args, value)))
            print('CLI_WIRE', json.dumps({'group': 'code', 'args': args,
                                         'requests': [requests[-1]], 'response': reply, 'output': result.output}))
reply = {'success': True, 'data': {'result': 0}}
result = runner.invoke(cli, ['--format', 'text', 'code', 'execute', 'return 0;'])
check(result.exit_code == 0 and 'Result: 0' in result.output, 'text result notice retained')
for group, args in (('code', ['execute']), ('code', ['execute', '']),
                    ('code', ['replay', 'bad']), ('reflect', ['member', 'System.String']),
                    ('reflect', ['search', 'Fixture', '--scope', 'bad'])):
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', group, *args])
    check(result.exit_code != 0 and len(requests) == before, 'invalid CLI before HTTP')
for group, commands in (('code', (None, 'execute', 'history', 'replay', 'clear-history')),
                        ('reflect', (None, *reflect.commands))):
    for command in commands:
        result = runner.invoke(cli, [group, *([command] if command else []), '--help'])
        check(result.exit_code == 0, 'help ' + repr((group, command)))
        print('FULL_HELP', json.dumps({'group': group, 'command': command, 'text': result.output}))
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

server = FastMCP('reflection-execution-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
server.enable(tags={'group:docs', 'group:scripting_ext'}, components={'tool'})
cases = [
    ('unity_reflect', {'action': 'GET_TYPE', 'class_name': 'System.String'},
     {'action': 'get_type', 'class_name': 'System.String'}),
    ('unity_reflect', {'action': 'get_type', 'class_name': 'Dictionary<string,List<int>>'},
     {'action': 'get_type', 'class_name': 'Dictionary<string,List<int>>'}),
    ('unity_reflect', {'action': 'get_member', 'class_name': 'System.String', 'member_name': 'Chars'},
     {'action': 'get_member', 'class_name': 'System.String', 'member_name': 'Chars'}),
    ('unity_reflect', {'action': 'get_member', 'class_name': 'ReflectionProof.Fixture', 'member_name': 'Item'},
     {'action': 'get_member', 'class_name': 'ReflectionProof.Fixture', 'member_name': 'Item'}),
    ('unity_reflect', {'action': 'search', 'query': 'Fixture'}, {'action': 'search', 'query': 'Fixture'}),
    *[('unity_reflect', {'action': 'search', 'query': 'Fixture', 'scope': scope},
       {'action': 'search', 'query': 'Fixture', **({'scope': scope} if scope is not None else {})})
      for scope in (None, 'unity', 'packages', 'project', 'all')],
    ('unity_reflect', {'action': 'get_type', 'class_name': 'System.String', 'scope': 'ignored', 'query': ''},
     {'action': 'get_type', 'class_name': 'System.String', 'query': ''}),
    ('execute_code', {'action': 'execute', 'code': 'return 0;'},
     {'action': 'execute', 'code': 'return 0;', 'safety_checks': True, 'compiler': 'auto'}),
    *[('execute_code', {'action': 'execute', 'code': 'return false;', 'safety_checks': False, 'compiler': compiler},
       {'action': 'execute', 'code': 'return false;', 'safety_checks': False, 'compiler': compiler})
      for compiler in ('auto', 'roslyn', 'codedom')],
    ('execute_code', {'action': 'execute', 'code': ''},
     {'action': 'execute', 'code': '', 'safety_checks': True, 'compiler': 'auto'}),
    *[('execute_code', {'action': 'get_history', 'limit': limit},
       {'action': 'get_history', 'limit': max(1, min(int(limit), 50))})
      for limit in (0, -1, 100, 1)],
    ('execute_code', {'action': 'get_history'}, {'action': 'get_history', 'limit': 10}),
    *[('execute_code', {'action': 'replay', 'index': index}, {'action': 'replay', 'index': int(index)})
      for index in (0, 1, -1)],
    ('execute_code', {'action': 'clear_history', 'code': 'return 0;', 'safety_checks': False},
     {'action': 'clear_history'}),
]

async def invoke(client, tool, payload, mode):
    before = len(requests)
    result = await client.call_tool(tool, payload, raise_on_error=False)
    sent = requests[before:]
    print('SDK_WIRE', json.dumps({'mode': mode, 'tool': tool, 'payload': payload, 'wires': sent,
                                 'response': result.structured_content, 'is_error': result.is_error}))
    return result, sent

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            discovered = {tool.name: tool for tool in await client.list_tools()}
            check('unity_reflect' in discovered and 'execute_code' in discovered, 'actual tool visibility ' + mode)
            for tool in ('unity_reflect', 'execute_code'):
                print('FULL_SCHEMA', mode, tool, json.dumps(discovered[tool].inputSchema, sort_keys=True))
                print('FULL_ANNOTATIONS', mode, tool, discovered[tool].annotations.model_dump_json())
            for tool, payload, expected in cases:
                for failed, wrapped in ((False, False), (True, True)):
                    response = {'success': not failed, 'message': 'Controlled native diagnostic',
                                'data': {'result': 0, 'enabled': False, 'entries': [], 'optional': None}}
                    raw = {'status': 'success', 'result': response} if wrapped else response
                    result, sent = await invoke(client, tool, payload, mode)
                    check(sent == [('Project@fixture', tool, expected)], 'literal SDK/defaults ' + repr(payload))
                    check(result.structured_content == response, 'existing envelope fidelity')
            for value in (0, False, '', None, {'enabled': False, 'count': 0}):
                raw = {'success': True, 'message': 'Benign result', 'data': {'result': value}}
                result, sent = await invoke(client, 'execute_code', {'action': 'execute', 'code': 'return 0;'}, mode)
                check(result.structured_content == raw, 'result falsey/container retained')
            raw = {'success': False, 'error': 'Native compiler diagnostic', 'code': 'native-code',
                   'data': {'errors': ['owned line 1'], 'compiler': 'roslyn'}}
            result, sent = await invoke(client, 'execute_code', {'action': 'execute', 'code': 'return 0;'}, mode)
            check(result.structured_content == {**raw, 'message': raw['error']},
                  'execution diagnostic fields retained')
            for tool, payload in (
                ('unity_reflect', {'action': 'get_type'}),
                ('unity_reflect', {'action': 'get_member', 'class_name': 'System.String', 'member_name': ''}),
                ('unity_reflect', {'action': 'search', 'query': ''}),
                ('unity_reflect', {'action': 'search', 'query': 'Fixture', 'scope': 'ALL'}),
                ('unity_reflect', {'action': 'unknown'}),
                ('execute_code', {'action': 'execute', 'code': None}),
                ('execute_code', {'action': 'execute', 'code': 'return 0;', 'safety_checks': None}),
                ('execute_code', {'action': 'get_history', 'limit': None}),
                ('execute_code', {'action': 'replay', 'index': None}),
                ('execute_code', {'action': 'execute', 'code': 'return 0;', 'compiler': 'unknown'}),
                ('execute_code', {'action': 'EXECUTE', 'code': 'return 0;'}),
                *[('execute_code', {'action': action, key: value})
                  for action, key in (('get_history', 'limit'), ('replay', 'index'))
                  for value in (True, False, 1.0, 0.5, '0', '1', '1e0')],
                *[('execute_code', {'action': 'execute', 'code': 'return 0;', 'safety_checks': value})
                  for value in (0, 1, 'true', 'false')],
            ):
                result, sent = await invoke(client, tool, payload, mode)
                check(not sent and (result.is_error or result.structured_content.get('success') is False),
                      'existing local/schema reject/no transport ' + repr(payload))
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
'''


def test_reflection_execution_top_level_cli_contracts(tmp_path):
    assert 'CLI_SUMMARY' in _run(CLI_PROGRAM, tmp_path)


def test_reflection_execution_registered_sdk_contracts(tmp_path):
    assert 'SDK_SUMMARY' in _run(SDK_PROGRAM, tmp_path)
