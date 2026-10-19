"""Exercise prefab plan tokens through the real installed FastMCP schema."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_prefab_preview_sdk_preserves_token_and_rejects_replacement_inputs():
    source = textwrap.dedent("""
        import asyncio
        import importlib
        import warnings
        from fastmcp import Client, FastMCP
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        tool = importlib.import_module('services.tools.manage_prefabs')
        sent = []
        async def instance(ctx): return 'fixture'
        async def gate(ctx, **kwargs): return None
        async def send(sender, instance, command, params, **kwargs):
            sent.append(params)
            return {'success': True, 'data': {'preview_id': 'fixture'}}
        tool.get_unity_instance_from_context = instance
        tool.preflight = gate
        tool.send_with_unity_instance = send
        server = FastMCP('prefab-plan-sdk')
        wrapped = log_execution('manage_prefabs', 'Tool')(tool.manage_prefabs)
        wrapped = telemetry_tool('manage_prefabs')(wrapped)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            server.tool(name='manage_prefabs')(wrapped)
        assert not caught, [str(item.message) for item in caught]
        async def main():
            async with Client(server, mode='2026-07-28') as client:
                result = await client.call_tool('manage_prefabs', {'action': 'preview_modify_contents', 'prefab_path': 'Assets/Source.prefab', 'position': [1, 2, 3]})
                assert result.structured_content['success'] is True
                assert sent[-1] == {'action': 'preview_modify_contents', 'prefabPath': 'Assets/Source.prefab', 'position': [1., 2., 3.]}
                result = await client.call_tool('manage_prefabs', {'action': 'apply_preview', 'preview_id': 'fixture'})
                assert result.structured_content['success'] is True
                assert sent[-1] == {'action': 'apply_preview', 'preview_id': 'fixture'}
                before = len(sent)
                result = await client.call_tool('manage_prefabs', {'action': 'apply_preview', 'preview_id': 'fixture', 'name': 'Injected'})
                assert result.structured_content['success'] is False
                assert len(sent) == before
                result = await client.call_tool('manage_prefabs', {'action': 'preview_modify_contents', 'prefab_path': 'Assets/Source.prefab', 'name': 'Unsupported'})
                assert result.structured_content['success'] is False
                assert 'normalizes' in result.structured_content['message']
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
