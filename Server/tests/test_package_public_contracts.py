"""Package commands and polling through fresh registered SDK and central CLI paths."""

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


CLI_PROGRAM = r'''
import copy
import json
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.commands.packages import packages
from cli.utils import connection

requests, failures = [], []
checks = 0
reply = {}
wrapped = False
client_type = httpx.AsyncClient

def respond(request):
    requests.append(json.loads(request.content))
    value = copy.deepcopy(reply)
    body = {'status': 'success', 'result': value} if wrapped else value
    return httpx.Response(200, json=body)

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
    (['add', 'com.fixture.tool@1.2.3'], {'action': 'add_package', 'package': 'com.fixture.tool@1.2.3'}),
    (['add', 'https://example.test/fixture.git#main'],
     {'action': 'add_package', 'package': 'https://example.test/fixture.git#main'}),
    (['add', 'file:../Fixture'], {'action': 'add_package', 'package': 'file:../Fixture'}),
    (['remove', 'com.fixture.tool'], {'action': 'remove_package', 'package': 'com.fixture.tool'}),
    (['remove', 'com.fixture.tool', '--force'],
     {'action': 'remove_package', 'package': 'com.fixture.tool', 'force': True}),
    (['list'], {'action': 'list_packages'}),
    (['search', 'com.fixture'], {'action': 'search_packages', 'query': 'com.fixture'}),
    (['info', 'com.fixture.tool'], {'action': 'get_package_info', 'package': 'com.fixture.tool'}),
    (['status'], {'action': 'status'}),
    (['status', 'package-owned'], {'action': 'status', 'job_id': 'package-owned'}),
    (['embed', 'com.fixture.tool'], {'action': 'embed_package', 'package': 'com.fixture.tool'}),
    (['resolve'], {'action': 'resolve_packages'}),
    (['list-registries'], {'action': 'list_registries'}),
    (['add-registry', 'Fixture', '--url', 'https://packages.example.test', '--scope', 'com.fixture',
      '--scope', 'com.other'],
     {'action': 'add_registry', 'name': 'Fixture', 'url': 'https://packages.example.test',
      'scopes': ['com.fixture', 'com.other']}),
    (['remove-registry', 'Fixture'], {'action': 'remove_registry', 'name': 'Fixture'}),
    (['ping'], {'action': 'ping'}),
]
for args, expected in cases:
    for failed, wrapped in ((False, False), (False, True), (True, False), (True, True)):
        reply = {
            'success': not failed, 'message': 'Controlled package diagnostic',
            'data': {'count': 0, 'force': False, 'packages': [], 'warning': None},
        }
        before = len(requests)
        result = runner.invoke(
            cli, ['--format', 'json', '--instance', 'Project@fixture', 'packages', *args],
        )
        sent = requests[before:]
        check(result.exit_code == (1 if failed else 0), 'native diagnostic exit ' + repr(args))
        check(sent == [{'type': 'manage_packages', 'params': expected, 'unity_instance': 'Project@fixture'}],
              'literal CLI values/defaults ' + repr(args))
        check(json.loads(result.output) == reply, 'one JSON document/falsey response ' + repr(args))
        print('CLI_WIRE', json.dumps({'args': args, 'requests': sent, 'response': reply}))

for command, initial_action in (('add', 'add_package'), ('remove', 'remove_package'), ('embed', 'embed_package')):
    reply = {'success': True, '_mcp_status': 'pending', '_mcp_poll_interval': 3.0,
             'data': {'job_id': 'package-owned', 'operation': command}}
    args = [command, 'com.fixture.tool']
    result = runner.invoke(cli, ['--format', 'json', 'packages', *args])
    check(result.exit_code == 0 and json.loads(result.output) == reply, 'pending JSON envelope')
    check(requests[-1]['params']['action'] == initial_action, 'initial package action')
    print('CLI_WIRE', json.dumps({'args': args, 'requests': [requests[-1]], 'response': reply}))
    result = runner.invoke(cli, ['--format', 'text', 'packages', *args])
    check(result.exit_code == 0 and 'unity-mcp packages status package-owned' in result.output,
          'text job hint ' + command)

for phase in ('running', 'succeeded', 'failed'):
    reply = {'success': True, 'data': {'job_id': 'package-owned', 'status': phase, 'error': None}}
    if phase == 'running':
        reply.update(_mcp_status='pending', _mcp_poll_interval=3.0)
    result = runner.invoke(cli, ['--format', 'json', 'packages', 'status', 'package-owned'])
    check(result.exit_code == 0 and json.loads(result.output) == reply, 'opaque job status fidelity')
    print('CLI_WIRE', json.dumps({'args': ['status', 'package-owned'],
                                 'requests': [requests[-1]], 'response': reply}))

# Exact output of the controlled actual native GetStatus/ToSerializable handlers.
native_failed_status = json.loads("""{
  "success": true,
  "message": "Job fixture-failed-job failed (add 'com.fixture.tool'): controlled native package failure",
  "data": {"job_id": "fixture-failed-job", "status": "failed", "operation": "add",
           "package_": "com.fixture.tool", "started_unix_ms": 1000, "finished_unix_ms": 2000,
           "last_update_unix_ms": 2000, "error": "controlled native package failure",
           "result_version": null, "result_name": null}
}""")
for wrapped in (False, True):
    reply = native_failed_status
    result = runner.invoke(cli, ['--format', 'json', 'packages', 'status', 'fixture-failed-job'])
    check(result.exit_code == 0 and json.loads(result.output) == native_failed_status,
          'successful native status query retains failed job envelope')
    check(requests[-1]['params'] == {'action': 'status', 'job_id': 'fixture-failed-job'},
          'native failed job status identity')
    print('CLI_WIRE', json.dumps({'args': ['status', 'fixture-failed-job'],
                                 'requests': [requests[-1]], 'response': reply}))

for args in (['add'], ['add-registry', 'Fixture', '--url', 'https://packages.example.test'],
             ['add-registry', 'Fixture', '--scope', 'com.fixture']):
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', 'packages', *args])
    check(result.exit_code != 0 and len(requests) == before, 'missing argument before HTTP')
for command in (None, *packages.commands):
    result = runner.invoke(cli, ['packages', *([command] if command else []), '--help'])
    check(result.exit_code == 0, 'help ' + repr(command))
    print('FULL_HELP', json.dumps({'command': command, 'text': result.output}))
print('CLI_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
assert not failures, failures
'''


SDK_PROGRAM = r'''
import copy
import json
from types import SimpleNamespace
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from services.tools import register_all_tools
from services.custom_tool_service import CustomToolService
import services.custom_tool_service as custom
from models.models import ToolDefinitionModel
from core.config import config
from transport.plugin_hub import PluginHub

config.transport_mode = 'http'
config.http_remote_hosted = False
requests, failures = [], []
checks = 0
raw = {}
replies = []

def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)

async def send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    return copy.deepcopy(replies.pop(0) if replies else raw)

PluginHub.send_command_for_instance = staticmethod(send)
custom.get_unity_connection_pool = lambda: SimpleNamespace(discover_all_instances=lambda: [])

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

server = FastMCP('package-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
service = CustomToolService(server)
definition = ToolDefinitionModel(name='manage_packages', requires_polling=True, poll_action='status')
service.register_global_tools([definition])
assert 'manage_packages' not in service._global_tools
service._register_project_tools('fixture', [definition], project_hash='fixture')
native_failed_status = json.loads("""{
  "success": true,
  "message": "Job fixture-failed-job failed (add 'com.fixture.tool'): controlled native package failure",
  "data": {"job_id": "fixture-failed-job", "status": "failed", "operation": "add",
           "package_": "com.fixture.tool", "started_unix_ms": 1000, "finished_unix_ms": 2000,
           "last_update_unix_ms": 2000, "error": "controlled native package failure",
           "result_version": null, "result_name": null}
}""")
cases = [
    *[({'action': action}, {'action': action}) for action in ('list_packages', 'ping', 'status',
                                                           'resolve_packages', 'list_registries')],
    ({'action': 'ADD_PACKAGE', 'package': 'com.fixture.tool@1.2.3'},
     {'action': 'add_package', 'package': 'com.fixture.tool@1.2.3'}),
    *[({'action': 'remove_package', 'package': 'com.fixture.tool', 'force': force},
       {'action': 'remove_package', 'package': 'com.fixture.tool',
        **({'force': force} if force is not None else {})}) for force in (None, False, True)],
    ({'action': 'embed_package', 'package': 'com.fixture.tool'},
     {'action': 'embed_package', 'package': 'com.fixture.tool'}),
    ({'action': 'get_package_info', 'package': 'com.fixture.tool'},
     {'action': 'get_package_info', 'package': 'com.fixture.tool'}),
    ({'action': 'search_packages', 'query': 'com.fixture'},
     {'action': 'search_packages', 'query': 'com.fixture'}),
    ({'action': 'status', 'job_id': 'package-owned'}, {'action': 'status', 'job_id': 'package-owned'}),
    ({'action': 'status', 'job_id': None}, {'action': 'status'}),
    *[({'action': 'add_registry', 'name': 'Fixture', 'url': 'https://packages.example.test', 'scopes': scopes},
       {'action': 'add_registry', 'name': 'Fixture', 'url': 'https://packages.example.test',
        **({'scopes': scopes} if scopes is not None else {})}) for scopes in (None, [], ['com.fixture'])],
    ({'action': 'remove_registry', 'name': 'Fixture'}, {'action': 'remove_registry', 'name': 'Fixture'}),
    ({'action': 'remove_registry', 'url': 'https://packages.example.test'},
     {'action': 'remove_registry', 'url': 'https://packages.example.test'}),
    *[({'action': 'add_package', 'package': value},
       {'action': 'add_package', **({'package': value} if value is not None else {})})
      for value in (None, '', 'file:../Fixture', 'https://example.test/fixture.git#main')],
    ({'action': 'search_packages', 'query': ''}, {'action': 'search_packages', 'query': ''}),
]

async def invoke(client, tool, payload, mode):
    before = len(requests)
    result = await client.call_tool(tool, payload, raise_on_error=False)
    sent = requests[before:]
    print('SDK_WIRE', json.dumps({'mode': mode, 'tool': tool, 'payload': payload, 'wires': sent,
                                 'response': result.structured_content, 'is_error': result.is_error}))
    return result, sent

async def main():
    global raw, replies
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            discovered = {tool.name: tool for tool in await client.list_tools()}
            check('manage_packages' in discovered and 'execute_custom_tool' in discovered,
                  'actual registry/builtin preserved ' + mode)
            print('FULL_SCHEMA', mode, json.dumps(discovered['manage_packages'].inputSchema, sort_keys=True))
            for payload, expected in cases:
                for failed, wrapped in ((False, False), (True, True)):
                    response = {'success': not failed, 'message': 'Controlled package diagnostic',
                                'data': {'force': False, 'count': 0, 'packages': [], 'warning': None}}
                    raw = {'status': 'success', 'result': response} if wrapped else response
                    result, sent = await invoke(client, 'manage_packages', payload, mode)
                    check(sent == [('Project@fixture', 'manage_packages', expected)], 'literal SDK values/defaults')
                    check(result.structured_content == response, 'opaque native result fidelity')
            for phase in ('running', 'succeeded', 'failed'):
                raw = {'success': True, 'data': {'job_id': 'package-owned', 'status': phase, 'error': None}}
                if phase == 'running':
                    raw.update(_mcp_status='pending', _mcp_poll_interval=3.0)
                result, sent = await invoke(client, 'manage_packages', {'action': 'status', 'job_id': 'package-owned'}, mode)
                check(result.structured_content == raw and len(sent) == 1, 'direct status fidelity without implicit poll')
            for action in ('add_package', 'remove_package', 'embed_package', 'list_packages', 'search_packages'):
                original = {'action': action}
                if action in ('add_package', 'remove_package', 'embed_package'):
                    original['package'] = 'com.fixture.tool@1.2.3' if action == 'add_package' else 'com.fixture.tool'
                if action == 'remove_package':
                    original['force'] = False
                if action == 'search_packages':
                    original['query'] = 'com.fixture'
                saved = copy.deepcopy(original)
                for failed in (False, True):
                    initial = {'success': True, '_mcp_status': 'pending', '_mcp_poll_interval': 0.001,
                               'data': {'job_id': 'package-owned', 'operation': action}}
                    intermediate = {'success': True, '_mcp_status': 'pending', '_mcp_poll_interval': 0.001,
                                    'data': {'job_id': 'package-owned', 'status': 'running'}}
                    final = {'success': not failed, 'message': 'Terminal package diagnostic',
                             'data': {'job_id': 'package-owned', 'status': 'failed' if failed else 'succeeded',
                                      'count': 0, 'force': False, 'packages': []}}
                    if failed:
                        final.update(_mcp_status='error', error='Controlled UPM failure')
                    replies = [initial, intermediate, final]
                    result, sent = await invoke(client, 'execute_custom_tool',
                                                {'tool_name': 'manage_packages', 'parameters': original}, mode)
                    expected_poll = {**original, 'action': 'status', 'job_id': 'package-owned'}
                    check(sent == [('Project@fixture', 'manage_packages', original),
                                   ('Project@fixture', 'manage_packages', expected_poll),
                                   ('Project@fixture', 'manage_packages', expected_poll)], 'returned jobID status polling')
                    check(result.structured_content['success'] is final['success']
                          and result.structured_content['data'] == final['data']
                          and result.structured_content.get('error') == final.get('error'), 'polling terminal fidelity')
                    check(original == saved, 'polling does not mutate caller parameters')
            initial = {'success': True, '_mcp_status': 'pending', '_mcp_poll_interval': 0.001,
                       'data': {'job_id': native_failed_status['data']['job_id']}}
            replies = [initial, native_failed_status]
            original = {'action': 'add_package', 'package': 'com.fixture.tool'}
            result, sent = await invoke(client, 'execute_custom_tool',
                                        {'tool_name': 'manage_packages', 'parameters': original}, mode)
            check(sent == [('Project@fixture', 'manage_packages', original),
                           ('Project@fixture', 'manage_packages',
                            {**original, 'action': 'status', 'job_id': 'fixture-failed-job'})],
                  'native failed status returned ID adoption')
            check(result.structured_content['success'] is True
                  and result.structured_content['data'] == native_failed_status['data']
                  and result.structured_content['message'] == native_failed_status['message'],
                  'native failed job stays successful status-query envelope')
            check(original == {'action': 'add_package', 'package': 'com.fixture.tool'},
                  'native terminal response does not mutate caller input')
            for payload in ({'action': 'unknown'}, {'action': ' add_package '},
                            {'action': 'add_registry', 'scopes': [False]},
                            {'action': 'status', 'job_id': False}):
                result, sent = await invoke(client, 'manage_packages', payload, mode)
                check(not sent and (result.is_error or result.structured_content.get('success') is False),
                      'local/schema rejection without transport')
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
'''


def test_packages_top_level_cli_contracts(tmp_path):
    assert 'CLI_SUMMARY' in _run(CLI_PROGRAM, tmp_path)


def test_packages_registered_sdk_and_polling_contracts(tmp_path):
    assert 'SDK_SUMMARY' in _run(SDK_PROGRAM, tmp_path)
