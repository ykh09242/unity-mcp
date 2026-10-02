"""Build forwarding and polling through fresh registered SDK and central CLI paths."""

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
from cli.commands.build import build
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
    (['run'], {'action': 'build'}),
    (['run', '--target', 'windows64', '--profile', 'Assets/Profiles/Android.asset', '--backend', 'il2cpp'],
     {'action': 'build', 'target': 'windows64', 'profile': 'Assets/Profiles/Android.asset',
      'scripting_backend': 'il2cpp'}),
    (['run', '--profile', 'Assets/Profiles/Android.asset', '--output', 'FixtureOutput',
      '--development', '--subtarget', 'server', '--clean', '--auto-run'],
     {'action': 'build', 'profile': 'Assets/Profiles/Android.asset', 'output_path': 'FixtureOutput',
      'development': True, 'subtarget': 'server', 'options': ['clean_build', 'auto_run']}),
    (['status'], {'action': 'status'}),
    (['status', 'build-owned'], {'action': 'status', 'job_id': 'build-owned'}),
    (['platform'], {'action': 'platform'}),
    (['platform', 'android'], {'action': 'platform', 'target': 'android'}),
    (['settings', 'defines'], {'action': 'settings', 'property': 'defines'}),
    *[(['settings', 'defines', '--value', value],
       {'action': 'settings', 'property': 'defines', 'value': value}) for value in ('', '0', 'false')],
    (['scenes'], {'action': 'scenes'}),
    (['scenes', '--set', 'Assets/Scenes/First.unity, Assets/Scenes/Second.unity'],
     {'action': 'scenes', 'scenes': [{'path': 'Assets/Scenes/First.unity', 'enabled': True},
                                    {'path': 'Assets/Scenes/Second.unity', 'enabled': True}]}),
    (['profiles', 'Assets/Profiles/Android.asset', '--activate'],
     {'action': 'profiles', 'profile': 'Assets/Profiles/Android.asset', 'activate': True}),
    (['profiles'], {'action': 'profiles'}),
    (['batch', '--targets', 'windows64, android', '--output-dir', 'FixtureBuilds', '--development'],
     {'action': 'batch', 'targets': ['windows64', 'android'], 'output_dir': 'FixtureBuilds', 'development': True}),
    (['batch', '--profiles', 'Assets/Profiles/Android.asset,Assets/Profiles/Windows.asset'],
     {'action': 'batch', 'profiles': ['Assets/Profiles/Android.asset', 'Assets/Profiles/Windows.asset']}),
    (['cancel', 'batch-owned'], {'action': 'cancel', 'job_id': 'batch-owned'}),
]
for args, expected in cases:
    for failed, wrapped in ((False, False), (False, True), (True, False), (True, True)):
        reply = {'success': not failed, 'message': 'Controlled native build diagnostic',
                 'data': {'count': 0, 'development': False, 'scenes': [], 'report': None}}
        before = len(requests)
        result = runner.invoke(
            cli, ['--format', 'json', '--instance', 'Project@fixture', 'build', *args],
        )
        sent = requests[before:]
        check(result.exit_code == (1 if failed else 0), 'CLI native failure exit ' + repr(args))
        check(sent == [{'type': 'manage_build', 'params': expected, 'unity_instance': 'Project@fixture'}],
              'CLI literal values/defaults ' + repr(args))
        check(json.loads(result.output) == reply, 'single JSON/falsey data ' + repr(args))
        print('CLI_WIRE', json.dumps({'args': args, 'requests': sent, 'response': reply}))
for args, identity in ((['run'], 'build-owned'), (['batch', '--targets', 'windows64,android'], 'batch-owned')):
    for phase in ('pending', 'succeeded', 'failed', 'cancelled'):
        reply = {'success': True, 'data': {'job_id': identity, 'result': phase, 'completed': 0}}
        if phase == 'pending':
            reply.update(_mcp_status='pending', _mcp_poll_interval=5.0)
        command = args if phase == 'pending' else ['status', identity]
        result = runner.invoke(cli, ['--format', 'json', 'build', *command])
        check(result.exit_code == 0 and json.loads(result.output) == reply, 'pending/terminal state fidelity')
        check(requests[-1]['params'].get('job_id') == (None if phase == 'pending' else identity),
              'explicit CLI polling identity')
        print('CLI_WIRE', json.dumps({'args': command, 'requests': [requests[-1]], 'response': reply}))
reply = {'success': True, '_mcp_status': 'pending', 'data': {'job_id': 'batch-owned'}}
result = runner.invoke(cli, ['--format', 'text', 'build', 'batch', '--targets', 'windows64'])
check(result.exit_code == 0 and 'unity-mcp build status batch-owned' in result.output, 'text poll hint')
for args in (['run', '--backend', 'bad'], ['cancel']):
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', 'build', *args])
    check(result.exit_code != 0 and len(requests) == before, 'invalid CLI before HTTP')
for command in (None, *build.commands):
    result = runner.invoke(cli, ['build', *([command] if command else []), '--help'])
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

server = FastMCP('build-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
service = CustomToolService(server)
definition = ToolDefinitionModel(name='manage_build', requires_polling=True,
                                 poll_action='status', max_poll_seconds=1800)
service.register_global_tools([definition])
assert 'manage_build' not in service._global_tools
service._register_project_tools('fixture', [definition], project_hash='fixture')
cases = [
    ({'action': 'BUILD'}, {'action': 'build'}),
    ({'action': 'build', 'target': 'windows64', 'profile': 'Assets/Profiles/Android.asset',
      'scripting_backend': 'il2cpp', 'development': 'false'},
     {'action': 'build', 'target': 'windows64', 'profile': 'Assets/Profiles/Android.asset',
      'scripting_backend': 'il2cpp', 'development': False}),
    ({'action': 'build', 'profile': 'Assets/Profiles/Android.asset'},
     {'action': 'build', 'profile': 'Assets/Profiles/Android.asset'}),
    ({'action': 'build', 'profile': 'Assets/Profiles/Android.asset', 'output_path': 'FixtureOutput',
      'options': '["clean_build","strict_mode"]', 'development': '0'},
     {'action': 'build', 'profile': 'Assets/Profiles/Android.asset', 'output_path': 'FixtureOutput',
      'options': ['clean_build', 'strict_mode'], 'development': False}),
    ({'action': 'build', 'scenes': '[]', 'options': '[]'}, {'action': 'build', 'scenes': [], 'options': []}),
    ({'action': 'scenes', 'scenes': '[{"path":"Assets/Scenes/First.unity","enabled":false}]'},
     {'action': 'scenes', 'scenes': [{'path': 'Assets/Scenes/First.unity', 'enabled': False}]}),
    ({'action': 'build', 'scenes': 'Assets/Scenes/First.unity, Assets/Scenes/Second.unity'},
     {'action': 'build', 'scenes': ['Assets/Scenes/First.unity', 'Assets/Scenes/Second.unity']}),
    ({'action': 'scenes', 'scenes': ''}, {'action': 'scenes'}),
    *[({'action': 'settings', 'property': 'defines', 'value': value},
       {'action': 'settings', 'property': 'defines', **({'value': value} if value is not None else {})})
      for value in (None, '', '0', 'false')],
    ({'action': 'profiles', 'profile': 'Assets/Profiles/Android.asset', 'activate': 'false'},
     {'action': 'profiles', 'profile': 'Assets/Profiles/Android.asset', 'activate': False}),
    ({'action': 'profiles', 'activate': 'true'}, {'action': 'profiles', 'activate': True}),
    ({'action': 'batch', 'targets': '["windows64","android"]', 'development': 'false'},
     {'action': 'batch', 'targets': ['windows64', 'android'], 'development': False}),
    ({'action': 'batch', 'profiles': '["Assets/Profiles/Android.asset","Assets/Profiles/Windows.asset"]'},
     {'action': 'batch', 'profiles': ['Assets/Profiles/Android.asset', 'Assets/Profiles/Windows.asset']}),
    ({'action': 'status', 'job_id': 'build-owned'}, {'action': 'status', 'job_id': 'build-owned'}),
    ({'action': 'status', 'job_id': None}, {'action': 'status'}),
    ({'action': 'cancel', 'job_id': 'batch-owned'}, {'action': 'cancel', 'job_id': 'batch-owned'}),
    ({'action': 'platform', 'target': 'android'}, {'action': 'platform', 'target': 'android'}),
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
            check('manage_build' in discovered and 'execute_custom_tool' in discovered,
                  'actual registry/builtin preserved ' + mode)
            print('FULL_SCHEMA', mode, json.dumps(discovered['manage_build'].inputSchema, sort_keys=True))
            for payload, expected in cases:
                for failed, wrapped in ((False, False), (True, True)):
                    response = {'success': not failed, 'message': 'Controlled native build diagnostic',
                                'data': {'development': False, 'count': 0, 'scenes': [], 'report': None}}
                    raw = {'status': 'success', 'result': response} if wrapped else response
                    result, sent = await invoke(client, 'manage_build', payload, mode)
                    check(sent == [('Project@fixture', 'manage_build', expected)], 'SDK parsed literal input ' + mode)
                    check(result.structured_content == response, 'opaque native result fidelity ' + mode)
            for state in ('pending', 'building', 'succeeded', 'failed', 'cancelled'):
                raw = {'success': True, 'data': {'job_id': 'build-owned', 'result': state, 'completed': 0}}
                if state in ('pending', 'building'):
                    raw.update(_mcp_status='pending', _mcp_poll_interval=5.0)
                result, sent = await invoke(client, 'manage_build', {'action': 'status', 'job_id': 'build-owned'}, mode)
                check(result.structured_content == raw and len(sent) == 1, 'direct status does not implicitly poll')
            for state in ('succeeded', 'failed', 'cancelled', 'absent'):
                initial = {'success': True, '_mcp_status': 'pending', '_mcp_poll_interval': 0.001,
                           'data': {'job_id': 'batch-owned', 'completed': 0, 'total': 2}}
                intermediate = {'success': True, '_mcp_status': 'pending', '_mcp_poll_interval': 0.001,
                                'data': {'job_id': 'batch-owned', 'completed': 1}}
                final = {'success': state != 'absent', 'data': {'job_id': 'batch-owned', 'result': state,
                                                                            'completed': 0, 'active': False}}
                if state == 'absent':
                    final['error'] = 'No job found'
                replies = [initial, intermediate, final]
                original = {'action': 'batch', 'targets': ['windows64', 'android'], 'development': False}
                result, sent = await invoke(client, 'execute_custom_tool',
                                            {'tool_name': 'manage_build', 'parameters': original}, mode)
                expected_poll = {**original, 'action': 'status', 'job_id': 'batch-owned'}
                check(sent == [('Project@fixture', 'manage_build', original),
                               ('Project@fixture', 'manage_build', expected_poll),
                               ('Project@fixture', 'manage_build', expected_poll)], 'response jobID poll identity')
                check(result.structured_content['success'] is final['success']
                      and result.structured_content['data'] == final['data']
                      and result.structured_content.get('error') == final.get('error'), 'terminal polling envelope')
                check(original == {'action': 'batch', 'targets': ['windows64', 'android'], 'development': False},
                      'caller parameters unchanged')
            for payload in ({'action': 'unknown'}, {'action': ' build '},
                            {'action': 'build', 'scenes': []}, {'action': 'build', 'development': False}):
                result, sent = await invoke(client, 'manage_build', payload, mode)
                check(not sent and (result.is_error or result.structured_content.get('success') is False),
                      'local/schema rejection without transport')
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
'''


def test_build_top_level_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_build_registered_sdk_and_polling_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)
