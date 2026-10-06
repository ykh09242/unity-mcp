"""Fresh SDK/CLI UI and texture contracts with controlled transport only."""

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
from cli.utils import connection

requests, failures = [], []
checks = 0
step = 0
fail_at = None
wrapped = False
texture_reply = {}
client_type = httpx.AsyncClient
initial = {'success': True, 'message': 'Created',
           'data': {'instanceID': 101, 'count': 0, 'enabled': False, 'reference': None}}

def respond(request):
    global step
    value = json.loads(request.content)
    requests.append(value)
    step += 1
    if texture_reply:
        response = copy.deepcopy(texture_reply)
    elif step == fail_at:
        response = {'success': False, 'error': 'Controlled native step rejected',
                    'data': {'count': 0, 'modified': False, 'reference': None}}
    elif value['type'] == 'manage_gameobject':
        response = copy.deepcopy(initial)
        if value['params']['name'].endswith('_Label'):
            response['data']['instanceID'] = 202
    else:
        response = {'success': True, 'data': {'modified': False, 'count': 0}}
    return httpx.Response(200, json={'status': 'success', 'result': response} if wrapped else response)

connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
connection._auth_headers = lambda config: {}
runner = CliRunner()

def check(condition, label):
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print('FAIL', label)

ui_cases = [
    (['create-canvas', 'Fixture', '--render-mode', 'ScreenSpaceOverlay'], [
        ('manage_gameobject', {'action': 'create', 'name': 'Fixture'}),
        *[('manage_components', {'action': 'add', 'target': 101, 'searchMethod': 'by_id',
                                'componentType': component})
          for component in ('Canvas', 'CanvasScaler', 'GraphicRaycaster')],
        ('manage_components', {'action': 'set_property', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'Canvas', 'property': 'renderMode', 'value': 0}),
    ]),
    (['create-text', 'Fixture', '--parent', 'Parent', '--text', '', '--position', '0', '-2'], [
        ('manage_gameobject', {'action': 'create', 'name': 'Fixture', 'parent': 'Parent',
                              'position': [0.0, -2.0]}),
        ('manage_components', {'action': 'add', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'TextMeshProUGUI'}),
        ('manage_components', {'action': 'set_property', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'RectTransform', 'property': 'anchoredPosition',
                               'value': [0.0, -2.0]}),
        ('manage_components', {'action': 'set_property', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'TextMeshProUGUI', 'property': 'text', 'value': ''}),
    ]),
    (['create-text', 'Fixture', '--parent', 'Parent'], [
        ('manage_gameobject', {'action': 'create', 'name': 'Fixture', 'parent': 'Parent',
                              'position': [0, 0]}),
        ('manage_components', {'action': 'add', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'TextMeshProUGUI'}),
        ('manage_components', {'action': 'set_property', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'TextMeshProUGUI', 'property': 'text', 'value': 'New Text'}),
    ]),
    (['create-button', 'Fixture', '--parent', 'Parent', '--text', '😀'], [
        ('manage_gameobject', {'action': 'create', 'name': 'Fixture', 'parent': 'Parent'}),
        ('manage_components', {'action': 'add', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'Image'}),
        ('manage_components', {'action': 'add', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'Button'}),
        ('manage_gameobject', {'action': 'create', 'name': 'Fixture_Label', 'parent': 101}),
        ('manage_components', {'action': 'add', 'target': 202, 'searchMethod': 'by_id',
                               'componentType': 'TextMeshProUGUI'}),
        ('manage_components', {'action': 'set_property', 'target': 202, 'searchMethod': 'by_id',
                               'componentType': 'TextMeshProUGUI', 'property': 'text', 'value': '😀'}),
    ]),
    (['create-image', 'Fixture', '--parent', 'Parent', '--sprite', 'Assets/Sprite.png'], [
        ('manage_gameobject', {'action': 'create', 'name': 'Fixture', 'parent': 'Parent'}),
        ('manage_components', {'action': 'add', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'Image'}),
        ('manage_components', {'action': 'set_property', 'target': 101, 'searchMethod': 'by_id',
                               'componentType': 'Image', 'property': 'sprite', 'value': 'Assets/Sprite.png'}),
    ]),
]
for args, wires in ui_cases:
    for wrapped in (False, True):
        for fail_at in (None, *range(1, len(wires) + 1)):
            step = 0
            before = len(requests)
            result = runner.invoke(cli, ['--format', 'json', '--instance', 'Project@fixture',
                                         'ui', *args])
            sent = requests[before:]
            expected_count = fail_at if fail_at else len(wires)
            label = repr((args[0], wrapped, fail_at))
            check(len(sent) == expected_count, 'stop on exact failing step ' + label)
            check([(row['type'], row['params']) for row in sent] == wires[:expected_count],
                  'created identity and literal payload ' + label)
            check(all(row['unity_instance'] == 'Project@fixture' for row in sent),
                  'CLI instance ' + label)
            check(result.exit_code == (1 if fail_at else 0), 'UI exit ' + label)
            try:
                document = json.loads(result.output)
            except ValueError:
                document = None
            expected = {'success': False, 'error': 'Controlled native step rejected',
                        'data': {'count': 0, 'modified': False, 'reference': None}} if fail_at else initial
            check(document == expected, 'UI single JSON document ' + label)
            print('CLI_WIRE', json.dumps({'args': args, 'fail_at': fail_at, 'requests': sent}))
        fail_at = None
        step = 0
        result = runner.invoke(cli, ['--format', 'text', 'ui', *args])
        check(result.exit_code == 0 and 'Created ' in result.output,
              'UI text notice ' + args[0])

wrapped = False
fail_at = None
for value in (None, 0, True, '', 'bad'):
    initial['data']['instanceID'] = value
    step = 0
    before = len(requests)
    result = runner.invoke(cli, ['--format', 'json', 'ui', 'create-text', 'Fixture', '--parent', 'Parent'])
    check(result.exit_code == 1 and len(requests) == before + 1,
          'missing ID stops followups ' + repr(value))
    check('no valid instanceID' in result.output, 'missing ID diagnostic ' + repr(value))
initial['data']['instanceID'] = 101

texture_cases = [
    (['create', 'Assets/Fixture.png', '--width', '1', '--height', '1', '--color', '#00000000'],
     {'action': 'create', 'path': 'Assets/Fixture.png', 'width': 1, 'height': 1,
      'fillColor': [0, 0, 0, 0]}),
    (['create', 'Assets/Fixture.png', '--image-path', 'Assets/Source.png'],
     {'action': 'create', 'path': 'Assets/Fixture.png', 'width': 64, 'height': 64,
      'imagePath': 'Assets/Source.png'}),
    (['sprite', 'Assets/Fixture.png', '--width', '1', '--height', '1', '--color', '[0,0,0,0]',
      '--ppu', '0', '--pivot', '[0,0]'],
     {'action': 'create_sprite', 'path': 'Assets/Fixture.png', 'width': 1, 'height': 1,
      'fillColor': [0, 0, 0, 0], 'spriteSettings': {'pixelsPerUnit': 0.0, 'pivot': [0, 0]}}),
    (['modify', 'Assets/Fixture.png', '--set-pixels',
      '{"x":-1,"y":0,"width":2,"height":1,"color":[1.0,0,0,0]}', '--no-readable'],
     {'action': 'modify', 'path': 'Assets/Fixture.png', 'importSettings': {'isReadable': False},
      'setPixels': {'x': -1, 'y': 0, 'width': 2, 'height': 1, 'color': [255, 0, 0, 0]}}),
    (['set-import-settings', 'Assets/Fixture.png', '--linear', '--no-mipmaps', '--no-readable'],
     {'action': 'set_import_settings', 'path': 'Assets/Fixture.png',
      'importSettings': {'sRGBTexture': False, 'mipmapEnabled': False, 'isReadable': False}}),
    (['delete', 'Assets/Fixture.png', '--force'], {'action': 'delete', 'path': 'Assets/Fixture.png'}),
    (['create', 'Assets/Fixture.png', '--width', '2', '--height', '2', '--import-settings',
      '{"textureType":"Sprite","anisoLevel":"bad"}'],
     {'action': 'create', 'path': 'Assets/Fixture.png', 'width': 2, 'height': 2,
      'fillColor': [255, 255, 255, 255],
      'importSettings': {'textureType': 'Sprite', 'anisoLevel': 'bad'}}),
]
for args, wire in texture_cases:
    for failed in (False, True):
        for wrapped in (False, True):
            texture_reply = {'success': not failed, 'message': 'Controlled native diagnostic',
                             'data': {'count': 0, 'modified': False, 'reference': None}}
            before = len(requests)
            result = runner.invoke(cli, ['--format', 'json', 'texture', *args])
            label = repr((args, failed, wrapped))
            check(result.exit_code == (1 if failed else 0), 'texture exit ' + label)
            check(len(requests) == before + 1 and requests[-1]['params'] == wire,
                  'texture unchanged payload ' + label)
            try:
                document = json.loads(result.output)
            except ValueError:
                document = None
            check(document == texture_reply, 'texture one document ' + label)
            print('CLI_WIRE', json.dumps({'args': args, 'requests': requests[before:]}))
for group, commands in (('ui', [[], ['create-canvas'], ['create-text'], ['create-button'], ['create-image']]),
                        ('texture', [[], ['create'], ['sprite'], ['modify'], ['delete'], ['set-import-settings']])):
    for command in commands:
        result = runner.invoke(cli, [group, *command, '--help'])
        check(result.exit_code == 0, 'help ' + repr((group, command)))
        print('FULL_HELP', json.dumps({'group': group, 'command': command, 'text': result.output}))
print('CLI_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
assert not failures, failures
'''


SDK_PROGRAM = r'''
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

async def controlled_send(instance, command, params, **kwargs):
    requests.append((instance, command, copy.deepcopy(params)))
    if command == 'get_editor_state':
        return {'success': True, 'data': {'compilation': {'is_compiling': False},
                                         'assets': {'external_changes_dirty': False}}}
    if command == 'get_project_info':
        return {'success': True, 'data': {}}
    return copy.deepcopy(raw)

PluginHub.send_command_for_instance = staticmethod(controlled_send)

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

server = FastMCP('ui-texture-public-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
server.enable(tags={'group:ui', 'group:vfx'})
ui_cases = [
    ({'action': 'ping'}, {'action': 'ping'}),
    ({'action': 'create', 'path': r'assets\UI\Fixture.uss', 'contents': ''},
     {'action': 'create', 'path': 'Assets/UI/Fixture.uss', 'contentsEncoded': True, 'encodedContents': ''}),
    ({'action': 'update', 'path': 'Assets/UI/Fixture.uxml', 'contents': '<ui:UXML>😀</ui:UXML>'},
     {'action': 'update', 'path': 'Assets/UI/Fixture.uxml', 'contentsEncoded': True,
      'encodedContents': base64.b64encode('<ui:UXML>😀</ui:UXML>'.encode()).decode()}),
    ({'action': 'read', 'path': 'Assets/UI/Fixture.uss'},
     {'action': 'read', 'path': 'Assets/UI/Fixture.uss'}),
    ({'action': 'delete', 'path': 'Assets/UI/Fixture.uss'},
     {'action': 'delete', 'path': 'Assets/UI/Fixture.uss'}),
    ({'action': 'attach_ui_document', 'target': 'Fixture', 'source_asset': 'Assets/UI/Fixture.uxml',
      'panel_settings': None, 'sort_order': 0},
     {'action': 'attach_ui_document', 'target': 'Fixture', 'sourceAsset': 'Assets/UI/Fixture.uxml',
      'sortOrder': 0}),
    ({'action': 'detach_ui_document', 'target': 'Root/Fixture'},
     {'action': 'detach_ui_document', 'target': 'Root/Fixture'}),
    ({'action': 'create_panel_settings', 'path': 'Assets/UI/Fixture.asset',
      'scale_mode': 'ScaleWithScreenSize', 'reference_resolution': {'width': 0, 'height': 0},
      'settings': {'sortingOrder': 0, 'clearColor': False, 'referenceResolution': {'width': 1920, 'height': 1080}}},
     {'action': 'create_panel_settings', 'path': 'Assets/UI/Fixture.asset',
      'scaleMode': 'ScaleWithScreenSize', 'referenceResolution': {'width': 0, 'height': 0},
      'settings': {'sortingOrder': 0, 'clearColor': False, 'referenceResolution': {'width': 1920, 'height': 1080}}}),
    ({'action': 'update_panel_settings', 'path': 'Assets/UI/Fixture.asset',
      'settings': {'sortingOrder': 0, 'clearColor': False,
                   'referenceResolution': {'width': 'bad', 'height': 1080}}},
     {'action': 'update_panel_settings', 'path': 'Assets/UI/Fixture.asset',
      'settings': {'sortingOrder': 0, 'clearColor': False,
                   'referenceResolution': {'width': 'bad', 'height': 1080}}}),
    ({'action': 'get_visual_tree', 'target': 'Fixture', 'max_depth': 0},
     {'action': 'get_visual_tree', 'target': 'Fixture', 'maxDepth': 0}),
    ({'action': 'render_ui', 'path': 'Assets/UI/Fixture.uxml', 'width': 1, 'height': 1,
      'include_image': False, 'max_resolution': 0, 'screenshot_file_name': '', 'output_folder': ' Captures '},
     {'action': 'render_ui', 'path': 'Assets/UI/Fixture.uxml', 'width': 1, 'height': 1,
      'include_image': False, 'max_resolution': 0, 'file_name': '', 'output_folder': 'Captures'}),
    ({'action': 'link_stylesheet', 'path': 'Assets/UI/Fixture.uxml', 'stylesheet': 'Assets/UI/Fixture.uss'},
     {'action': 'link_stylesheet', 'path': 'Assets/UI/Fixture.uxml', 'stylesheet': 'Assets/UI/Fixture.uss'}),
    ({'action': 'list', 'filter_type': '', 'page_size': 1, 'page_number': 1},
     {'action': 'list', 'filterType': '', 'pageSize': 1, 'pageNumber': 1}),
    ({'action': 'modify_visual_element', 'target': 'Fixture', 'element_name': 'Owned',
      'text': '', 'add_classes': [], 'remove_classes': [], 'toggle_classes': [],
      'style': {'opacity': 0}, 'enabled': False, 'visible': False, 'tooltip': ''},
     {'action': 'modify_visual_element', 'target': 'Fixture', 'elementName': 'Owned',
      'text': '', 'addClasses': [], 'removeClasses': [], 'toggleClasses': [],
      'style': {'opacity': 0}, 'enabled': False, 'visible': 'false', 'tooltip': ''}),
]
for action in ('create_panel_settings', 'update_panel_settings'):
    ui_cases.append((
        {'action': action, 'path': 'Assets/UI/Fixture.asset', 'settings': {
            'sortingOrder': 5, 'referenceResolution': {'width': 'bad', 'height': 1080}}},
        {'action': action, 'path': 'Assets/UI/Fixture.asset', 'settings': {
            'sortingOrder': 5, 'referenceResolution': {'width': 'bad', 'height': 1080}}},
    ))
    ui_cases.append(({'action': action, 'path': 'Assets/UI/Fixture.asset', 'settings': None},
                     {'action': action, 'path': 'Assets/UI/Fixture.asset'}))
texture_cases = [
    ({'action': 'create', 'path': 'Assets/Fixture.png', 'as_sprite': False},
     {'action': 'create', 'path': 'Assets/Fixture.png', 'width': 64, 'height': 64,
      'fillColor': [255, 255, 255, 255]}),
    ({'action': 'create', 'path': 'Assets/Fixture.png', 'width': 1, 'height': 1,
      'fill_color': [0, 0, 0, 0]},
     {'action': 'create', 'path': 'Assets/Fixture.png', 'width': 1, 'height': 1,
      'fillColor': [0, 0, 0, 0]}),
    ({'action': 'create', 'path': 'Assets/Fixture.png', 'image_path': 'Assets/Source.png'},
     {'action': 'create', 'path': 'Assets/Fixture.png', 'imagePath': 'Assets/Source.png'}),
    ({'action': 'create_sprite', 'path': 'Assets/Fixture.png', 'width': 1, 'height': 1,
      'fill_color': '#00000000', 'as_sprite': {'pivot': [0, 0], 'pixels_per_unit': 0}},
     {'action': 'create_sprite', 'path': 'Assets/Fixture.png', 'width': 1, 'height': 1,
      'fillColor': [0, 0, 0, 0], 'spriteSettings': {'pivot': [0.0, 0.0], 'pixelsPerUnit': 0.0}}),
    ({'action': 'modify', 'path': 'Assets/Fixture.png',
      'set_pixels': {'x': -1, 'y': 0, 'width': 2, 'height': 1, 'color': [1.0, 0.0, 0.0, 0.0]},
      'import_settings': {'srgb': False, 'readable': False, 'generate_mipmaps': False,
                          'aniso_level': 0, 'compression_quality': 0}},
     {'action': 'modify', 'path': 'Assets/Fixture.png', 'width': 64, 'height': 64,
      'setPixels': {'x': -1, 'y': 0, 'width': 2, 'height': 1, 'color': [255, 0, 0, 0]},
      'importSettings': {'sRGBTexture': False, 'isReadable': False, 'mipmapEnabled': False,
                         'anisoLevel': 0, 'compressionQuality': 0}}),
    ({'action': 'set_import_settings', 'path': 'Assets/Fixture.png',
      'import_settings': {'texture_type': 'sprite', 'sprite_pixels_per_unit': 0,
                          'sprite_pivot': [0, 0], 'sprite_extrude': 0}},
     {'action': 'set_import_settings', 'path': 'Assets/Fixture.png', 'width': 64, 'height': 64,
      'importSettings': {'textureType': 'Sprite', 'spritePixelsPerUnit': 0.0,
                         'spritePivot': [0.0, 0.0], 'spriteExtrude': 0}}),
    ({'action': 'delete', 'path': 'Assets/Fixture.png', 'as_sprite': None, 'import_settings': None},
     {'action': 'delete', 'path': 'Assets/Fixture.png', 'width': 64, 'height': 64}),
    ({'action': 'apply_pattern', 'path': 'Assets/Fixture.png', 'pattern': 'checkerboard',
      'palette': [[0, 0, 0, 0], [255, 255, 255, 255]], 'pattern_size': 1},
     {'action': 'apply_pattern', 'path': 'Assets/Fixture.png', 'width': 64, 'height': 64,
      'pattern': 'checkerboard', 'palette': [[0, 0, 0, 0], [255, 255, 255, 255]], 'patternSize': 1}),
    ({'action': 'apply_gradient', 'path': 'Assets/Fixture.png', 'gradient_type': 'linear',
      'gradient_angle': 0, 'palette': [[0, 0, 0, 0], [255, 255, 255, 255]]},
     {'action': 'apply_gradient', 'path': 'Assets/Fixture.png', 'width': 64, 'height': 64,
      'gradientType': 'linear', 'gradientAngle': 0.0,
      'palette': [[0, 0, 0, 0], [255, 255, 255, 255]]}),
    ({'action': 'apply_noise', 'path': 'Assets/Fixture.png', 'noise_scale': 0, 'octaves': 1},
     {'action': 'apply_noise', 'path': 'Assets/Fixture.png', 'width': 64, 'height': 64,
      'noiseScale': 0.0, 'octaves': 1}),
    ({'action': 'create', 'path': 'Assets/Fixture.png', 'width': 1, 'height': 1, 'pixels': 'AAAAAA=='},
     {'action': 'create', 'path': 'Assets/Fixture.png', 'width': 1, 'height': 1, 'pixels': 'base64:AAAAAA=='}),
]

async def invoke(client, name, payload):
    before = len(requests)
    result = await client.call_tool(name, payload, raise_on_error=False)
    document = result.structured_content
    wires = [row for row in requests[before:] if row[1] in ('manage_ui', 'manage_texture')]
    print('SDK_WIRE', json.dumps({'mode': client._fixture_mode, 'tool': name,
                                  'payload': payload, 'wires': wires, 'response': document}))
    return document, wires, requests[before:]

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            client._fixture_mode = mode
            discovered = {tool.name: tool for tool in await client.list_tools()}
            for name, cases in (('manage_ui', ui_cases), ('manage_texture', texture_cases)):
                check(name in discovered, 'actual registered discovery ' + mode + name)
                print('FULL_SCHEMA', mode, name, json.dumps(discovered[name].inputSchema, sort_keys=True))
                for payload, expected_wire in cases:
                    for failed in (False, True):
                        for wrapped in (False, True):
                            response = {'success': not failed, 'message': 'Controlled native diagnostic',
                                        'data': {'count': 0, 'changed': False, 'reference': None}}
                            raw = {'status': 'success', 'result': response} if wrapped else response
                            actual, wires, calls = await invoke(client, name, payload)
                            label = repr((mode, name, payload, failed, wrapped))
                            check(len(wires) == 1 and wires[0][2] == expected_wire,
                                  'literal SDK wire ' + label)
                            check(wires and wires[0][0] == 'Project@fixture', 'instance ' + label)
                            expected = copy.deepcopy(response)
                            if name == 'manage_texture':
                                expected['_debug_params'] = expected_wire
                            check(actual == expected, 'falsey error/response preservation ' + label)
                            if name == 'manage_texture':
                                check(any(row[1] == 'get_editor_state' for row in calls),
                                      'actual preflight retained ' + label)
            for encoded in ('', base64.b64encode('😀'.encode()).decode()):
                raw = {'success': True, 'data': {'contentsEncoded': True, 'encodedContents': encoded}}
                actual, wires, calls = await invoke(client, 'manage_ui', {
                    'action': 'read', 'path': 'Assets/UI/Fixture.uss',
                })
                check(actual['data'] == {'contents': base64.b64decode(encoded).decode()},
                      'UI empty/Unicode decode ' + mode)
            for name, payload in (
                ('manage_ui', {'action': 'render_ui', 'width': 0}),
                ('manage_ui', {'action': 'render_ui', 'height': 0}),
                ('manage_ui', {'action': 'render_ui', 'max_resolution': -1}),
                ('manage_ui', {'action': 'list', 'page_size': 0}),
                ('manage_ui', {'action': 'list', 'page_number': 0}),
                ('manage_ui', {'action': 'create', 'path': 'Assets/../Escape.uxml', 'contents': ''}),
                ('manage_ui', {'action': 'link_stylesheet', 'path': 'Assets/UI/Fixture.uxml',
                               'stylesheet': 'Assets/../Escape.uss'}),
                ('manage_texture', {'action': 'create', 'width': 0}),
                ('manage_texture', {'action': 'modify', 'set_pixels': {'width': 0, 'height': 1, 'pixels': []}}),
                ('manage_texture', {'action': 'create', 'import_settings': {'texture_type': {}}}),
                ('manage_texture', {'action': 'create', 'image_path': 'Assets/Source.png', 'fill_color': '#FFFFFF'}),
            ):
                actual, wires, calls = await invoke(client, name, payload)
                check(actual and actual.get('success') is False and not calls,
                      'local validation before preflight/transport ' + mode + repr(payload))
    print('SDK_SUMMARY', json.dumps({'checks': checks, 'failures': failures, 'requests': len(requests)}))
    assert not failures, failures

anyio.run(main)
'''


def test_ui_texture_actual_cli_contracts(tmp_path):
    assert "CLI_SUMMARY" in _run(CLI_PROGRAM, tmp_path)


def test_ui_texture_registered_sdk_contracts(tmp_path):
    assert "SDK_SUMMARY" in _run(SDK_PROGRAM, tmp_path)
