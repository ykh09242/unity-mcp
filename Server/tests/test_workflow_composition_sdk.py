"""Exercise composed operations through registered current and legacy MCP clients."""

import os
import subprocess
import sys


PROGRAM = r"""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from core.config import config
from services.tools import register_all_tools
from services.registry import operation_invocation as invocation
from services.registry.operation_scheduler import OperationScope
from services.registry.operation_context import active_operation
from services.registry.operation_target import OperationTarget
from services.verification.service import VerificationService
from services.tools import wait_for_operation as waiting
from services import job_completion
from transport.plugin_hub import PluginHub

config.transport_mode = 'http'
config.http_remote_hosted = False
calls = []
checks = []
target = OperationTarget(OperationScope('local', 'fixture', 'g1'), lambda: True)
invocation.capture_target = AsyncMock(return_value=target)
observation_target = waiting.ObservationTarget('Project@fixture', 'session', 'g1', None)
waiting.capture_target = AsyncMock(return_value=observation_target)
job_completion.capture_target = waiting.capture_target

async def start_edit(self, ctx, instance, **kwargs):
    assert active_operation() is None
    assert kwargs['original_target'].scope == target.scope
    checks.append(kwargs)
    return {'success': True, 'data': {'run_id': 'a' * 32, 'state': 'running', 'stages': []}}

VerificationService.start_edit = start_edit

async def send(instance, command, params, **kwargs):
    calls.append((command, copy.deepcopy(params)))
    if command == 'get_editor_state':
        return {'success': True, 'data': {'settings': {'batch_execute_max_commands': 100}}}
    if command == 'batch_execute':
        assert 'verification' not in params
        return {'success': False, 'data': {
            'executionStatus': 'partial',
            'results': [
                {'tool': 'manage_script', 'result': {'success': True, 'data': {
                    'path': 'Assets/A.cs', 'bytes_sha256': 'b' * 64, 'source_saved': True}}},
                {'tool': 'manage_script', 'result': {'success': False, 'error': 'rejected'}}],
            'outcomes': [{'index': 0, 'state': 'succeeded'}, {'index': 1, 'state': 'failed'}]}}
    if command == 'manage_recording':
        assert params['action'] == 'start'
        assert 'wait_timeout' not in params
        return {'success': True, 'data': {'job_id': 'c' * 32, 'status': 'recording'}}
    raise AssertionError((command, params))

PluginHub.send_command_for_instance = staticmethod(send)

async def status(captured, command, params):
    assert captured == observation_target
    assert waiting.operation_wait_target.get() == observation_target
    assert command == 'manage_recording'
    assert params == {'action': 'status', 'job_id': 'c' * 32}
    calls.append((command, copy.deepcopy(params)))
    return {'success': True, 'data': {'job_id': 'c' * 32, 'status': 'failed',
        'error': 'encoder failed', 'frames_recorded': 3}}

waiting.fetch_observation = status

class FixtureState(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state('unity_instance', 'Project@fixture')
        return await call_next(context)

server = FastMCP('workflow-composition')
server.add_middleware(FixtureState())
register_all_tools(server)

async def main():
    for mode in ('2026-07-28', 'legacy'):
        async with Client(server, mode=mode) as client:
            before = len(checks)
            result = await client.call_tool('batch_execute', {'verification': 'scripts', 'commands': [
                {'tool': 'manage_script', 'params': {'action': 'update', 'name': 'A'}},
                {'tool': 'manage_script', 'params': {'action': 'update', 'name': 'B'}}]}, raise_on_error=False)
            body = result.structured_content
            assert body['success'] is False, body
            assert body['data'].get('executionStatus') == 'partial', body
            assert body['data']['verification']['run_id'] == 'a' * 32, body
            assert len(checks) == before + 1
            assert checks[-1]['expected_sources'] == {'Assets/A.cs': {'bytes_sha256': 'b' * 64}}
            assert checks[-1]['paths'] == ['Assets/A.cs']
            await client.call_tool('manage_tools', {'action': 'activate', 'tool': 'manage_recording'})
            result = await client.call_tool('manage_recording', {'action': 'start', 'wait_timeout': 2})
            body = result.structured_content
            assert body['success'] is True, body
            assert body['data']['status'] == 'recording'
            assert body['data']['completion']['status'] == 'failed', body
            assert body['data']['completion']['complete'] is True
            assert body['data']['completion']['summary']['error'] == 'encoder failed'
    assert sum(command == 'batch_execute' for command, _ in calls) == 2
    assert sum(command == 'manage_recording' and params['action'] == 'start' for command, params in calls) == 2
    print('WORKFLOW_SDK_OK')

anyio.run(main)
"""


def test_registered_workflow_composition(tmp_path):
    env = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", PROGRAM], env=env, capture_output=True, text=True, timeout=90
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WORKFLOW_SDK_OK" in result.stdout
