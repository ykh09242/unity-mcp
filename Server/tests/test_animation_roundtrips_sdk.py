"""Actual FastMCP schema coverage for precise animation identities and finite key payloads."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_animation_round_trips_through_actual_sdk():
    source = textwrap.dedent("""
        import asyncio
        import importlib
        import warnings
        from fastmcp import Client, FastMCP
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        tool = importlib.import_module('services.tools.manage_animation')
        sent = []
        async def instance(ctx): return 'fixture'
        async def send(sender, instance, command, params, **kwargs):
            sent.append(params)
            return {'success': True, 'data': {'revision': 'fresh'}}
        tool.get_unity_instance_from_context = instance
        tool.send_with_unity_instance = send
        server = FastMCP('animation-round-trips')
        wrapped = log_execution('manage_animation', 'Tool')(tool.manage_animation)
        wrapped = telemetry_tool('manage_animation')(wrapped)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            server.tool(name='manage_animation')(wrapped)
        assert not caught, [str(item.message) for item in caught]
        async def main():
            async with Client(server, mode='2026-07-28') as client:
                for action, props in [
                    ('clip_get_curves', {'page_size': 1}),
                    ('clip_get_curve', {'binding_id': 'binding', 'page_size': 1}),
                    ('clip_replace_curve', {'binding_id': 'binding', 'expected_revision': 'fresh', 'keys': [{'time': 0, 'value': {'guid': 'a' * 32, 'file_id': '21300000'}}]}),
                    ('clip_delete_curve', {'binding_id': 'binding', 'expected_revision': 'fresh'}),
                    ('controller_get_graph', {'page_size': 1}),
                    ('controller_modify_transition', {'transition_id': '110100001', 'expected_revision': 'fresh', 'changes': {'duration': .75}}),
                    ('controller_delete_transition', {'transition_id': '110100001', 'expected_revision': 'fresh'}),
                    ('controller_modify_state', {'state_id': '110200001', 'expected_revision': 'fresh', 'changes': {'motion': None}}),
                    ('controller_delete_state', {'state_id': '110200001', 'expected_revision': 'fresh'}),
                ]:
                    response = await client.call_tool('manage_animation', {'action': action, 'clip_path': 'Assets/Clip.anim', 'controller_path': 'Assets/Graph.controller', 'properties': props})
                    assert response.structured_content['success'] is True
                    assert sent[-1]['properties'] == props
                before = len(sent)
                response = await client.call_tool('manage_animation', {'action': 'controller_delete_transition', 'properties': {'from_state': 'Idle', 'expected_revision': 'fresh'}})
                assert response.structured_content['success'] is False
                assert len(sent) == before
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
