"""Profiler public routing through fresh SDK and central CLI transport boundaries."""

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
from cli.commands.profiler import profiler
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
    (['start'], {'action': 'profiler_start'}),
    (['start', '--callstacks', '--log-file', 'Fixture.raw'],
     {'action': 'profiler_start', 'enable_callstacks': True, 'log_file': 'Fixture.raw'}),
    (['stop'], {'action': 'profiler_stop'}),
    (['status'], {'action': 'profiler_status'}),
    (['set-areas', '--area', 'CPU=true', '--area', 'Memory=0', '--area', 'CPU=no'],
     {'action': 'profiler_set_areas', 'areas': {'CPU': False, 'Memory': False}}),
    (['set-areas'], {'action': 'profiler_set_areas', 'areas': {}}),
    (['frame-timing'], {'action': 'get_frame_timing'}),
    (['get-counters', '--category', 'Render', '--counter', 'Owned', '--counter', 'Owned'],
     {'action': 'get_counters', 'category': 'Render', 'counters': ['Owned', 'Owned']}),
    (['get-counters', '--category', ''], {'action': 'get_counters', 'category': ''}),
    (['object-memory', '--path', 'Assets/Fixture.asset'],
     {'action': 'get_object_memory', 'object_path': 'Assets/Fixture.asset'}),
    (['object-memory', '--path', 'Fixture'],
     {'action': 'get_object_memory', 'object_path': 'Fixture'}),
    (['memory-snapshot', '--path', 'Fixture.snap'],
     {'action': 'memory_take_snapshot', 'snapshot_path': 'Fixture.snap'}),
    (['memory-snapshot'], {'action': 'memory_take_snapshot'}),
    (['memory-list', '--search-path', 'Fixture'],
     {'action': 'memory_list_snapshots', 'search_path': 'Fixture'}),
    (['memory-compare', '--a', 'First.snap', '--b', 'Second.snap'],
     {'action': 'memory_compare_snapshots', 'snapshot_a': 'First.snap', 'snapshot_b': 'Second.snap'}),
    (['frame-debugger-enable'], {'action': 'frame_debugger_enable'}),
    (['frame-debugger-disable'], {'action': 'frame_debugger_disable'}),
    (['frame-debugger-events'], {'action': 'frame_debugger_get_events', 'page_size': 50}),
    (['frame-debugger-events', '--page-size', '0', '--cursor', '0'],
     {'action': 'frame_debugger_get_events', 'page_size': 0, 'cursor': 0}),
    (['frame-debugger-events', '--page-size', '2', '--cursor', '-1'],
     {'action': 'frame_debugger_get_events', 'page_size': 2, 'cursor': -1}),
]
for args, wire in cases:
    for failed, wrapped in ((False, False), (False, True), (True, False), (True, True)):
        reply = {
            'success': not failed, 'message': 'Controlled profiler diagnostic',
            'data': {'enabled': False, 'count': 0, 'events': [], 'next_cursor': None},
        }
        before = len(requests)
        result = runner.invoke(
            cli, ['--format', 'json', '--instance', 'Project@fixture', 'profiler', *args],
        )
        sent = requests[before:]
        check(result.exit_code == (1 if failed else 0), 'CLI exit ' + repr(args))
        check(sent == [{'type': 'manage_profiler', 'params': wire, 'unity_instance': 'Project@fixture'}],
              'CLI literal wire ' + repr(args))
        check(json.loads(result.output) == reply, 'single document/diagnostic ' + repr(args))
        print('CLI_WIRE', json.dumps({'args': args, 'requests': sent, 'response': reply}))
for args in (
    ['set-areas', '--area', 'CPU'], ['set-areas', '--area', '=true'],
    ['set-areas', '--area', 'CPU=bad'], ['set-areas', '--area', 'CPU='],
    ['frame-debugger-events', '--page-size', 'nan'],
    ['frame-debugger-events', '--cursor', 'inf'],
):
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', 'profiler', *args])
    check(result.exit_code != 0 and len(requests) == before, 'invalid CLI no HTTP ' + repr(args))
reply = {'success': True, 'data': {'enabled': False, 'count': 0}}
result = runner.invoke(cli, ['--format', 'text', 'profiler', 'status'])
check(result.exit_code == 0 and 'enabled' in result.output, 'text output preserved')
for command in (None, *profiler.commands):
    result = runner.invoke(cli, ['profiler', *([command] if command else []), '--help'])
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

async def send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    return copy.deepcopy(raw)

PluginHub.send_command_for_instance = staticmethod(send)

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

server = FastMCP('profiler-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
server.enable(tags={'group:profiling'}, components={'tool'})
cases = [
    {'action': 'PROFILER_START'},
    {'action': 'profiler_start', 'enable_callstacks': None, 'log_file': None},
    {'action': 'profiler_start', 'enable_callstacks': False},
    {'action': 'profiler_start', 'enable_callstacks': True, 'log_file': 'Fixture.raw'},
    {'action': 'profiler_start', 'log_file': ''},
    {'action': 'profiler_stop'}, {'action': 'profiler_status'},
    {'action': 'profiler_set_areas', 'areas': {'CPU': True, 'Memory': False}},
    {'action': 'profiler_set_areas', 'areas': {}},
    {'action': 'get_frame_timing'},
    {'action': 'get_counters', 'category': 'Render', 'counters': ['Owned', 'Owned']},
    {'action': 'get_counters', 'category': '', 'counters': []},
    {'action': 'get_object_memory', 'object_path': 'Assets/Fixture.asset'},
    {'action': 'get_object_memory', 'object_path': 'Fixture'},
    {'action': 'memory_take_snapshot', 'snapshot_path': 'Fixture.snap'},
    {'action': 'memory_take_snapshot', 'snapshot_path': None},
    {'action': 'memory_list_snapshots', 'search_path': ''},
    {'action': 'memory_compare_snapshots', 'snapshot_a': 'First.snap', 'snapshot_b': 'Second.snap'},
    {'action': 'frame_debugger_enable'}, {'action': 'frame_debugger_disable'},
    {'action': 'frame_debugger_get_events'},
    {'action': 'frame_debugger_get_events', 'page_size': 0, 'cursor': 0},
    {'action': 'frame_debugger_get_events', 'page_size': 2147483647, 'cursor': 2147483647},
    {'action': 'frame_debugger_get_events', 'page_size': 2, 'cursor': 1},
    {'action': 'frame_debugger_get_events', 'page_size': -1, 'cursor': -1},
    {'action': 'ping'},
]

async def invoke(client, payload, mode):
    before = len(requests)
    result = await client.call_tool('manage_profiler', payload, raise_on_error=False)
    sent = requests[before:]
    print('SDK_WIRE', json.dumps({'mode': mode, 'payload': payload, 'wires': sent,
                                 'response': result.structured_content, 'is_error': result.is_error}))
    return result, sent

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            discovered = {tool.name: tool for tool in await client.list_tools()}
            check('manage_profiler' in discovered, 'actual profiling registry discovery ' + mode)
            print('FULL_SCHEMA', mode, json.dumps(discovered['manage_profiler'].inputSchema, sort_keys=True))
            for payload in cases:
                for failed, wrapped in ((False, False), (True, True)):
                    response = {
                        'success': not failed, 'message': 'Controlled profiler diagnostic',
                        'data': {'enabled': False, 'count': 0, 'events': [], 'next_cursor': None},
                    }
                    raw = {'status': 'success', 'result': response} if wrapped else response
                    result, sent = await invoke(client, payload, mode)
                    expected = {key: value for key, value in payload.items() if value is not None}
                    expected['action'] = payload['action'].lower()
                    check(sent == [('Project@fixture', 'manage_profiler', expected)],
                          'SDK literal values/defaults ' + mode + repr(payload))
                    check(result.structured_content == response, 'opaque native result fidelity ' + mode)
            raw = {'success': True, 'data': {'events': []}}
            for value in (0, 1, -1, None):
                payload = {'action': 'frame_debugger_get_events', 'page_size': value, 'cursor': value}
                result, sent = await invoke(client, payload, mode)
                expected = {'action': 'frame_debugger_get_events'}
                if value is not None:
                    expected.update(page_size=int(value), cursor=int(value))
                check(sent == [('Project@fixture', 'manage_profiler', expected)],
                      'typed paging control ' + mode + repr(value))
            for key in ('page_size', 'cursor'):
                for value in (True, False, '0', '1', '1e0', 1.0, 0.5):
                    result, sent = await invoke(client, {'action': 'frame_debugger_get_events', key: value}, mode)
                    check(result.is_error and not sent, 'strict paging before transport ' + mode + key + repr(value))
            for key in ('areas', 'enable_callstacks'):
                for value in (0, 1):
                    payload = {'action': 'profiler_set_areas', 'areas': {'CPU': value}} if key == 'areas' else {'action': 'profiler_start', key: value}
                    result, sent = await invoke(client, payload, mode)
                    check(result.is_error and not sent, 'numeric flag before transport ' + mode + key + repr(value))
            for key, value in (('cursor', float('nan')), ('page_size', float('inf'))):
                result, sent = await invoke(
                    client, {'action': 'frame_debugger_get_events', key: value}, mode,
                )
                check(sent == [('Project@fixture', 'manage_profiler',
                                {'action': 'frame_debugger_get_events'})],
                      'Client nonfinite optional value serialized as null ' + mode + key)
            for payload in (
                {'action': ''}, {'action': ' profiler_start '}, {'action': 'unknown'},
                {'action': 'frame_debugger_get_events', 'cursor': 'NaN'},
                {'action': 'frame_debugger_get_events', 'page_size': 'Infinity'},
                {'action': 'profiler_set_areas', 'areas': {'CPU': 'bad'}},
                {'action': 'get_counters', 'counters': [1]},
            ):
                result, sent = await invoke(client, payload, mode)
                check(not sent and (result.is_error or result.structured_content.get('success') is False),
                      'local/schema rejection before transport ' + mode + repr(payload))
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
"""


def test_profiler_top_level_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_profiler_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)
