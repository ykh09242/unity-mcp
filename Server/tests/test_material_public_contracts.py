"""Fresh material SDK/CLI payload, slot and result contracts."""

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
    result = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(code)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_material_cli_public_values_and_single_json(tmp_path):
    output = _run(
        r"""
        import copy
        import json
        import httpx
        from click.testing import CliRunner
        from cli.main import cli
        from cli.utils import connection
        requests, failures = ([], [])
        checks = 0
        raw = {}
        client_type = httpx.AsyncClient

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=raw)
        connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
        connection._auth_headers = lambda config: {}

        def check(condition, label):
            global checks
            checks += 1
            if not condition:
                failures.append(label)
                print('FAIL', label)
        runner = CliRunner()
        path = 'Assets/Fixture.mat'
        cases = [
            (['info', path], {'action': 'get_material_info', 'materialPath': path}),
            (['create', path], {'action': 'create', 'materialPath': path, 'shader': 'Standard'}),
            (
                [
                    'create',
                    'Assets\\Materials\\Nested\\Fixture',
                    '--shader',
                    'FixtureShader',
                    '--properties',
                    '{"metallic":0,"enabled":false,"reference":null,"color":{"name":"_Color","value":[0,0,0,0]}}',
                ],
                {
                    'action': 'create',
                    'materialPath': 'Assets\\Materials\\Nested\\Fixture',
                    'shader': 'FixtureShader',
                    'properties': {
                        'metallic': 0,
                        'enabled': False,
                        'reference': None,
                        'color': {'name': '_Color', 'value': [0, 0, 0, 0]},
                    },
                },
            ),
            (
                ['create', path, '--properties', '{}'],
                {'action': 'create', 'materialPath': path, 'shader': 'Standard', 'properties': {}},
            ),
            (
                ['set-color', path, '0', '0', '0', '0'],
                {
                    'action': 'set_material_color',
                    'materialPath': path,
                    'property': '_Color',
                    'color': [0, 0, 0, 0],
                },
            ),
            (
                ['set-color', path, '1', '0', '0', '--property', '_BaseColor'],
                {
                    'action': 'set_material_color',
                    'materialPath': path,
                    'property': '_BaseColor',
                    'color': [1, 0, 0, 1],
                },
            ),
            (
                ['set-property', path, '_Metallic', '0'],
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Metallic',
                    'value': 0,
                },
            ),
            (
                ['set-property', path, '_Flag', 'false'],
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Flag',
                    'value': False,
                },
            ),
            (
                ['set-property', path, '_MainTex', ''],
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_MainTex',
                    'value': '',
                },
            ),
            (
                ['set-property', path, '_MainTex', 'null'],
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_MainTex',
                    'value': None,
                },
            ),
            (
                ['set-property', path, '_Vector', '[0,-1,2,0]'],
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Vector',
                    'value': [0, -1, 2, 0],
                },
            ),
            (
                ['set-property', path, '_Int', '16777217'],
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Int',
                    'value': 16777217,
                },
            ),
            (
                ['set-property', path, '_MainTex', '{"find":"Assets/Fixture.png"}'],
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_MainTex',
                    'value': {'find': 'Assets/Fixture.png'},
                },
            ),
            (
                ['assign', path, 'Fixture'],
                {
                    'action': 'assign_material_to_renderer',
                    'materialPath': path,
                    'target': 'Fixture',
                    'slot': 0,
                    'mode': 'shared',
                },
            ),
            (
                [
                    'assign',
                    path,
                    'Root/Fixture',
                    '--search-method',
                    'by_path',
                    '--slot',
                    '1',
                    '--mode',
                    'instance',
                ],
                {
                    'action': 'assign_material_to_renderer',
                    'materialPath': path,
                    'target': 'Root/Fixture',
                    'searchMethod': 'by_path',
                    'slot': 1,
                    'mode': 'instance',
                },
            ),
            (
                ['assign', path, '0', '--search-method', 'by_id', '--slot', '-1'],
                {
                    'action': 'assign_material_to_renderer',
                    'materialPath': path,
                    'target': '0',
                    'searchMethod': 'by_id',
                    'slot': -1,
                    'mode': 'shared',
                },
            ),
            (
                ['set-renderer-color', 'Fixture', '0', '0', '0', '0'],
                {
                    'action': 'set_renderer_color',
                    'target': 'Fixture',
                    'color': [0, 0, 0, 0],
                    'mode': 'property_block',
                },
            ),
            (
                [
                    'set-renderer-color',
                    'Root/Fixture',
                    '1',
                    '0',
                    '0',
                    '--search-method',
                    'by_path',
                    '--mode',
                    'create_unique',
                ],
                {
                    'action': 'set_renderer_color',
                    'target': 'Root/Fixture',
                    'searchMethod': 'by_path',
                    'color': [1, 0, 0, 1],
                    'mode': 'create_unique',
                },
            ),
        ]
        for args, wire in cases:
            for failed in (False, True):
                for wrapped in (False, True):
                    expected = {
                        'success': not failed,
                        'message': 'Controlled native diagnostic' if failed else 'Done',
                        'data': {'slot': 0, 'value': 0, 'enabled': False, 'texture': None, 'properties': []},
                    }
                    raw = (
                        {'status': 'success', 'result': copy.deepcopy(expected)}
                        if wrapped
                        else copy.deepcopy(expected)
                    )
                    before = len(requests)
                    result = runner.invoke(
                        cli,
                        ['--format', 'json', '--instance', 'Project@fixture', 'material', *args],
                    )
                    label = repr(args) + str(failed) + str(wrapped)
                    check(result.exit_code == (1 if failed else 0), 'exit ' + label)
                    try:
                        document = json.loads(result.stdout)
                    except ValueError:
                        document = None
                    check(document == expected, 'single JSON/falsey/native diagnostic ' + label)
                    check(
                        (
                            len(requests) == before + 1
                            and requests[-1] == {'type': 'manage_material', 'unity_instance': 'Project@fixture', 'params': wire}
                        ),
                        'value/target/slot wire ' + label,
                    )
                    if not failed and (not wrapped):
                        print(
                            'DOMAIN_WIRE',
                            json.dumps(
                                {'surface': 'cli', 'input': args, 'wire': requests[-1]['params']},
                                sort_keys=True,
                            ),
                        )
        raw = {'success': True, 'message': 'Done'}
        for (args, notice) in (
            (['create', path], 'Created material'),
            (['set-color', path, '0', '0', '0'], 'Set color'),
            (['set-property', path, '_Metallic', '0'], 'Set _Metallic'),
            (['assign', path, 'Fixture'], 'Assigned material'),
            (['set-renderer-color', 'Fixture', '0', '0', '0'], 'Set renderer color'),
        ):
            result = runner.invoke(cli, ['material', *args])
            check(result.exit_code == 0 and notice in result.stdout, 'plain success notice ' + notice)
        for args in (
            ['create', path, '--properties', '[]'],
            ['assign', path, 'Fixture', '--slot', 'bad'],
            ['assign', path, 'Fixture', '--mode', 'bad'],
        ):
            before = len(requests)
            result = runner.invoke(cli, ['material', *args])
            check(
                result.exit_code != 0 and len(requests) == before,
                'existing CLI shape/type guard ' + repr(args),
            )
        for command in ('info', 'create', 'set-color', 'set-property', 'assign', 'set-renderer-color'):
            result = runner.invoke(cli, ['material', command, '--help'])
            check(result.exit_code == 0, 'help ' + command)
            print('FULL_HELP', command, json.dumps(result.stdout))
        print(f'fresh material CLI checks={checks} failures={len(failures)} requests={len(requests)}')
        assert not failures, failures
        """,
        tmp_path,
    )
    assert "fresh material CLI checks=" in output


def test_material_registered_sdk_slots_and_value_contracts(tmp_path):
    output = _run(
        r"""
        import copy
        import json
        import anyio
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from services.tools.manage_material import manage_material
        from core.config import config
        from transport.plugin_hub import PluginHub
        config.transport_mode = 'http'
        config.http_remote_hosted = False
        requests, failures = ([], [])
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

        class FixtureContext:

            async def get_state(self, key):
                return 'Project@fixture'
        server = FastMCP('material-contracts')
        server.add_middleware(FixtureState())
        register_all_tools(server)
        path = 'Assets/Fixture.mat'
        cases = [
            ({'action': 'ping'}, {'action': 'ping'}),
            (
                {
                    'action': 'create',
                    'material_path': 'Assets\\Materials\\Fixture',
                    'properties': '{"metallic":0,"enabled":false,"reference":null}',
                },
                {
                    'action': 'create',
                    'materialPath': 'Assets\\Materials\\Fixture',
                    'properties': {'metallic': 0, 'enabled': False, 'reference': None},
                },
            ),
            (
                {'action': 'create', 'material_path': path, 'shader': 'FixtureShader', 'properties': {}},
                {'action': 'create', 'materialPath': path, 'shader': 'FixtureShader', 'properties': {}},
            ),
            (
                {'action': 'create', 'material_path': '', 'shader': '', 'properties': None},
                {'action': 'create', 'materialPath': '', 'shader': ''},
            ),
            (
                {'action': 'get_material_info', 'material_path': path},
                {'action': 'get_material_info', 'materialPath': path},
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_Metallic',
                    'value': 0,
                },
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Metallic',
                    'value': 0,
                },
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_Flag',
                    'value': False,
                },
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Flag',
                    'value': False,
                },
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_MainTex',
                    'value': '',
                },
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_MainTex',
                    'value': '',
                },
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_MainTex',
                    'value': None,
                },
                {'action': 'set_material_shader_property', 'materialPath': path, 'property': '_MainTex'},
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_Vector',
                    'value': '[0,-1,2,0]',
                },
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Vector',
                    'value': [0, -1, 2, 0],
                },
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_Int',
                    'value': 16777217,
                },
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Int',
                    'value': 16777217,
                },
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_MainTex',
                    'value': '{"find":"Assets/Fixture.png"}',
                },
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_MainTex',
                    'value': {'find': 'Assets/Fixture.png'},
                },
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_Metallic',
                    'value': '0',
                },
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Metallic',
                    'value': 0,
                },
            ),
            (
                {
                    'action': 'set_material_shader_property',
                    'material_path': path,
                    'property': '_Flag',
                    'value': 'false',
                },
                {
                    'action': 'set_material_shader_property',
                    'materialPath': path,
                    'property': '_Flag',
                    'value': False,
                },
            ),
            (
                {
                    'action': 'set_material_color',
                    'material_path': path,
                    'color': [0, 0, 0],
                    'property': '_BaseColor',
                },
                {
                    'action': 'set_material_color',
                    'materialPath': path,
                    'color': [0, 0, 0, 1],
                    'property': '_BaseColor',
                },
            ),
            (
                {
                    'action': 'set_material_color',
                    'material_path': path,
                    'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0},
                },
                {'action': 'set_material_color', 'materialPath': path, 'color': [0, 0, 0, 0]},
            ),
            (
                {'action': 'set_material_color', 'material_path': path, 'color': '#00000000'},
                {'action': 'set_material_color', 'materialPath': path, 'color': [0, 0, 0, 0]},
            ),
            (
                {
                    'action': 'assign_material_to_renderer',
                    'material_path': path,
                    'target': 'Fixture',
                    'slot': 0,
                },
                {
                    'action': 'assign_material_to_renderer',
                    'materialPath': path,
                    'target': 'Fixture',
                    'slot': 0,
                },
            ),
            (
                {
                    'action': 'assign_material_to_renderer',
                    'material_path': path,
                    'target': 'Root/Fixture',
                    'search_method': 'by_path',
                    'slot': 1,
                    'mode': 'instance',
                },
                {
                    'action': 'assign_material_to_renderer',
                    'materialPath': path,
                    'target': 'Root/Fixture',
                    'searchMethod': 'by_path',
                    'slot': 1,
                    'mode': 'instance',
                },
            ),
            (
                {
                    'action': 'assign_material_to_renderer',
                    'material_path': path,
                    'target': '0',
                    'search_method': 'by_id',
                    'slot': -1,
                },
                {
                    'action': 'assign_material_to_renderer',
                    'materialPath': path,
                    'target': '0',
                    'searchMethod': 'by_id',
                    'slot': -1,
                },
            ),
            (
                {
                    'action': 'set_renderer_color',
                    'target': 'Fixture',
                    'slot': None,
                    'mode': None,
                    'color': '[0,0,0,0]',
                },
                {'action': 'set_renderer_color', 'target': 'Fixture', 'color': [0, 0, 0, 0]},
            ),
            (
                {
                    'action': 'set_renderer_color',
                    'target': 'Fixture',
                    'slot': 1,
                    'color': [0, 0, 0, 0],
                    'mode': 'property_block',
                },
                {
                    'action': 'set_renderer_color',
                    'target': 'Fixture',
                    'slot': 1,
                    'color': [0, 0, 0, 0],
                    'mode': 'property_block',
                },
            ),
            (
                {
                    'action': 'set_renderer_color',
                    'target': 'Fixture',
                    'slot': 1,
                    'color': [0, 0, 0, 0],
                    'mode': 'shared',
                },
                {
                    'action': 'set_renderer_color',
                    'target': 'Fixture',
                    'slot': 1,
                    'color': [0, 0, 0, 0],
                    'mode': 'shared',
                },
            ),
        ]

        async def main():
            global raw
            for mode in ('2026-07-28', 'legacy'):
                async with Client(server, mode=mode) as client:
                    tools = {tool.name: tool for tool in await client.list_tools()}
                    check('manage_material' in tools, 'registry discovery ' + mode)
                    print(
                        'FULL_SCHEMA',
                        mode,
                        json.dumps(tools['manage_material'].inputSchema, sort_keys=True),
                    )
                    for payload, wire in cases:
                        for failed in (False, True):
                            for wrapped in (False, True):
                                expected = {
                                    'success': not failed,
                                    'message': 'Controlled native diagnostic' if failed else 'Done',
                                    'data': {
                                        'slot': 0,
                                        'value': 0,
                                        'enabled': False,
                                        'texture': None,
                                        'properties': [],
                                    },
                                }
                                raw = (
                                    {'status': 'success', 'result': copy.deepcopy(expected)}
                                    if wrapped
                                    else copy.deepcopy(expected)
                                )
                                before = len(requests)
                                result = await client.call_tool('manage_material', payload)
                                label = mode + repr(payload) + str(failed) + str(wrapped)
                                check(
                                    result.structured_content == expected,
                                    'native/falsey document ' + label,
                                )
                                check(
                                    (
                                        len(requests) == before + 1
                                        and requests[-1] == ('Project@fixture', 'manage_material', wire)
                                    ),
                                    'color/slot/value normalization ' + label,
                                )
                                if not failed and (not wrapped):
                                    print(
                                        'DOMAIN_WIRE',
                                        json.dumps(
                                            {
                                                'surface': 'sdk',
                                                'mode': mode,
                                                'input': payload,
                                                'wire': requests[-1][2],
                                            },
                                            sort_keys=True,
                                        ),
                                    )
                    raw = {'success': True, 'message': 'Controlled response'}
                    for action in ('assign_material_to_renderer', 'set_renderer_color'):
                        for slot in (True, False, 1.0, 0.5, '0', '1', '1e0'):
                            payload = {
                                'action': action,
                                'target': 'Fixture',
                                'material_path': path,
                                'color': [0, 0, 0, 0],
                                'slot': slot,
                            }
                            before = len(requests)
                            rejected = False
                            try:
                                await client.call_tool('manage_material', payload)
                            except ToolError:
                                rejected = True
                            wire = requests[-1][2] if len(requests) > before else None
                            check(
                                rejected and len(requests) == before,
                                'strict slot before transport ' + mode + action + repr(slot),
                            )
                            print(
                                'SLOT_WIRE',
                                json.dumps(
                                    {'mode': mode, 'input': payload, 'rejected': rejected, 'wire': wire},
                                    sort_keys=True,
                                ),
                            )
                    for payload in (
                        {'action': 'create', 'properties': 'bad'},
                        {'action': 'set_material_color', 'color': 'bad'},
                        {'action': 'set_material_shader_property', 'value': '[object Object]'},
                    ):
                        before = len(requests)
                        result = await client.call_tool('manage_material', payload)
                        check(
                            result.structured_content['success'] is False and len(requests) == before,
                            'local invalid normalization ' + mode + repr(payload),
                        )
                    for payload in (
                        {'action': 'bad'},
                        {'action': 'set_material_shader_property', 'value': {'find': 'Assets/Fixture.png'}},
                        {'action': 'set_renderer_color', 'target': 0},
                        {'action': 'set_renderer_color', 'slot': 1.5},
                    ):
                        before = len(requests)
                        rejected = False
                        try:
                            await client.call_tool('manage_material', payload)
                        except ToolError:
                            rejected = True
                        check(
                            rejected and len(requests) == before,
                            'existing schema guard ' + mode + repr(payload),
                        )
            for slot in (True, False, 0, 1):
                payload = {'action': 'set_renderer_color', 'target': 'Fixture', 'color': [0, 0, 0, 0], 'slot': slot}
                before = len(requests)
                if isinstance(slot, bool):
                    rejected = False
                    try:
                        await manage_material(FixtureContext(), **payload)
                    except ValueError:
                        rejected = True
                    check(rejected and len(requests) == before, 'direct boolean slot rejected before transport ' + str(slot))
                    continue
                await manage_material(FixtureContext(), **payload)
                wire = {'action': 'set_renderer_color', 'target': 'Fixture', 'color': [0, 0, 0, 0]}
                if not isinstance(slot, bool):
                    wire['slot'] = slot
                check(
                    len(requests) == before + 1 and requests[-1][2] == wire,
                    'direct integer slot mapping ' + str(slot),
                )
                print(
                    'SLOT_WIRE',
                    json.dumps(
                        {'mode': 'direct_internal', 'input': payload, 'wire': requests[-1][2]},
                        sort_keys=True,
                    ),
                )
            print(f'fresh material SDK checks={checks} failures={len(failures)} requests={len(requests)}')
            assert not failures, failures
        anyio.run(main)
        """,
        tmp_path,
    )
    assert "fresh material SDK checks=" in output
