"""Fresh public scene/prefab boundaries; engine outcomes are opaque fixtures."""

import os
import subprocess
import sys
import textwrap


def _run(program, tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(program)],
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
raw = {}
client_type = httpx.AsyncClient

def respond(request):
    requests.append(json.loads(request.content))
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

scene_cases = [
    (['hierarchy', '--limit', '0', '--cursor', '0', '--max-depth', '0',
      '--parent', '0', '--include-transform'],
     {'action': 'get_hierarchy', 'pageSize': 0, 'cursor': 0, 'maxDepth': 0,
      'parent': '0', 'includeTransform': True}),
    (['active'], {'action': 'get_active'}),
    (['load', '0', '--by-index'], {'action': 'load', 'buildIndex': 0}),
    (['load', 'Same'], {'action': 'load', 'name': 'Same'}),
    (['load', 'Assets/Second/Same.unity'],
     {'action': 'load', 'path': 'Assets/Second/Same.unity'}),
    (['save'], {'action': 'save'}),
    (['save', '--path', 'Assets/Scenes/NewScene.unity'],
     {'action': 'save', 'path': 'Assets/Scenes/NewScene.unity'}),
    (['create', 'Fixture'], {'action': 'create', 'name': 'Fixture'}),
    (['create', 'Fixture', '--path', 'Assets/Scenes', '--template', '2d_basic'],
     {'action': 'create', 'name': 'Fixture', 'path': 'Assets/Scenes', 'template': '2d_basic'}),
    (['build-settings'], {'action': 'get_build_settings'}),
    (['loaded'], {'action': 'get_loaded_scenes'}),
    (['open-additive', 'Assets/Second/Same.unity'],
     {'action': 'load', 'path': 'Assets/Second/Same.unity', 'additive': True}),
    (['close', 'Same'], {'action': 'close_scene', 'sceneName': 'Same'}),
    (['close', 'Same', '--remove'],
     {'action': 'close_scene', 'sceneName': 'Same', 'removeScene': True}),
    (['set-active', 'Same'], {'action': 'set_active_scene', 'sceneName': 'Same'}),
    (['move-to', '0', 'Same'],
     {'action': 'move_to_scene', 'target': '0', 'sceneName': 'Same'}),
    (['validate'], {'action': 'validate'}),
    (['validate', '--repair'], {'action': 'validate', 'autoRepair': True}),
]
prefab_cases = [
    (['open', 'Assets/Fixture.prefab'],
     {'action': 'open_prefab_stage', 'prefabPath': 'Assets/Fixture.prefab'}),
    (['close'], {'action': 'close_prefab_stage'}),
    (['close', '--save'], {'action': 'close_prefab_stage', 'saveBeforeClose': True}),
    (['save'], {'action': 'save_prefab_stage'}),
    (['info', 'Assets/Fixture.prefab', '--compact'],
     {'action': 'get_info', 'prefabPath': 'Assets/Fixture.prefab'}),
    (['hierarchy', 'Assets/Fixture.prefab', '--compact', '--show-prefab-info'],
     {'action': 'get_hierarchy', 'prefabPath': 'Assets/Fixture.prefab'}),
    (['create', 'Root/Fixture', 'Assets/Fixture.prefab'],
     {'action': 'create_from_gameobject', 'target': 'Root/Fixture', 'prefabPath': 'Assets/Fixture.prefab'}),
    (['create', 'Root/Fixture', 'Assets/Fixture.prefab', '--overwrite',
      '--include-inactive', '--unlink-if-instance'],
     {'action': 'create_from_gameobject', 'target': 'Root/Fixture', 'prefabPath': 'Assets/Fixture.prefab',
      'allowOverwrite': True, 'searchInactive': True, 'unlinkIfInstance': True}),
    (['modify', 'Assets/Fixture.prefab', '--target', 'Child', '--position', '0,-1,2',
      '--rotation', '0,0,0', '--scale', '0,0,0', '--inactive', '--parent', 'Root',
      '--add-component', 'Rigidbody', '--remove-component', 'BoxCollider',
      '--set-property', 'Rigidbody.mass=0', '--set-property', 'Rigidbody.useGravity=false',
      '--set-property', 'Owned.label=a=b', '--delete-child', 'Old', '--delete-child', 'Root/Old',
      '--create-child', '{"name":"New","position":[0,0,0],"set_active":false}'],
     {'action': 'modify_contents', 'prefabPath': 'Assets/Fixture.prefab', 'target': 'Child',
      'position': [0, -1, 2], 'rotation': [0, 0, 0], 'scale': [0, 0, 0],
      'setActive': False, 'parent': 'Root', 'componentsToAdd': ['Rigidbody'],
      'componentsToRemove': ['BoxCollider'], 'componentProperties': {
          'Rigidbody': {'mass': 0, 'useGravity': False}, 'Owned': {'label': 'a=b'}},
      'deleteChild': ['Old', 'Root/Old'],
      'createChild': {'name': 'New', 'position': [0, 0, 0], 'set_active': False}}),
]
for group, cases in (('scene', scene_cases), ('prefab', prefab_cases)):
    for args, wire in cases:
        for failed in (False, True):
            for wrapped in (False, True):
                expected = {
                    'success': not failed,
                    'message': 'Controlled native diagnostic' if failed else 'Done',
                    'data': {'totalIssues': 0, 'repaired': 0, 'cursor': 0,
                             'nextCursor': None, 'items': [], 'isVariant': False},
                }
                raw = {'status': 'success', 'result': expected} if wrapped else expected
                before = len(requests)
                result = runner.invoke(
                    cli, ['--format', 'json', '--instance', 'Project@fixture', group, *args]
                )
                label = group + repr(args) + str(failed) + str(wrapped)
                check(result.exit_code == (1 if failed else 0), 'exit ' + label)
                try:
                    document = json.loads(result.stdout)
                except ValueError:
                    document = None
                check(document == expected, 'single JSON/diagnostic/falsey ' + label)
                check(requests[before:] == [{
                    'type': 'manage_scene' if group == 'scene' else 'manage_prefabs',
                    'params': wire, 'unity_instance': 'Project@fixture',
                }], 'exact central HTTP ' + label)
                if not failed and not wrapped:
                    print('DOMAIN_WIRE', json.dumps({
                        'surface': 'cli', 'group': group, 'input': args,
                        'requests': requests[before:],
                    }))

raw = {'success': True, 'message': 'Done', 'data': {'totalIssues': 0, 'repaired': 0}}
for args, notice in (
    (['load', '0', '--by-index'], 'Loaded scene'),
    (['save'], 'Scene saved'),
    (['create', 'Fixture'], 'Created scene'),
    (['open-additive', 'Assets/Fixture.unity'], 'Opened additively'),
    (['close', 'Same'], 'Closed scene'),
    (['set-active', 'Same'], 'Set active'),
    (['move-to', 'Owned', 'Same'], 'Moved'),
    (['validate'], 'Scene is clean'),
):
    result = runner.invoke(cli, ['scene', *args])
    check(result.exit_code == 0 and notice in result.stdout, 'text notice ' + notice)
for total, repaired, notice in ((2, 1, 'repaired 1'), (2, 0, 'none repaired')):
    raw['data'] = {'totalIssues': total, 'repaired': repaired}
    result = runner.invoke(cli, ['scene', 'validate', '--repair'])
    check(result.exit_code == 0 and notice in result.stdout, 'text validation ' + notice)

for group, args in (
    ('scene', ['load', 'bad', '--by-index']),
    ('scene', ['create', 'Fixture', '--template', 'bad']),
    ('scene', ['hierarchy', '--cursor', 'bad']),
    ('prefab', ['modify', 'Assets/Fixture.prefab', '--position', '1,2']),
    ('prefab', ['modify', 'Assets/Fixture.prefab', '--set-property', 'bad']),
    ('prefab', ['modify', 'Assets/Fixture.prefab', '--create-child', '[]']),
    ('prefab', ['modify', 'Assets/Fixture.prefab', '--create-child', '{']),
):
    before = len(requests)
    result = runner.invoke(cli, [group, *args])
    check(result.exit_code != 0 and len(requests) == before, 'pretransport invalid ' + repr(args))
for group, commands in (
    ('scene', ('hierarchy', 'active', 'load', 'save', 'create', 'build-settings', 'loaded',
               'open-additive', 'close', 'set-active', 'move-to', 'validate')),
    ('prefab', ('open', 'close', 'save', 'info', 'hierarchy', 'create', 'modify')),
):
    for command in commands:
        result = runner.invoke(cli, [group, command, '--help'])
        check(result.exit_code == 0, 'help ' + group + command)
        print('FULL_HELP', group + '.' + command, json.dumps(result.stdout))
print(f'fresh scene/prefab CLI checks={checks} failures={len(failures)} requests={len(requests)}')
assert not failures, failures
"""


SDK_PROGRAM = r"""
import copy
import json
import anyio
from fastmcp import FastMCP, Client
from fastmcp.server.middleware import Middleware
from services.tools import register_all_tools
from services.tools.manage_scene import manage_scene
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
        return {'success': True, 'data': {'compilation': {'is_compiling': False}}}
    if command == 'get_project_info':
        return {'success': True, 'data': {}}
    return copy.deepcopy(raw)

PluginHub.send_command_for_instance = staticmethod(controlled_send)

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

class DirectContext:
    async def get_state(self, key):
        return 'Project@fixture' if key == 'unity_instance' else None

server = FastMCP('scene-prefab-contracts')
server.add_middleware(FixtureState())
register_all_tools(server)
scene_cases = [
    ({'action': 'load', 'name': 'Same'}, {'action': 'load', 'name': 'Same'}),
    ({'action': 'load', 'name': 'Same', 'additive': False},
     {'action': 'load', 'name': 'Same', 'additive': False}),
    ({'action': 'load', 'path': 'Assets/Second/Same.unity', 'additive': 'true'},
     {'action': 'load', 'path': 'Assets/Second/Same.unity', 'additive': True}),
    ({'action': 'create', 'name': 'Fixture', 'path': 'Assets/Scenes', 'template': '2d_basic'},
     {'action': 'create', 'name': 'Fixture', 'path': 'Assets/Scenes', 'template': '2d_basic'}),
    ({'action': 'save', 'path': 'Assets/Scenes/NewScene.unity'},
     {'action': 'save', 'path': 'Assets/Scenes/NewScene.unity'}),
    ({'action': 'save', 'name': '', 'path': ''}, {'action': 'save'}),
    ({'action': 'get_hierarchy', 'parent': 0, 'page_size': '0', 'cursor': 0, 'max_depth': 0,
      'max_nodes': 0, 'max_children_per_node': 0, 'include_transform': False},
     {'action': 'get_hierarchy', 'parent': 0, 'pageSize': 0, 'cursor': 0, 'maxDepth': 0,
      'maxNodes': 0, 'maxChildrenPerNode': 0, 'includeTransform': False}),
    ({'action': 'get_hierarchy', 'cursor': None, 'parent': '', 'include_transform': 'false'},
     {'action': 'get_hierarchy', 'parent': '', 'includeTransform': False}),
    ({'action': 'scene_view_frame', 'scene_view_target': 0},
     {'action': 'scene_view_frame', 'sceneViewTarget': 0}),
    ({'action': 'move_to_scene', 'target': 0, 'scene_name': 'Same',
      'scene_path': 'Assets/Second/Same.unity'},
     {'action': 'move_to_scene', 'target': 0, 'sceneName': 'Same',
      'scenePath': 'Assets/Second/Same.unity'}),
    ({'action': 'close_scene', 'scene_name': 'Same', 'scene_path': 'Assets/Second/Same.unity',
      'remove_scene': False},
     {'action': 'close_scene', 'sceneName': 'Same', 'scenePath': 'Assets/Second/Same.unity',
      'removeScene': False}),
    ({'action': 'set_active_scene', 'scene_name': 'Same', 'scene_path': 'Assets/Second/Same.unity'},
     {'action': 'set_active_scene', 'sceneName': 'Same', 'scenePath': 'Assets/Second/Same.unity'}),
    ({'action': 'close_scene', 'name': 'Same', 'scene_name': None, 'scene_path': ''},
     {'action': 'close_scene', 'name': 'Same', 'scenePath': ''}),
    ({'action': 'validate', 'auto_repair': False}, {'action': 'validate', 'autoRepair': False}),
]
for action in ('get_active', 'get_build_settings', 'get_loaded_scenes'):
    scene_cases.append(({'action': action}, {'action': action}))
for index in (0, 1, '0', '1', None):
    for additive in (False, True):
        payload = {'action': 'load', 'build_index': index, 'additive': additive}
        wire = {'action': 'load', 'additive': additive}
        if index is not None:
            wire['buildIndex'] = int(index)
        else:
            payload['name'] = wire['name'] = 'Same'
        scene_cases.append((payload, wire))
for selector in (
    {'scene_name': 'Same'}, {'scene_path': 'Assets/Second/Same.unity'},
    {'scene_name': '', 'scene_path': 'Assets/Second/Same.unity'},
):
    wire = {'action': 'set_active_scene'}
    for key, value in selector.items():
        if value is not None:
            wire['sceneName' if key == 'scene_name' else 'scenePath'] = value
    scene_cases.append(({'action': 'set_active_scene', **selector}, wire))
prefab_cases = [
    ({'action': 'create_from_gameobject', 'target': 'Root/Fixture', 'name': 'Alias',
      'prefab_path': 'Assets/Fixture.prefab', 'allow_overwrite': False,
      'search_inactive': False, 'unlink_if_instance': False},
     {'action': 'create_from_gameobject', 'target': 'Root/Fixture', 'name': 'Alias',
      'prefabPath': 'Assets/Fixture.prefab', 'allowOverwrite': False,
      'searchInactive': False, 'unlinkIfInstance': False}),
    ({'action': 'create_from_gameobject', 'target': None, 'name': 'Root/Fixture',
      'prefab_path': 'Assets/Fixture.prefab'},
     {'action': 'create_from_gameobject', 'target': 'Root/Fixture', 'name': 'Root/Fixture',
      'prefabPath': 'Assets/Fixture.prefab'}),
    ({'action': 'modify_contents', 'prefab_path': 'Assets/Fixture.prefab', 'target': 'Child',
      'position': {'x': 0, 'y': -1, 'z': 2}, 'rotation': '[0,0,0]', 'scale': [0, 0, 0],
      'name': '', 'tag': '', 'layer': '', 'parent': '', 'set_active': False,
      'components_to_add': [], 'components_to_remove': [], 'delete_child': [],
      'component_properties': {'Rigidbody': {'mass': 0, 'useGravity': False, 'reference': None}},
      'create_child': [{'name': 'New', 'position': '[0,0,0]', 'set_active': False},
                       {'name': 'Other', 'rotation': None}]},
     {'action': 'modify_contents', 'prefabPath': 'Assets/Fixture.prefab', 'target': 'Child',
      'position': [0, -1, 2], 'rotation': [0, 0, 0], 'scale': [0, 0, 0],
      'name': '', 'tag': '', 'layer': '', 'parent': '', 'setActive': False,
      'componentsToAdd': [], 'componentsToRemove': [], 'deleteChild': [],
      'componentProperties': {'Rigidbody': {'mass': 0, 'useGravity': False, 'reference': None}},
      'createChild': [{'name': 'New', 'position': [0, 0, 0], 'set_active': False},
                      {'name': 'Other', 'rotation': None}]}),
    ({'action': 'modify_contents', 'prefab_path': 'Assets/Fixture.prefab', 'delete_child': '',
      'create_child': {'name': 'New', 'scale': {'x': 0, 'y': 0, 'z': 0}}},
     {'action': 'modify_contents', 'prefabPath': 'Assets/Fixture.prefab', 'deleteChild': '',
      'createChild': {'name': 'New', 'scale': [0, 0, 0]}}),
]
for action in ('open_prefab_stage', 'get_info', 'get_hierarchy'):
    prefab_cases.append(({'action': action, 'prefab_path': 'Assets/Fixture.prefab'},
                         {'action': action, 'prefabPath': 'Assets/Fixture.prefab'}))
for action in ('save_prefab_stage', 'close_prefab_stage'):
    prefab_cases.append(({'action': action}, {'action': action}))
for target in (-19340, '-19340', 'McpProbeCube', 'Parent/McpProbeCube', '/Parent/McpProbeCube', 0):
    prefab_cases.append((
        {'action': 'create_from_gameobject', 'target': target, 'prefab_path': 'Assets/ProbeCube.prefab'},
        {'action': 'create_from_gameobject', 'target': target, 'prefabPath': 'Assets/ProbeCube.prefab'},
    ))

async def main():
    global raw
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            for name, cases in (('manage_scene', scene_cases), ('manage_prefabs', prefab_cases)):
                check(name in tools, 'registered discovery ' + mode + name)
                print('FULL_SCHEMA', mode, name, json.dumps(tools[name].inputSchema, sort_keys=True))
                for payload, wire in cases:
                    for failed in (False, True):
                        for wrapped in (False, True):
                            expected = {
                                'success': not failed,
                                'message': 'Controlled native diagnostic' if failed else 'Done',
                                'data': {'instanceId': 321, 'rootObjectName': 'ProbeCube',
                                         'rootObjectPath': 'ProbeCube', 'cursor': 0, 'nextCursor': None,
                                         'items': [], 'isVariant': False},
                            }
                            raw = {'status': 'success', 'result': expected} if wrapped else expected
                            before = len(requests)
                            result = await client.call_tool(name, payload)
                            actual = requests[before:]
                            domain = [row for row in actual if row[1] == name]
                            label = mode + name + repr(payload) + str(failed) + str(wrapped)
                            check(result.structured_content == expected, 'response fidelity ' + label)
                            check(domain == [('Project@fixture', name, wire)], 'wire fidelity ' + label)
                            check(any(row[1] == 'get_editor_state' for row in actual),
                                  'actual preflight ' + label)
                            if not failed and not wrapped:
                                print('DOMAIN_WIRE', json.dumps({
                                    'surface': 'sdk', 'mode': mode, 'tool': name,
                                    'input': payload, 'wire': domain[-1][2],
                                }))
            for value in (True, False, 1.0, 1.9):
                payload = {'action': 'load', 'build_index': value}
                before = len(requests)
                result = await client.call_tool('manage_scene', payload, raise_on_error=False)
                check(result.is_error and len(requests) == before, 'invalid index no transport ' + mode + str(value))
                print('SELECTOR_WIRE', json.dumps({
                    'mode': mode, 'input': payload, 'rejected': result.is_error,
                    'requests': requests[before:],
                }))
            for name, payload in (
                ('manage_scene', {'action': 'load'}),
                ('manage_scene', {'action': 'load', 'build_index': -1}),
                ('manage_scene', {'action': 'close_scene', 'scene_name': None, 'scene_path': ''}),
                ('manage_scene', {'action': 'set_active_scene', 'scene_name': '', 'scene_path': None}),
                ('manage_scene', {'action': 'load', 'build_index': 'bad'}),
                ('manage_scene', {'action': 'get_hierarchy', 'cursor': 'bad'}),
                ('manage_scene', {'action': 'load', 'additive': 'bad'}),
                ('manage_scene', {'action': 'close_scene', 'remove_scene': 'bad'}),
                ('manage_scene', {'action': 'validate', 'auto_repair': 'bad'}),
                ('manage_prefabs', {'action': 'create_from_gameobject', 'prefab_path': 'Assets/Fixture.prefab'}),
                ('manage_prefabs', {'action': 'get_info', 'prefab_path': ' '}),
                ('manage_prefabs', {'action': 'modify_contents', 'prefab_path': 'Assets/Fixture.prefab',
                                    'position': '[1,2]'}),
                ('manage_prefabs', {'action': 'modify_contents', 'prefab_path': 'Assets/Fixture.prefab',
                                    'create_child': {'name': 'New', 'position': 'bad'}}),
            ):
                before = len(requests)
                result = await client.call_tool(name, payload)
                check(result.structured_content.get('success') is False and len(requests) == before,
                      'local validation before preflight ' + mode + repr(payload))
            for name, payload in (
                ('manage_scene', {'action': 'bad'}),
                ('manage_prefabs', {'action': 'bad'}),
                ('manage_prefabs', {'action': 'create_from_gameobject', 'target': {'id': 321},
                                    'prefab_path': 'Assets/Fixture.prefab'}),
            ):
                before = len(requests)
                result = await client.call_tool(name, payload, raise_on_error=False)
                check(result.is_error and len(requests) == before, 'SDK rejection ' + mode + repr(payload))
    for value in (True, False, 1.0, 1.9):
        before = len(requests)
        result = await manage_scene(DirectContext(), action='load', build_index=value)
        check(result.get('success') is False and len(requests) == before, 'direct invalid index control ' + str(value))
    print(f'fresh scene/prefab SDK checks={checks} failures={len(failures)} requests={len(requests)}')
    assert not failures, failures

anyio.run(main)
"""


def test_scene_prefab_cli_requests_json_and_errors(tmp_path):
    assert "fresh scene/prefab CLI checks=" in _run(CLI_PROGRAM, tmp_path)


def test_scene_prefab_registered_sdk_requests_and_falsey_controls(tmp_path):
    assert "fresh scene/prefab SDK checks=" in _run(SDK_PROGRAM, tmp_path)
