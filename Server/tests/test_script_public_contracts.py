"""Fresh registered SDK and central HTTP script contracts, without Unity IO."""

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
import os
import json
import base64
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
    if requests[-1]['params'].get('action') == 'get_sha':
        return httpx.Response(200, json={'status': 'success', 'result': {
            'success': True, 'data': {'sha256': 'a' * 64}}})
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

cases = [
    (['create', 'Fixture'],
     {'action': 'create', 'name': 'Fixture', 'path': 'Assets/Scripts',
      'scriptType': 'MonoBehaviour'}),
    (['create', 'Fixture', '--path', 'Assets/Owned', '--type', 'Plain',
      '--namespace', 'Owned', '--contents', 'class Fixture {}'],
     {'action': 'create', 'name': 'Fixture', 'path': 'Assets/Owned',
      'scriptType': 'Plain', 'namespace': 'Owned', 'contents': 'class Fixture {}'}),
    (['create', 'Fixture', '--contents', '', '--namespace', ''],
     {'action': 'create', 'name': 'Fixture', 'path': 'Assets/Scripts',
      'scriptType': 'MonoBehaviour'}),
    (['read', r'Assets\Owned\Fixture.cs'],
     {'action': 'read', 'name': 'Fixture', 'path': 'Assets/Owned'}),
    (['read', 'Assets/Fixture.CS', '--start-line', '2', '--line-count', '1'],
     {'action': 'read', 'name': 'Fixture', 'path': 'Assets'}),
    (['read', 'Fixture'], {'action': 'read', 'name': 'Fixture', 'path': 'Assets'}),
    (['delete', 'Assets/Owned/Fixture.cs', '--force'],
     {'action': 'delete', 'name': 'Fixture', 'path': 'Assets/Owned'}),
    (['edit', 'Assets/Fixture.cs', '--edits',
      '[{"startLine":1,"startCol":1,"endLine":1,"endCol":1,"newText":""}]'],
     {'action': 'apply_text_edits', 'name': 'Fixture', 'path': 'Assets',
      'precondition_sha256': 'a' * 64,
      'edits': [{'startLine': 1, 'startCol': 1, 'endLine': 1, 'endCol': 1, 'newText': ''}]}),
    (['validate', 'Assets/Fixture.cs', '--level', 'standard'],
     {'action': 'validate', 'name': 'Fixture', 'path': 'Assets', 'level': 'standard'}),
]
for args, wire in cases:
    for failed in (False, True):
        for wrapped in (False, True):
            expected = {
                'success': not failed,
                'message': 'Controlled native failure' if failed else 'Done',
                'data': {'contents': 'first\r\n😀second\nthird\n',
                         'scheduledRefresh': False, 'editsApplied': 0, 'reference': None},
            }
            raw = {'status': 'success', 'result': expected} if wrapped else expected
            before = len(requests)
            result = runner.invoke(
                cli, ['--format', 'json', '--instance', 'Project@fixture', 'script', *args]
            )
            label = repr((args, failed, wrapped))
            check(len(requests) == before + (2 if args[0] == 'edit' else 1), 'HTTP dispatch count ' + label)
            if args[0] == 'edit':
                check(requests[-2]['params'] == {'action': 'get_sha', 'name': wire['name'], 'path': wire['path']},
                      'SHA precondition lookup ' + label)
            check(requests[-1]['params'] == wire, 'literal payload ' + label)
            check(requests[-1]['type'] == 'manage_script', 'native action ' + label)
            check(result.exit_code == (1 if failed else 0), 'exit ' + label)
            if failed:
                check('Controlled native failure' in result.output, 'diagnostic ' + label)
            try:
                document = json.loads(result.output)
            except ValueError:
                document = None
            wanted = copy.deepcopy(expected)
            if not failed and '--start-line' in args:
                wanted['data']['contents'] = '😀second\n'
            check(document == wanted, 'single JSON document ' + label)
            print('CLI_WIRE', json.dumps({'args': args, 'request': requests[-1]}, ensure_ascii=True))
    raw = {'success': True, 'data': {'contents': 'first\r\n😀second\nthird\n'}}
    text_result = runner.invoke(cli, ['--format', 'text', 'script', *args])
    check(text_result.exit_code == 0, 'text success ' + repr(args))
    if args[0] == 'read':
        wanted = '😀second\n' if '--start-line' in args else raw['data']['contents']
        # Result.output normalizes CRLF; raw bytes retain source text and the
        # platform TextIOWrapper translation, including echo's final newline.
        wanted_bytes = (wanted + '\n').replace('\n', os.linesep).encode(runner.charset)
        check(text_result.stdout_bytes == wanted_bytes, 'raw text read ' + repr(args))
    elif args[0] != 'validate':
        check('✓' in text_result.output or 'Created' in text_result.output
              or 'Deleted' in text_result.output or 'Applied' in text_result.output,
              'text notice ' + repr(args))

for args in (
    ['read', 'Assets/Fixture.cs', '--start-line', '0'],
    ['read', 'Assets/Fixture.cs', '--line-count', '-1'],
    ['edit', 'Assets/Fixture.cs', '--edits', '{}'],
    ['edit', 'Assets/Fixture.cs', '--edits', 'not json'],
):
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', 'script', *args])
    check(result.exit_code != 0 and len(requests) == before, 'local rejection ' + repr(args))
large = 'first\n' + ('😀x' * 6000) + '\nlast\n'
native = {'success': True, 'message': 'Read native large script', 'data': {
    'uri': 'mcpforunity://path/Assets/Fixture.cs', 'path': 'Assets/Fixture.cs',
    'contents': large, 'contentsEncoded': True,
    'encodedContents': base64.b64encode(large.encode('utf-8')).decode('ascii'),
}}
raw = copy.deepcopy(native)
for arguments, wanted in (([], large), (['--start-line', '3', '--line-count', '1'], 'last\n')):
    result = runner.invoke(cli, ['--format', 'json', 'script', 'read',
                                 'Assets/Fixture.cs', *arguments])
    try:
        document = json.loads(result.output)
    except ValueError:
        document = {}
    data = document.get('data', {})
    check(data.get('contents') == wanted, 'native large plain contents ' + repr(arguments))
    encoded = base64.b64decode(data.get('encodedContents', '')).decode('utf-8')
    check(encoded == wanted and data.get('contentsEncoded') is True,
          'native large encoded contents ' + repr(arguments))
    check(raw == native, 'native response fixture unmutated ' + repr(arguments))
for command in ([], ['create'], ['read'], ['delete'], ['edit'], ['validate']):
    result = runner.invoke(cli, ['script', *command, '--help'])
    check(result.exit_code == 0, 'help ' + repr(command))
    print('FULL_HELP', json.dumps({'command': command, 'text': result.output}))
print('CLI_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
assert not failures, failures
"""


SDK_PROGRAM = r"""
import base64
import copy
import hashlib
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
raw = {'success': True, 'data': {'editsApplied': 0, 'scheduledRefresh': False}}
source = ''

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
    if command == 'manage_script' and params['action'] == 'read' and source:
        return {'success': True, 'data': {'contents': source}}
    return copy.deepcopy(raw)

PluginHub.send_command_for_instance = staticmethod(controlled_send)

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

server = FastMCP('script-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
names = ('manage_script', 'create_script', 'delete_script', 'validate_script',
         'get_sha', 'manage_script_capabilities', 'apply_text_edits')

async def invoke(client, name, payload):
    before = len(requests)
    result = await client.call_tool(name, payload, raise_on_error=False)
    document = result.structured_content
    if document is None and result.content:
        try:
            document = json.loads(result.content[0].text)
        except (ValueError, AttributeError):
            pass
    wires = [row for row in requests[before:] if row[1] == 'manage_script']
    print('SDK_WIRE', json.dumps({'mode': client._fixture_mode, 'tool': name,
                                  'payload': payload, 'wires': wires,
                                  'source': source, 'response': document}, ensure_ascii=True))
    return document, wires

async def main():
    global source, raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            client._fixture_mode = mode
            discovered = {tool.name: tool for tool in await client.list_tools()}
            for name in names:
                check(name in discovered, 'discovery ' + mode + name)
                print('FULL_SCHEMA', mode, name,
                      json.dumps(discovered[name].inputSchema, sort_keys=True))
            for action in ('create', 'read', 'delete'):
                for contents in (None, '', 'class Fixture { string s = "😀"; }'):
                    raw = {'success': True, 'message': 'Done',
                           'data': {'contents': 'class Fixture {}', 'scheduledRefresh': False}}
                    payload = {'action': action, 'name': 'Fixture', 'path': 'Assets/Owned',
                               'contents': contents, 'namespace': '', 'script_type': ''}
                    response, wires = await invoke(client, 'manage_script', payload)
                    check(response['success'] is True, 'router success ' + repr(payload))
                    wire = wires[0][2]
                    check(wire['action'] == action and wire['name'] == 'Fixture'
                          and wire['path'] == 'Assets/Owned', 'router locator ' + repr(payload))
                    check(wire['namespace'] == '' and wire['scriptType'] == '',
                          'empty optional values ' + repr(payload))
                    if contents:
                        if action == 'create':
                            check(base64.b64decode(wire['encodedContents']).decode('utf-8') == contents
                                  and wire['contentsEncoded'] is True, 'encoding ' + mode)
                        else:
                            check(wire['contents'] == contents, 'literal contents ' + mode)
                    else:
                        check('contents' not in wire and 'encodedContents' not in wire,
                              'template omission ' + mode)
            source = ''
            for name, payload, action in (
                ('create_script', {'path': 'Assets/Owned/Fixture.cs', 'contents': ''}, 'create'),
                ('delete_script', {'uri': 'file:///C:/Owned/Assets/Fixture.cs'}, 'delete'),
                ('get_sha', {'uri': 'mcpforunity://path/Assets/Fixture.cs'}, 'get_sha'),
            ):
                raw = {'success': True, 'data': {'sha256': 'zero', 'lengthBytes': 0}}
                response, wires = await invoke(client, name, payload)
                check(response['success'] is True and wires[0][2]['action'] == action,
                      'dedicated action ' + name + mode)
                check(wires[0][0] == 'Project@fixture', 'instance routing ' + name + mode)
                if name == 'get_sha':
                    check(response['data'] == {'sha256': 'zero', 'lengthBytes': 0},
                          'zero byte result ' + mode)
            for include in (False, True):
                raw = {'success': True, 'data': {'diagnostics': [
                    {'severity': 'warning', 'line': 0}, {'severity': 'error'},
                    {'severity': 'fatal'}, {'severity': 'info'},
                ]}}
                response, wires = await invoke(client, 'validate_script', {
                    'uri': 'Assets/Fixture.cs', 'level': 'standard',
                    'include_diagnostics': include,
                })
                summary = response['data']['summary'] if include else response['data']
                check(summary == {'warnings': 1, 'errors': 2}, 'diagnostic summary ' + mode)
                check(wires[0][2]['level'] == 'standard', 'validation level ' + mode)
            before = len(requests)
            response, wires = await invoke(client, 'manage_script_capabilities', {})
            check(response['success'] is True and len(requests) == before,
                  'capabilities readonly local ' + mode)
            for text, start, end, expected in (
                ('a😀b\nc', {'line': 0, 'character': 1}, {'line': 0, 'character': 3}, (1, 2, 1, 3)),
                ('a😀b\r\nc', {'line': 1, 'character': 0}, {'line': 1, 'character': 1}, (2, 1, 2, 2)),
                ('a😀b\rc', {'line': 1, 'character': 0}, {'line': 1, 'character': 1}, (2, 1, 2, 2)),
                ('a\rb\nc\r\nd', {'line': 3, 'character': 0}, {'line': 3, 'character': 1}, (4, 1, 4, 2)),
                ('a\r', {'line': 1, 'character': 0}, {'line': 1, 'character': 0}, (2, 1, 2, 1)),
                ('a\n', {'line': 1, 'character': 0}, {'line': 1, 'character': 0}, (2, 1, 2, 1)),
                ('a\r\n', {'line': 1, 'character': 0}, {'line': 1, 'character': 0}, (2, 1, 2, 1)),
                ('a\u0085\u2028\u2029\v\fb', {'line': 0, 'character': 6},
                 {'line': 0, 'character': 7}, (1, 7, 1, 8)),
                ('😀x', {'line': 0, 'character': 20}, {'line': 0, 'character': 21}, (1, 3, 1, 3)),
            ):
                source = text
                raw = {'success': True, 'data': {'editsApplied': 0, 'scheduledRefresh': False}}
                response, wires = await invoke(client, 'apply_text_edits', {
                    'uri': 'Assets/Fixture.cs',
                    'precondition_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    'options': {'validate': 'syntax'},
                    'edits': [{'range': {'start': start, 'end': end}, 'text': ''}],
                })
                mutations = [row for row in wires if row[2]['action'] == 'apply_text_edits']
                check(response and response.get('success') is True,
                      'LSP normalization ' + repr((mode, text)))
                actual = None
                if mutations:
                    edit = mutations[0][2]['edits'][0]
                    actual = tuple(edit[key] for key in ('startLine', 'startCol', 'endLine', 'endCol'))
                check(not mutations or (edit['newText'] == '' and 'text' not in edit),
                      'empty alias edit ' + mode)
                check(actual == expected, 'LSP coordinates ' + repr((mode, text)))
            for text, bounds, expected in (
                ('a😀b\nc', [4, 5], (2, 1, 2, 2)),
                ('a😀b\rc', [4, 5], (2, 1, 2, 2)),
                ('a😀b\r\nc', [5, 6], (2, 1, 2, 2)),
                ('a\rb\nc\r\nd', [7, 8], (4, 1, 4, 2)),
                ('a\r', [2, 2], (2, 1, 2, 1)),
                ('a\n', [2, 2], (2, 1, 2, 1)),
                ('a\r\n', [3, 3], (2, 1, 2, 1)),
                ('a\r\nb', [1, 1], (1, 2, 1, 2)),
                ('a\r\nb', [3, 3], (2, 1, 2, 1)),
                ('a\u0085b', [1, 2], (1, 2, 1, 3)),
            ):
                source = text
                response, wires = await invoke(client, 'apply_text_edits', {
                    'uri': 'Assets/Fixture.cs',
                    'precondition_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    'options': {'validate': 'syntax'},
                    'edits': [{'range': bounds, 'newText': 'Z'}],
                })
                check(response and response.get('success') is True,
                      'absolute index normalization ' + repr((mode, text, bounds)))
                edit = wires[-1][2]['edits'][0]
                actual = tuple(edit[key] for key in ('startLine', 'startCol', 'endLine', 'endCol'))
                check(actual == expected, 'absolute index coordinates ' + repr((mode, text)))
            for bounds in ([-1, -1], [-1, 1], [2, 2], [5, 5]):
                source = 'a\r\nb'
                response, wires = await invoke(client, 'apply_text_edits', {
                    'uri': 'Assets/Fixture.cs',
                    'precondition_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                    'options': {'validate': 'syntax'},
                    'edits': [{'range': bounds, 'newText': 'Z'}],
                })
                label = repr((mode, bounds))
                check(response and response.get('success') is False
                      and response.get('code') == 'invalid_range',
                      'invalid absolute index rejected ' + label)
                expected_reads = [] if min(bounds) < 0 else ['read']
                check([row[2]['action'] for row in wires] == expected_reads,
                      'invalid absolute index validation order ' + label)
            source = ''
            plain = 'class Fixture { string s = "😀"; }'
            raw = {'success': True, 'data': {
                'contentsEncoded': True,
                'encodedContents': base64.b64encode(plain.encode('utf-8')).decode('ascii'),
            }}
            response, wires = await invoke(client, 'manage_script', {
                'action': 'read', 'name': 'Fixture', 'path': 'Assets',
            })
            check(response['data'] == {'contents': plain}, 'encoded read decoding ' + mode)
            raw = {'success': True, 'data': {'editsApplied': 0}}
            response, wires = await invoke(client, 'apply_text_edits', {
                'uri': 'Assets/Fixture.cs', 'options': {'debug_preview': True},
                'edits': [{'startLine': 1, 'startCol': 1, 'endLine': 1, 'endCol': 1,
                           'newText': ''}],
            })
            check(response['success'] is True and not wires
                  and response['data']['normalizedEdits'][0]['newText'] == '',
                  'local debug preview readonly ' + mode)
            raw = {'success': False, 'error': 'Controlled native preview rejection'}
            response, wires = await invoke(client, 'apply_text_edits', {
                'uri': 'Assets/Fixture.cs', 'options': {'preview': True, 'debug_preview': True},
                'edits': [{'startLine': 1, 'startCol': 1, 'endLine': 1, 'endCol': 1,
                           'newText': ''}],
            })
            check(response == raw and len(wires) == 1
                  and wires[0][2]['action'] == 'preview_text_edits',
                  'native preview precedes debug and preserves error ' + mode)
            source = 'a😀b'
            for bounds in (
                {'start': {'line': 0, 'character': 2}, 'end': {'line': 0, 'character': 3}},
                {'start': {'line': -1, 'character': 0}, 'end': {'line': 0, 'character': 0}},
            ):
                response, wires = await invoke(client, 'apply_text_edits', {
                    'uri': 'Assets/Fixture.cs', 'edits': [{'range': bounds, 'newText': 'Z'}],
                })
                check(response.get('code') == 'invalid_range'
                      and all(row[2]['action'] == 'read' for row in wires),
                      'invalid LSP stops mutation ' + mode)
            source = ''
            for strict in (False, True):
                response, wires = await invoke(client, 'apply_text_edits', {
                    'uri': 'Assets/Fixture.cs', 'strict': strict,
                    'edits': [{'startLine': 0, 'startCol': 0, 'endLine': 0, 'endCol': 0,
                               'newText': 'Z'}],
                })
                check(response.get('code') == 'zero_based_explicit_fields' if strict
                      else wires[0][2]['edits'][0]['startLine'] == 1,
                      'strict explicit controls ' + mode)
            for name, payload in (
                ('manage_script', {'action': 'read', 'name': 'Fixture', 'path': 'Assets'}),
                ('delete_script', {'uri': 'Assets/Fixture.cs'}),
                ('get_sha', {'uri': 'Assets/Fixture.cs'}),
                ('validate_script', {'uri': 'Assets/Fixture.cs'}),
                ('apply_text_edits', {'uri': 'Assets/Fixture.cs', 'edits': [
                    {'startLine': 1, 'startCol': 1, 'endLine': 1, 'endCol': 1, 'newText': ''}]}),
            ):
                raw = {'success': False, 'error': "Script not found at 'Assets/Fixture.cs'."}
                response, wires = await invoke(client, name, payload)
                check(response.get('success') is False and response.get('error') == raw['error'],
                      'native error preservation ' + name + mode)
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures,
                                    'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
"""


def test_script_cli_central_http_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_script_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)
