"""Exercise isolated registered MCP and CLI GameObject transport contracts."""

import os
import subprocess
import sys
import textwrap


def _run(code, tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(code)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_gameobject_cli_lifecycle_transforms_and_single_json(tmp_path):
    output = _run(
        r'''
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

        cases = [
            (['create', 'Fixture'], {'action': 'create', 'name': 'Fixture'}),
            (
                ['create', 'Fixture', '--components', 'BoxCollider, Rigidbody'],
                {'action': 'create', 'name': 'Fixture', 'componentsToAdd': ['BoxCollider', 'Rigidbody']},
            ),
            (
                ['create', 'Fixture', '--components', ''],
                {'action': 'create', 'name': 'Fixture'},
            ),
            (
                ['create', 'Fixture', '--components', ', BoxCollider,'],
                {'action': 'create', 'name': 'Fixture', 'componentsToAdd': ['', 'BoxCollider', '']},
            ),
            (
                ['create', 'Fixture', '--primitive', 'Cube', '--position', '0', '-1', '2',
                 '--rotation', '0', '0', '0', '--scale', '0', '0', '0', '--parent', 'Parent',
                 '--tag', 'Untagged', '--layer', 'Default'],
                {'action': 'create', 'name': 'Fixture', 'primitiveType': 'Cube',
                 'position': [0, -1, 2], 'rotation': [0, 0, 0], 'scale': [0, 0, 0],
                 'parent': 'Parent', 'tag': 'Untagged', 'layer': 'Default'},
            ),
            (
                ['create', 'Fixture', '--save-prefab', '--prefab-path', 'Assets/Owned/Fixture.prefab'],
                {'action': 'create', 'name': 'Fixture', 'saveAsPrefab': True,
                 'prefabPath': 'Assets/Owned/Fixture.prefab'},
            ),
            (
                ['modify', '321', '--search-method', 'by_id', '--name', 'Renamed',
                 '--position', '0', '0', '0', '--rotation', '0', '-90', '0',
                 '--scale', '0', '1', '-1', '--inactive', '--no-static'],
                {'action': 'modify', 'target': '321', 'searchMethod': 'by_id', 'name': 'Renamed',
                 'position': [0, 0, 0], 'rotation': [0, -90, 0], 'scale': [0, 1, -1],
                 'setActive': False, 'isStatic': False},
            ),
            (
                ['modify', 'Root/Fixture', '--search-method', 'by_path', '--active', '--static',
                 '--parent', 'Parent', '--add-components', 'BoxCollider,Rigidbody',
                 '--remove-components', 'SphereCollider'],
                {'action': 'modify', 'target': 'Root/Fixture', 'searchMethod': 'by_path',
                 'setActive': True, 'isStatic': True, 'parent': 'Parent',
                 'componentsToAdd': ['BoxCollider', 'Rigidbody'], 'componentsToRemove': ['SphereCollider']},
            ),
            (
                ['modify', '321', '--inactive'],
                {'action': 'modify', 'target': '321', 'setActive': False},
            ),
            (
                ['modify', '321', '--search-method', 'by_name', '--inactive'],
                {'action': 'modify', 'target': '321', 'searchMethod': 'by_name', 'setActive': False},
            ),
            (['delete', 'Fixture', '--force'], {'action': 'delete', 'target': 'Fixture'}),
            (
                ['delete', '0', '--force', '--search-method', 'by_id'],
                {'action': 'delete', 'target': '0', 'searchMethod': 'by_id'},
            ),
            (['duplicate', 'Fixture'], {'action': 'duplicate', 'target': 'Fixture'}),
            (
                ['duplicate', 'Root/Fixture', '--search-method', 'by_path', '--name', 'Copy',
                 '--offset', '0', '-1', '2'],
                {'action': 'duplicate', 'target': 'Root/Fixture', 'searchMethod': 'by_path',
                 'new_name': 'Copy', 'offset': [0, -1, 2]},
            ),
            (
                ['move', 'Fixture', '--reference', 'Reference', '--direction', 'right'],
                {'action': 'move_relative', 'target': 'Fixture', 'reference_object': 'Reference',
                 'direction': 'right', 'distance': 1, 'world_space': True},
            ),
            (
                ['move', '321', '--search-method', 'by_id', '--reference', 'Reference',
                 '--direction', 'forward', '--distance', '0', '--local'],
                {'action': 'move_relative', 'target': '321', 'searchMethod': 'by_id',
                 'reference_object': 'Reference', 'direction': 'forward', 'distance': 0,
                 'world_space': False},
            ),
        ]
        for args, wire in cases:
            for failed in (False, True):
                for wrapped in (False, True):
                    expected = {
                        'success': not failed,
                        'message': 'Controlled native diagnostic' if failed else 'Done',
                        'data': {'instanceID': 321, 'name': 'Fixture', 'activeSelf': False,
                                 'layer': 0, 'parent': None, 'components': []},
                    }
                    raw = {'status': 'success', 'result': expected} if wrapped else expected
                    before = len(requests)
                    result = runner.invoke(
                        cli, ['--format', 'json', '--instance', 'Project@fixture', 'gameobject', *args]
                    )
                    label = repr(args) + str(failed) + str(wrapped)
                    check(result.exit_code == (1 if failed else 0), 'exit ' + label)
                    try:
                        document = json.loads(result.stdout)
                    except ValueError:
                        document = None
                    check(document == expected, 'single JSON/native failure/falsey ' + label)
                    check(
                        len(requests) == before + 1
                        and requests[-1] == {'type': 'manage_gameobject', 'params': wire,
                                             'unity_instance': 'Project@fixture'},
                        'one owned lifecycle request ' + label,
                    )
                    if not failed and not wrapped:
                        print('DOMAIN_WIRE', json.dumps({'surface': 'cli', 'input': args,
                                                        'requests': requests[before:]}))

        raw = {'success': True, 'message': 'Done'}
        for args, notice in (
            (['create', 'Fixture'], 'Created GameObject'),
            (['delete', 'Fixture', '--force'], 'Deleted GameObject'),
            (['duplicate', 'Fixture'], 'Duplicated GameObject'),
            (['move', 'Fixture', '--reference', 'Reference', '--direction', 'right'], 'Moved'),
        ):
            result = runner.invoke(cli, ['gameobject', *args])
            check(result.exit_code == 0 and notice in result.stdout, 'text notice ' + notice)

        for args in (
            ['create', 'Fixture', '--position', 'bad', '0', '0'],
            ['modify', 'Fixture', '--search-method', 'bad'],
            ['move', 'Fixture', '--reference', 'Reference', '--direction', 'bad'],
        ):
            before = len(requests)
            result = runner.invoke(cli, ['gameobject', *args])
            check(result.exit_code != 0 and len(requests) == before, 'invalid option ' + repr(args))
        for command in ('find', 'create', 'modify', 'delete', 'duplicate', 'move'):
            result = runner.invoke(cli, ['gameobject', command, '--help'])
            check(result.exit_code == 0, 'help ' + command)
            print('FULL_HELP', command, json.dumps(result.stdout))
        print(f'fresh gameobject CLI checks={checks} failures={len(failures)} requests={len(requests)}')
        assert not failures, failures
        ''',
        tmp_path,
    )
    assert "fresh gameobject CLI checks=" in output


def test_gameobject_registered_sdk_vectors_flags_and_preflight(tmp_path):
    output = _run(
        r'''
        import copy
        import json
        import anyio
        from fastmcp import FastMCP, Client
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
                return {'success': True, 'data': {'compilation': {'is_compiling': False}}}
            if command == 'get_project_info':
                return {'success': True, 'data': {}}
            return copy.deepcopy(raw)

        PluginHub.send_command_for_instance = staticmethod(controlled_send)

        class FixtureState(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
                return await call_next(context)

        server = FastMCP('gameobject-contracts')
        server.add_middleware(FixtureState())
        register_all_tools(server)
        cases = [
            ({'action': 'create', 'name': 'Fixture'}, {'action': 'create', 'name': 'Fixture'}),
            (
                {'action': 'create', 'name': 'Fixture', 'parent': 'Fixture',
                 'position': {'x': 0, 'y': -1, 'z': 2}, 'rotation': '[0,0,0]', 'scale': [0, 0, 0],
                 'components_to_add': ['BoxCollider', {'type_name': 'Rigidbody',
                                                     'properties': {'mass': 0, 'useGravity': False}}]},
                {'action': 'create', 'name': 'Fixture', 'parent': 'Fixture',
                 'position': [0, -1, 2], 'rotation': [0, 0, 0], 'scale': [0, 0, 0],
                 'componentsToAdd': ['BoxCollider', {'typeName': 'Rigidbody',
                                                   'properties': {'mass': 0, 'useGravity': False}}]},
            ),
            (
                {'action': 'create', 'name': 'Fixture', 'save_as_prefab': 'true',
                 'prefab_folder': 'Assets\\Owned'},
                {'action': 'create', 'name': 'Fixture', 'saveAsPrefab': True,
                 'prefabPath': 'Assets/Owned/Fixture.prefab'},
            ),
            (
                {'action': 'create', 'name': 'Fixture', 'save_as_prefab': True,
                 'prefab_folder': ''},
                {'action': 'create', 'name': 'Fixture', 'saveAsPrefab': True,
                 'prefabPath': '/Fixture.prefab'},
            ),
            (
                {'action': 'create', 'name': 'Fixture', 'save_as_prefab': False,
                 'prefab_path': 'Assets/Owned/Source.prefab'},
                {'action': 'create', 'name': 'Fixture', 'saveAsPrefab': False,
                 'prefabPath': 'Assets/Owned/Source.prefab'},
            ),
            (
                {'action': 'create', 'name': 'Fixture', 'save_as_prefab': True,
                 'prefab_path': '../Outside/Fixture.prefab'},
                {'action': 'create', 'name': 'Fixture', 'saveAsPrefab': True,
                 'prefabPath': '../Outside/Fixture.prefab'},
            ),
            (
                {'action': 'modify', 'target': '321', 'search_method': 'by_id', 'name': 'Renamed',
                 'set_active': False, 'is_static': 'false', 'position': [0, 0, 0],
                 'component_properties': '{"Rigidbody":{"mass":0,"useGravity":false,"reference":null}}'},
                {'action': 'modify', 'target': '321', 'searchMethod': 'by_id', 'name': 'Renamed',
                 'setActive': False, 'isStatic': False, 'position': [0, 0, 0],
                 'componentProperties': {'Rigidbody': {'mass': 0, 'useGravity': False, 'reference': None}}},
            ),
            (
                {'action': 'modify', 'name': 'Fixture', 'target': None, 'parent': '',
                 'components_to_remove': '["BoxCollider"]', 'components_to_add': 'Rigidbody'},
                {'action': 'modify', 'name': 'Fixture', 'parent': '',
                 'componentsToRemove': ['BoxCollider'], 'componentsToAdd': ['Rigidbody']},
            ),
            (
                {'action': 'delete', 'target': 'Root/Fixture', 'search_method': 'by_path'},
                {'action': 'delete', 'target': 'Root/Fixture', 'searchMethod': 'by_path'},
            ),
            (
                {'action': 'duplicate', 'target': '0', 'search_method': 'by_id',
                 'new_name': '', 'offset': '[0,-1,2]', 'parent': None},
                {'action': 'duplicate', 'target': '0', 'searchMethod': 'by_id',
                 'new_name': '', 'offset': [0, -1, 2]},
            ),
            (
                {'action': 'duplicate', 'target': 'Fixture', 'parent': '',
                 'position': [0, 0, 0], 'offset': [1, 2, 3]},
                {'action': 'duplicate', 'target': 'Fixture', 'parent': '',
                 'position': [0, 0, 0], 'offset': [1, 2, 3]},
            ),
            (
                {'action': 'move_relative', 'target': 'Fixture', 'reference_object': 'Reference',
                 'direction': 'front'},
                {'action': 'move_relative', 'target': 'Fixture', 'reference_object': 'Reference',
                 'direction': 'front'},
            ),
            (
                {'action': 'move_relative', 'target': '321', 'search_method': 'by_id',
                 'reference_object': 'Reference', 'offset': [0, 0, 0],
                 'distance': 0, 'world_space': False},
                {'action': 'move_relative', 'target': '321', 'searchMethod': 'by_id',
                 'reference_object': 'Reference', 'offset': [0, 0, 0],
                 'distance': 0, 'world_space': False},
            ),
            (
                {'action': 'move_relative', 'target': 'Fixture', 'reference_object': 'Root/Reference',
                 'direction': 'behind', 'distance': -1, 'world_space': 'true'},
                {'action': 'move_relative', 'target': 'Fixture', 'reference_object': 'Root/Reference',
                 'direction': 'behind', 'distance': -1, 'world_space': True},
            ),
            (
                {'action': 'look_at', 'target': '321', 'search_method': 'by_id',
                 'look_at_target': 'Reference', 'look_at_up': [0, 1, 0]},
                {'action': 'look_at', 'target': '321', 'searchMethod': 'by_id',
                 'look_at_target': 'Reference', 'look_at_up': [0, 1, 0]},
            ),
            (
                {'action': 'look_at', 'target': 'Fixture', 'look_at_target': [0, 0, 0],
                 'look_at_up': '[0,1,0]'},
                {'action': 'look_at', 'target': 'Fixture', 'look_at_target': [0, 0, 0],
                 'look_at_up': '[0,1,0]'},
            ),
            ({'action': 'modify', 'target': '', 'position': None}, {'action': 'modify', 'target': ''}),
            ({'action': 'modify', 'target': '321', 'set_active': False},
             {'action': 'modify', 'target': '321', 'setActive': False}),
            ({'action': 'modify', 'target': '321', 'search_method': 'by_name', 'set_active': False},
             {'action': 'modify', 'target': '321', 'searchMethod': 'by_name', 'setActive': False}),
            ({'action': 'modify', 'target': '321', 'search_method': 'by_id', 'set_active': False},
             {'action': 'modify', 'target': '321', 'searchMethod': 'by_id', 'setActive': False}),
        ]

        async def main():
            global raw
            for mode in ('2026-07-28', 'legacy'):
                async with Client(server, mode=mode) as client:
                    tools = {tool.name: tool for tool in await client.list_tools()}
                    check('manage_gameobject' in tools, 'registry ' + mode)
                    print('FULL_SCHEMA', mode, json.dumps(tools['manage_gameobject'].inputSchema, sort_keys=True))
                    # This controlled response is the native required-path diagnostic.
                    # Actual native path/save consequences are checked independently.
                    raw = {
                        'success': False,
                        'message': "'prefabPath' is required when 'saveAsPrefab' is true and creating a new object.",
                    }
                    payload = {'action': 'create', 'name': 'Fixture', 'save_as_prefab': True}
                    before = len(requests)
                    result = await client.call_tool('manage_gameobject', payload)
                    domain = [row for row in requests[before:] if row[1] == 'manage_gameobject']
                    check(result.structured_content == raw, 'native required-path diagnostic ' + mode)
                    check(domain == [('Project@fixture', 'manage_gameobject',
                                      {'action': 'create', 'name': 'Fixture', 'saveAsPrefab': True,
                                       'world_space': True})],
                          'absent folder must not fabricate path ' + mode)
                    print('DOMAIN_WIRE', json.dumps({'surface': 'sdk', 'mode': mode,
                                                    'input': payload, 'wire': domain[-1][2]}))
                    for payload, wire in cases:
                        for failed in (False, True):
                            for wrapped in (False, True):
                                expected = {
                                    'success': not failed,
                                    'message': 'Controlled native diagnostic' if failed else 'Done',
                                    'data': {'instanceID': 321, 'name': 'Fixture', 'activeSelf': False,
                                             'layer': 0, 'parent': None, 'components': []},
                                }
                                raw = {'status': 'success', 'result': expected} if wrapped else expected
                                before = len(requests)
                                result = await client.call_tool('manage_gameobject', payload)
                                actual = requests[before:]
                                domain = [row for row in actual if row[1] == 'manage_gameobject']
                                expected_wire = {**wire, 'world_space': wire.get('world_space', True)}
                                label = mode + repr(payload) + str(failed) + str(wrapped)
                                check(result.structured_content == expected, 'native response ' + label)
                                check(domain == [('Project@fixture', 'manage_gameobject', expected_wire)],
                                      'opaque/vector/falsey wire ' + label)
                                check(any(row[1] == 'get_editor_state' for row in actual),
                                      'real preflight ' + label)
                                if not failed and not wrapped:
                                    print('DOMAIN_WIRE', json.dumps({'surface': 'sdk', 'mode': mode,
                                                                    'input': payload, 'wire': domain[-1][2]}))
                    for payload in (
                        {}, {'action': None},
                        {'action': 'create', 'position': '[1,2]'},
                        {'action': 'modify', 'rotation': {'x': 0}},
                        {'action': 'duplicate', 'offset': 'bad'},
                        {'action': 'create', 'components_to_add': '[1]'},
                        {'action': 'modify', 'component_properties': '[]'},
                        {'action': 'create', 'save_as_prefab': True},
                        {'action': 'create', 'name': 'Fixture', 'save_as_prefab': True,
                         'prefab_path': 'Assets/Fixture.bad'},
                    ):
                        before = len(requests)
                        result = await client.call_tool('manage_gameobject', payload)
                        check(result.structured_content.get('success') is False and len(requests) == before,
                              'local rejection before preflight ' + mode + repr(payload))
                    for payload in (
                        {'action': 'invalid'},
                        {'action': 'modify', 'target': {'instanceID': 321}},
                        {'action': 'modify', 'target': 321},
                        {'action': 'modify', 'search_method': 'invalid'},
                    ):
                        before = len(requests)
                        result = await client.call_tool('manage_gameobject', payload, raise_on_error=False)
                        check(result.is_error and len(requests) == before,
                              'existing SDK schema rejection ' + mode + repr(payload))
            print(f'fresh gameobject SDK checks={checks} failures={len(failures)} requests={len(requests)}')
            assert not failures, failures

        anyio.run(main)
        ''',
        tmp_path,
    )
    assert "fresh gameobject SDK checks=" in output
