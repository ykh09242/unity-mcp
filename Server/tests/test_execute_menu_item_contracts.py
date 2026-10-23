"""Menu evidence/observation boundary through the registered real MCP SDK."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_registered_menu_contract_schema_forwarding_and_no_execute_replay():
    program = textwrap.dedent("""
        import asyncio, copy, json
        from fastmcp import Client, FastMCP
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from core.config import config
        from transport.plugin_hub import PluginHub
        config.transport_mode = 'http'
        config.http_remote_hosted = False
        sends = []
        reply = {'success': True, 'message': 'Invocation accepted; completion not observed.',
                 'data': {'existence': {'value': None, 'source': 'unavailable'},
                          'invocation': {'attempted': True, 'accepted': True},
                          'completion': {'status': 'not_observed', 'observed': None}}}
        async def send(instance, command, params, **kwargs):
            sends.append((instance, command, copy.deepcopy(params), kwargs))
            return copy.deepcopy(reply)
        PluginHub.send_command_for_instance = staticmethod(send)
        class Selected(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state('unity_instance', 'DEV11@owned')
                return await call_next(context)
        server = FastMCP('dev11-menu-contract')
        server.add_middleware(Selected())
        register_all_tools(server)
        async def main():
            for mode in ('2026-07-28', 'legacy'):
                async with Client(server, mode=mode) as client:
                    discovered = {tool.name: tool for tool in await client.list_tools()}
                    assert 'inspect_menu_item' not in discovered and 'execute_menu_item_observed' not in discovered
                    tool = discovered['execute_menu_item']
                    assert tool.annotations.destructiveHint is True
                    for field in ('action', 'wait_for', 'timeout_ms'):
                        assert field in tool.inputSchema['properties'], field
                    cases = [
                        ({'menu_path': 'Fixture/Owned'}, {'menuPath': 'Fixture/Owned'}, False),
                        ({'menu_path': 'Fixture/Owned', 'action': 'inspect'},
                         {'menuPath': 'Fixture/Owned', 'action': 'inspect'}, True),
                        ({'menu_path': 'Fixture/Owned', 'action': 'exists'},
                         {'menuPath': 'Fixture/Owned', 'action': 'exists'}, True),
                        ({'menu_path': 'Window/General/Console', 'wait_for': 'console_window_open', 'timeout_ms': 60000},
                         {'menuPath': 'Window/General/Console', 'wait_for': 'console_window_open', 'timeout_ms': 60000, 'timeout_seconds': 60}, False),
                    ]
                    for payload, expected, retries in cases:
                        before = len(sends)
                        result = await client.call_tool('execute_menu_item', payload, raise_on_error=False)
                        assert len(sends) == before + 1
                        instance, command, params, options = sends[-1]
                        expected_command = ('inspect_menu_item' if payload.get('action') in ('inspect', 'exists')
                                            else 'execute_menu_item_observed' if payload.get('wait_for') == 'console_window_open'
                                            else 'execute_menu_item')
                        assert (instance, command, params) == ('DEV11@owned', expected_command, expected)
                        assert options['retry_on_reload'] is retries
                        assert result.structured_content['data'] == reply['data']
                    for payload in (
                        {'action': 'arbitrary'}, {'action': False}, {'wait_for': 'expression'},
                        {'menu_path': 'Fixture/Owned', 'wait_for': 'console_window_open'},
                        {'timeout_ms': True}, {'timeout_ms': '20'}, {'timeout_ms': 0},
                        {'timeout_ms': 10001}, {'timeout_ms': 2.5},
                        {'menu_path': 'x' * 1025}, {'menu_path': 'Fixture/Owned\\n'},
                    ):
                        before = len(sends)
                        result = await client.call_tool('execute_menu_item', payload, raise_on_error=False)
                        assert result.is_error or result.structured_content['success'] is False, payload
                        assert len(sends) == before, payload
                    reply.update(success=False, error='postcondition_timeout')
                    reply['data']['reason'] = 'postcondition_timeout'
                    reply['data']['completion'] = {'status': 'timeout', 'observed': False}
                    result = await client.call_tool('execute_menu_item', {'menu_path': 'Fixture/Owned'}, raise_on_error=False)
                    assert result.structured_content['success'] is False
                    assert result.structured_content['data'] == reply['data']
            print('DEV11 actual SDK modes passed; controlled native provider only')
        asyncio.run(main())
    """)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_old_native_dispatch_table_cannot_execute_new_semantics_or_receive_fallback():
    program = textwrap.dedent("""
        import asyncio, copy
        from fastmcp import Client, FastMCP
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from core.config import config
        from transport.plugin_hub import PluginHub
        config.transport_mode = 'http'
        config.http_remote_hosted = False
        sends, executions = [], []
        # Faithful old native dispatch behavior: action/wait are ignored by its
        # sole execute_menu_item handler. Unknown commands never enter that handler.
        def old_execute(params):
            executions.append(copy.deepcopy(params))
            return {'success': True, 'message': 'Attempted to execute menu item; check logs.'}
        old_table = {'execute_menu_item': old_execute}
        async def send(instance, command, params, **kwargs):
            sends.append((instance, command, copy.deepcopy(params), kwargs))
            if command not in old_table:
                return {'success': False, 'error': 'Unknown or unsupported command type: ' + command,
                        'data': {'reason': 'unsupported_command'}}
            return old_table[command](params)
        PluginHub.send_command_for_instance = staticmethod(send)
        class Selected(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state('unity_instance', 'OLD@owned')
                return await call_next(context)
        server = FastMCP('dev11-old-native-dispatch')
        server.add_middleware(Selected())
        register_all_tools(server)
        async def main():
            for mode in ('2026-07-28', 'legacy'):
                async with Client(server, mode=mode) as client:
                    for payload, command in (
                        ({'menu_path': 'Fixture/Owned', 'action': 'inspect'}, 'inspect_menu_item'),
                        ({'menu_path': 'Fixture/Owned', 'action': 'exists'}, 'inspect_menu_item'),
                        ({'menu_path': 'Window/General/Console', 'wait_for': 'console_window_open'}, 'execute_menu_item_observed'),
                    ):
                        before = len(sends)
                        result = await client.call_tool('execute_menu_item', payload, raise_on_error=False)
                        assert not executions, ('Older native handler executed new semantics', executions)
                        assert len(sends) == before + 1, sends[before:]
                        assert sends[-1][0:2] == ('OLD@owned', command)
                        assert result.structured_content['success'] is False
                        assert result.structured_content['data']['reason'] == 'unsupported_command'
                        assert sends[-1][3]['retry_on_reload'] is (command == 'inspect_menu_item')
            print('DEV11 old-native dispatch zero executions, six unsupported requests, no fallback')
        asyncio.run(main())
    """)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    result = subprocess.run(
        [sys.executable, "-B", "-c", program], env=env, text=True, capture_output=True, timeout=90
    )
    assert result.returncode == 0, result.stdout + result.stderr
