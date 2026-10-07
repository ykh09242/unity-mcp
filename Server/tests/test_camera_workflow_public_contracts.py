"""Registered camera and central CLI contracts without a live Editor or server."""

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
from cli.utils import connection

requests, failures = [], []
checks = 0
wrapped = False
response = {}
client_type = httpx.AsyncClient

def respond(request):
    requests.append(json.loads(request.content))
    reply = copy.deepcopy(response)
    return httpx.Response(200, json={'status': 'success', 'result': reply} if wrapped else reply)

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
    (['ping'], {'action': 'ping'}),
    (['list'], {'action': 'list_cameras'}),
    (['brain-status'], {'action': 'get_brain_status'}),
    (['create', '--name', 'Fixture', '--preset', 'static', '--priority', '0', '--fov', '42'],
     {'action': 'create_camera', 'properties': {'name': 'Fixture', 'preset': 'static',
                                              'priority': 0, 'fieldOfView': 42.0}}),
    (['ensure-brain', '--camera-ref', 'ExplicitCamera', '--blend-style', 'Cut', '--blend-duration', '0'],
     {'action': 'ensure_brain', 'properties': {'camera': 'ExplicitCamera',
                                             'defaultBlendStyle': 'Cut', 'defaultBlendDuration': 0.0}}),
    (['set-target', 'Fixture', '--search-method', 'by_name', '--follow', 'Reference', '--look-at', 'Missing'],
     {'action': 'set_target', 'target': 'Fixture', 'searchMethod': 'by_name',
      'properties': {'follow': 'Reference', 'lookAt': 'Missing'}}),
    (['set-lens', '0', '--search-method', 'by_id', '--fov', '0', '--near', '0',
      '--far', '0', '--ortho-size', '0', '--dutch', '0'],
     {'action': 'set_lens', 'target': '0', 'searchMethod': 'by_id', 'properties': {
         'fieldOfView': 0.0, 'nearClipPlane': 0.0, 'farClipPlane': 0.0,
         'orthographicSize': 0.0, 'dutch': 0.0}}),
    (['set-priority', 'Fixture', '--priority', '0'],
     {'action': 'set_priority', 'target': 'Fixture', 'properties': {'priority': 0}}),
    (['set-body', 'Root/Fixture', '--search-method', 'by_path', '--body-type', 'Flat',
      '--props', '{"bodyType":"Nested","distance":0,"enabled":false,"reference":null}'],
     {'action': 'set_body', 'target': 'Root/Fixture', 'searchMethod': 'by_path',
      'properties': {'bodyType': 'Nested', 'distance': 0, 'enabled': False, 'reference': None}}),
    (['set-aim', 'Fixture', '--aim-type', 'Flat', '--props', '{"aimType":"Nested","value":0}'],
     {'action': 'set_aim', 'target': 'Fixture', 'properties': {'aimType': 'Nested', 'value': 0}}),
    (['set-noise', 'Fixture', '--amplitude', '0', '--frequency', '0'],
     {'action': 'set_noise', 'target': 'Fixture', 'properties': {'amplitudeGain': 0.0, 'frequencyGain': 0.0}}),
    (['add-extension', 'Fixture', 'Flat', '--props', '{"extensionType":"Nested","value":false}'],
     {'action': 'add_extension', 'target': 'Fixture', 'properties': {'extensionType': 'Nested', 'value': False}}),
    (['remove-extension', 'Fixture', 'Owned'],
     {'action': 'remove_extension', 'target': 'Fixture', 'properties': {'extensionType': 'Owned'}}),
    (['set-blend', '--style', 'Cut', '--duration', '0'],
     {'action': 'set_blend', 'properties': {'style': 'Cut', 'duration': 0.0}}),
    (['force', 'Fixture', '--search-method', 'by_name'],
     {'action': 'force_camera', 'target': 'Fixture', 'searchMethod': 'by_name'}),
    (['release'], {'action': 'release_override'}),
    (['screenshot', '--camera-ref', 'Fixture', '--file-name', 'Owned', '--super-size', '1',
      '--no-include-image', '--max-resolution', '1', '--view-target', '0', '--output-folder', 'Captures'],
     {'action': 'screenshot', 'camera': 'Fixture', 'fileName': 'Owned', 'superSize': 1,
      'includeImage': False, 'maxResolution': 1, 'viewTarget': '0', 'outputFolder': 'Captures'}),
    (['screenshot-multiview', '--max-resolution', '1', '--view-target', 'Fixture'],
     {'action': 'screenshot_multiview', 'maxResolution': 1, 'viewTarget': 'Fixture'}),
    (['create'], {'action': 'create_camera'}),
    (['ensure-brain'], {'action': 'ensure_brain'}),
    (['ensure-brain', '--camera-ref', 'Root/ExplicitCamera'],
     {'action': 'ensure_brain', 'properties': {'camera': 'Root/ExplicitCamera'}}),
    (['ensure-brain', '--camera-ref', '321'],
     {'action': 'ensure_brain', 'properties': {'camera': '321'}}),
    (['set-target', 'Fixture', '--follow', '', '--look-at', ''],
     {'action': 'set_target', 'target': 'Fixture'}),
]
for args, wire in cases:
    for failed, wrapped in ((False, False), (False, True), (True, False)):
        response = {'success': not failed, 'message': 'Controlled native camera diagnostic',
                    'data': {'instanceID': 321, 'count': 0, 'enabled': False, 'reference': None}}
        before = len(requests)
        result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture', 'camera', *args])
        sent = requests[before:]
        label = repr((args, failed, wrapped))
        check(result.exit_code == (1 if failed else 0), 'CLI outcome ' + label)
        check(len(sent) == 1 and sent[0]['type'] == 'manage_camera' and sent[0]['params'] == wire,
              'CLI literal wire ' + label)
        check(sent and sent[0]['unity_instance'] == 'Project@fixture', 'CLI instance ' + label)
        try:
            actual = json.loads(result.output)
        except ValueError:
            actual = None
        check(actual == response, 'CLI one JSON document and falsey response ' + label)
        print('CLI_WIRE', json.dumps({'args': args, 'requests': sent}))
response = {'success': True, 'data': {'count': 0, 'enabled': False}}
result = runner.invoke(cli, ['--format', 'text', 'camera', 'list'])
check(result.exit_code == 0 and 'count' in result.output, 'text output remains available')
for command in ('set-body', 'set-aim', 'add-extension'):
    args = [command, 'Fixture'] + (['Owned'] if command == 'add-extension' else [])
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', 'camera', *args, '--props', '[]'])
    check(result.exit_code == 1 and len(requests) == before, 'invalid props before HTTP ' + command)
for command in ([], *[[args[0]] for args, wire in cases[:18]]):
    result = runner.invoke(cli, ['camera', *command, '--help'])
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
from services.tools.manage_camera import manage_camera
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

server = FastMCP('camera-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
cases = [
    ({'action': action}, {'action': action})
    for action in ('ping', 'get_brain_status', 'list_cameras', 'release_override')
] + [
    ({'action': 'CREATE_CAMERA', 'properties': {'name': 'Fixture', 'preset': 'static',
                                             'priority': 0, 'fieldOfView': 42}},
     {'action': 'create_camera', 'properties': {'name': 'Fixture', 'preset': 'static',
                                             'priority': 0, 'fieldOfView': 42}}),
    ({'action': 'ensure_brain', 'properties': {'camera': 'ExplicitCamera', 'defaultBlendDuration': 0}},
     {'action': 'ensure_brain', 'properties': {'camera': 'ExplicitCamera', 'defaultBlendDuration': 0}}),
    ({'action': 'ensure_brain', 'properties': '{"camera":"Root/ExplicitCamera","defaultBlendDuration":0}'},
     {'action': 'ensure_brain', 'properties': '{"camera":"Root/ExplicitCamera","defaultBlendDuration":0}'}),
    ({'action': 'ensure_brain', 'properties': {'camera': '321'}},
     {'action': 'ensure_brain', 'properties': {'camera': '321'}}),
    ({'action': 'set_target', 'target': 'Fixture', 'search_method': 'by_name',
      'properties': {'follow': 'Reference', 'lookAt': 'Missing'}},
     {'action': 'set_target', 'target': 'Fixture', 'searchMethod': 'by_name',
      'properties': {'follow': 'Reference', 'lookAt': 'Missing'}}),
    ({'action': 'set_target', 'target': 'Fixture', 'properties': {'follow': None, 'lookAt': None}},
     {'action': 'set_target', 'target': 'Fixture', 'properties': {'follow': None, 'lookAt': None}}),
    ({'action': 'set_priority', 'target': '0', 'search_method': 'by_id', 'properties': {'priority': 0}},
     {'action': 'set_priority', 'target': '0', 'searchMethod': 'by_id', 'properties': {'priority': 0}}),
    ({'action': 'set_lens', 'target': 'Root/Fixture', 'search_method': 'by_path',
      'properties': {'fieldOfView': 0, 'nearClipPlane': 0, 'orthographicSize': 0}},
     {'action': 'set_lens', 'target': 'Root/Fixture', 'searchMethod': 'by_path',
      'properties': {'fieldOfView': 0, 'nearClipPlane': 0, 'orthographicSize': 0}}),
    ({'action': 'set_body', 'target': 'Fixture', 'properties': {'bodyType': 'Owned', 'enabled': False}},
     {'action': 'set_body', 'target': 'Fixture', 'properties': {'bodyType': 'Owned', 'enabled': False}}),
    ({'action': 'set_aim', 'target': 'Fixture', 'properties': {'aimType': 'Owned', 'reference': None}},
     {'action': 'set_aim', 'target': 'Fixture', 'properties': {'aimType': 'Owned', 'reference': None}}),
    ({'action': 'set_noise', 'target': 'Fixture', 'properties': {'amplitudeGain': 0, 'frequencyGain': 0}},
     {'action': 'set_noise', 'target': 'Fixture', 'properties': {'amplitudeGain': 0, 'frequencyGain': 0}}),
    ({'action': 'add_extension', 'target': 'Fixture', 'properties': {'extensionType': 'Owned', 'value': False}},
     {'action': 'add_extension', 'target': 'Fixture', 'properties': {'extensionType': 'Owned', 'value': False}}),
    ({'action': 'remove_extension', 'target': 'Fixture', 'properties': {'extensionType': 'Owned'}},
     {'action': 'remove_extension', 'target': 'Fixture', 'properties': {'extensionType': 'Owned'}}),
    ({'action': 'set_blend', 'properties': {'style': 'Cut', 'duration': 0}},
     {'action': 'set_blend', 'properties': {'style': 'Cut', 'duration': 0}}),
    ({'action': 'force_camera', 'target': 'Fixture', 'search_method': 'by_name'},
     {'action': 'force_camera', 'target': 'Fixture', 'searchMethod': 'by_name'}),
    ({'action': 'screenshot', 'include_image': False, 'max_resolution': '1', 'view_target': 0,
      'view_position': '[0,-1,2]', 'view_rotation': [0, 0, 0], 'output_folder': ' Captures '},
     {'action': 'screenshot', 'includeImage': False, 'maxResolution': 1, 'viewTarget': 0,
      'viewPosition': [0.0, -1.0, 2.0], 'viewRotation': [0.0, 0.0, 0.0], 'outputFolder': 'Captures'}),
    ({'action': 'screenshot_multiview', 'include_image': False},
     {'action': 'screenshot_multiview', 'includeImage': False}),
    ({'action': 'ensure_brain', 'properties': None}, {'action': 'ensure_brain'}),
    ({'action': 'create_camera', 'target': '', 'properties': {}},
     {'action': 'create_camera', 'target': '', 'properties': {}}),
    ({'action': 'set_priority', 'properties': '{"priority":0,"enabled":false,"reference":null}'},
     {'action': 'set_priority', 'properties': '{"priority":0,"enabled":false,"reference":null}'}),
]

async def invoke(client, payload):
    before = len(requests)
    result = await client.call_tool('manage_camera', payload, raise_on_error=False)
    sent = requests[before:]
    print('SDK_WIRE', json.dumps({'mode': client._fixture_mode, 'payload': payload,
                                  'wires': sent, 'response': result.structured_content}))
    return result, sent

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            client._fixture_mode = mode
            discovered = {tool.name: tool for tool in await client.list_tools()}
            check('manage_camera' in discovered, 'registered discovery ' + mode)
            print('FULL_SCHEMA', mode, json.dumps(discovered['manage_camera'].inputSchema, sort_keys=True))
            for payload, expected_wire in cases:
                for failed, wrapped in ((False, False), (True, True)):
                    response = {'success': not failed, 'message': 'Controlled native camera diagnostic',
                                'data': {'instanceID': 321, 'priority': 0, 'enabled': False, 'reference': None}}
                    raw = {'status': 'success', 'result': response} if wrapped else response
                    result, sent = await invoke(client, payload)
                    label = repr((mode, payload, failed, wrapped))
                    check(sent == [('Project@fixture', 'manage_camera', expected_wire)],
                          'SDK literal wire and instance ' + label)
                    check(result.structured_content == response, 'opaque native response ' + label)
            for payload in (
                {'action': 'unknown'},
                {'action': 'ping', 'properties': '[]'},
                {'action': 'ping', 'properties': 'null'},
                {'action': 'screenshot', 'max_resolution': 'bad'},
                {'action': 'screenshot', 'include_image': 'bad'},
                {'action': 'screenshot', 'capture_source': 'scene_view', 'camera': 'Fixture'},
            ):
                result, sent = await invoke(client, payload)
                check(result.structured_content.get('success') is False and not sent,
                      'local error before transport ' + mode + repr(payload))
            for field in ('screenshot_super_size', 'max_resolution', 'orbit_angles'):
                for value in (True, False, 1.0, 1.9):
                    result, sent = await invoke(client, {'action': 'screenshot', field: value})
                    check(result.is_error and not sent,
                          'integer scalar rejected before transport ' + mode + field + repr(value))
    for field in ('screenshot_super_size', 'max_resolution', 'orbit_angles'):
        for value in (True, False):
            before = len(requests)
            result = await manage_camera(None, action='screenshot', **{field: value})
            check(result['success'] is False and len(requests) == before,
                  'direct bool guard rejects before transport ' + field + repr(value))
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
"""


def test_camera_actual_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_camera_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)


def test_camera_image_metadata_remains_structured_at_real_sdk_boundary(tmp_path):
    _run(
        r"""
import copy
import json
import anyio
from fastmcp import Client, FastMCP
from services.tools import register_all_tools
from core.config import config
from transport.plugin_hub import PluginHub

# Given: native capture replies and the actual registered tool pipeline.
config.transport_mode = 'http'
config.http_remote_hosted = False
reply = {}
async def controlled_send(instance, command, params, **kwargs):
    return copy.deepcopy(reply)
PluginHub.send_command_for_instance = staticmethod(controlled_send)
server = FastMCP('camera-image-metadata')
register_all_tools(server)
image = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8'
         '/x8AAwMCAO+a5XkAAAAASUVORK5CYII=')
cases = [
    ({'action': 'screenshot_multiview'},
     {'imageBase64': image, 'imageWidth': 1, 'imageHeight': 1,
      'outputFolder': 'Captures', 'shots': [{'angle': 'Front'}]}),
    ({'action': 'screenshot', 'view_position': [0, 0, 0], 'include_image': True},
     {'imageBase64': image, 'imageWidth': 1, 'imageHeight': 1,
      'path': 'Captures/positioned.png', 'position': [0, 0, 0]}),
    ({'action': 'screenshot', 'batch': 'surround', 'include_image': True},
     {'sceneCenter': [0, 0, 0], 'sceneRadius': 0,
      'screenshots': [{'angle': 'Front', 'path': 'Captures/front.png',
                       'imageBase64': image}]}),
]

async def main():
    global reply
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            for payload, data in cases:
                reply = {'success': True, 'message': 'Captured', 'data': data}
                expected = copy.deepcopy(reply)
                expected['data'].pop('imageBase64', None)
                for shot in expected['data'].get('screenshots', []):
                    shot.pop('imageBase64', None)
                # When: the public SDK requests an inline screenshot.
                result = await client.call_tool('manage_camera', payload)
                # Then: metadata is structured and image bytes occur only in image blocks.
                assert result.structured_content == expected, (mode, result.structured_content)
                assert json.loads(result.content[0].text) == expected
                images = [block for block in result.content if block.type == 'image']
                assert len(images) == 1 and images[0].data == image
                assert images[0].mime_type == 'image/png'
            for response in (
                {'success': True, 'message': 'Saved', 'data': {
                    'path': 'Captures/plain.png', 'imageWidth': 1, 'imageHeight': 1,
                }},
                {'success': False, 'error': 'capture_failed', 'data': {
                    'imageBase64': image, 'modified': False,
                }},
            ):
                reply = response
                result = await client.call_tool('manage_camera', {'action': 'screenshot'})
                assert result.structured_content == response
                assert all(block.type == 'text' for block in result.content)
anyio.run(main)
""",
        tmp_path,
    )
