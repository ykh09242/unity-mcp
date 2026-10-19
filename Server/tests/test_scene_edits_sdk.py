"""Exercise typed scene edits through the installed SDK in a fresh process."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_scene_edit_sdk_schema_preserves_expected_old_presence():
    source = textwrap.dedent("""
        import asyncio
        import importlib
        import json
        import warnings
        from fastmcp import Client, FastMCP
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        tool = importlib.import_module('services.tools.manage_scene_edits')
        sent = []
        gates = []
        async def instance(ctx):
            return 'fixture'
        async def gate(ctx, **kwargs):
            gates.append(kwargs)
            return None
        async def send(sender, instance, command, params, **kwargs):
            sent.append(params)
            return {'success': True, 'data': {'preview_id': 'fixture'}}
        tool.get_unity_instance_from_context = instance
        tool.preflight = gate
        tool.send_with_unity_instance = send
        server = FastMCP('scene-edit-sdk')
        wrapped = log_execution('manage_scene_edits', 'Tool')(tool.manage_scene_edits)
        wrapped = telemetry_tool('manage_scene_edits')(wrapped)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            server.tool(name='manage_scene_edits')(wrapped)
        assert not caught, [str(item.message) for item in caught]
        async def main():
            async with Client(server, mode='2026-07-28') as client:
                schema = (await client.list_tools())[0].input_schema
                json.dumps(schema, allow_nan=False)
                edit = {'target': 42, 'component_type': 'BoxCollider', 'property': 'm_IsTrigger', 'value': True}
                result = await client.call_tool('manage_scene_edits', {'action': 'preview', 'edits': [edit]})
                assert result.structured_content['success'] is True
                assert 'expected_old' not in sent[-1]['edits'][0]
                before = len(sent), len(gates)
                result = await client.call_tool('manage_scene_edits', {'action': 'apply', 'edits': [edit]})
                assert result.structured_content['success'] is False
                assert (len(sent), len(gates)) == before
                for expected in (False, 0, None):
                    result = await client.call_tool('manage_scene_edits', {'action': 'apply', 'edits': [{**edit, 'expected_old': expected}]})
                    assert result.structured_content['success'] is True
                    assert 'expected_old' in sent[-1]['edits'][0]
                    assert sent[-1]['edits'][0]['expected_old'] == expected
                    assert type(sent[-1]['edits'][0]['expected_old']) is type(expected)
                operations = [{'op': 'create', 'temp_id': '$parent', 'name': 'Parent', 'parent': 42}, {'op': 'reparent', 'target': 43, 'parent': '$parent'}]
                result = await client.call_tool('manage_scene_edits', {'action': 'preview_structure', 'target': 42, 'operations': operations})
                assert result.structured_content['success'] is True
                assert sent[-1]['operations'] == operations
                result = await client.call_tool('manage_scene_edits', {'action': 'apply_structure', 'preview_id': 'fixture'})
                assert result.structured_content['success'] is True
                assert 'operations' not in sent[-1]
                capsule = {'target': 42, 'component_type': 'CapsuleCollider', 'property': 'm_Radius', 'value': 2, 'expected_old': .5}
                result = await client.call_tool('manage_scene_edits', {'action': 'apply', 'edits': [capsule]})
                assert result.structured_content['success'] is True
                assert sent[-1]['edits'] == [capsule]
                result = await client.call_tool('manage_scene_edits', {'action': 'apply', 'preview_id': 'fixture'})
                assert result.structured_content['success'] is True
                assert 'edits' not in sent[-1]
                assert all(entry['refresh_if_dirty'] is False for entry in gates)
                print('scene-edit SDK schema and expected_old presence passed')
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scene-edit SDK schema and expected_old presence passed" in result.stdout
