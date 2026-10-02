"""Validate audio generation response contracts with fresh SDK and CLI imports."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_audio_generation_sdk_and_cli_response_contracts():
    source = '''
        import json
        import sys
        from unittest.mock import AsyncMock
        import anyio
        from click.testing import CliRunner
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        import services.tools as tools
        import services.tools.generate_audio as audio
        from services.registry import get_registered_tools
        from cli.commands.asset_gen import asset_gen
        from cli.utils import connection
        from cli.utils.config import CLIConfig, set_config

        captured = []
        pending = {"success": True, "_mcp_status": "pending", "_mcp_poll_interval": 3.0, "data": {"job_id": "owned-job", "provider": "fal", "status": "pending"}}
        response = pending
        async def send(fn, instance, command, params):
            assert instance == "Selected@hash" and command == "generate_audio"
            captured.append((command, params))
            return response
        audio.get_unity_instance_from_context = AsyncMock(return_value="Selected@hash")
        audio.send_with_unity_instance = send
        metadata = [item for item in get_registered_tools() if item["name"] == "generate_audio"]
        tools.discover_modules = lambda *args: []
        tools.get_registered_tools = lambda: metadata
        mcp = FastMCP("audio-contracts")
        tools.register_all_tools(mcp)
        async def scenario():
            global response
            for mode in ("2026-07-28", "legacy"):
                async with Client(mcp, mode=mode) as client:
                    for duration in (None, 0, 10.9):
                        result = await client.call_tool("generate_audio", {"action": "generate", "prompt": "owned sound", "duration": duration, "model": None})
                        assert result.data == pending
                        expected = {"action": "generate", "prompt": "owned sound"}
                        if duration is not None:
                            expected["duration"] = duration
                        assert captured[-1] == ("generate_audio", expected)
                    response = {"success": False, "error": "Audio import did not produce a usable AudioClip.", "data": {"state": "failed", "progress": 0}}
                    result = await client.call_tool("generate_audio", {"action": "status", "job_id": "owned-job"})
                    assert result.data == response
                    assert captured[-1] == ("generate_audio", {"action": "status", "jobId": "owned-job"})
                    response = pending
        anyio.run(scenario)

        async def cli_send(command, params, config, timeout):
            captured.append((command, params))
            return response
        connection.send_command = cli_send
        runner = CliRunner()
        set_config(CLIConfig(format="json"))
        for command in ("generate-audio", "generate-image", "generate-model"):
            result = runner.invoke(asset_gen, [command, "--prompt", "owned sound"])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == pending, result.output
        result = runner.invoke(asset_gen, ["generate-audio", "--prompt", "owned sound", "--duration", "0"])
        assert captured[-1] == ("generate_audio", {"action": "generate", "prompt": "owned sound", "duration": 0.0})
        assert json.loads(result.output) == pending
        response = {"success": False, "error": "Audio import did not produce a usable AudioClip."}
        result = runner.invoke(asset_gen, ["generate-audio", "--prompt", "owned sound"])
        assert result.exit_code == 1 and json.loads(result.output) == response, result.output
        response = {"success": True, "data": {"job_id": None}}
        result = runner.invoke(asset_gen, ["generate-audio", "--prompt", "owned sound"])
        assert result.exit_code == 0 and json.loads(result.output) == response
        response = pending
        set_config(CLIConfig(format="text"))
        result = runner.invoke(asset_gen, ["generate-audio", "--prompt", "owned sound"])
        assert result.exit_code == 0 and "unity-mcp asset-gen status --job-id owned-job" in result.output
    '''
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
