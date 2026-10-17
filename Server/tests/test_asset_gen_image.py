"""Tests for the generate_image asset-gen tool and CLI command (fal/OpenRouter).

Pass-through tool: NO API keys, NO file bytes. Unity transport fully mocked.
"""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner

from cli.commands.asset_gen import asset_gen
from cli.utils.config import CLIConfig
from services.registry import get_registered_tools

from services.tools import generate_image as generate_image_module
from services.tools.generate_image import generate_image


COMMAND = "generate_image"
ALLOWED_KEYS = {
    "action",
    "provider",
    "mode",
    "prompt",
    "imagePath",
    "imageUrl",
    "model",
    "transparent",
    "width",
    "height",
    "name",
    "outputFolder",
    "jobId",
}


def _call_tool(**kwargs):
    ctx = MagicMock()
    with patch.object(
        generate_image_module,
        "get_unity_instance_from_context",
        new=AsyncMock(return_value="unity-1"),
    ):
        with patch.object(
            generate_image_module,
            "send_with_unity_instance",
            new=AsyncMock(return_value={"success": True, "data": {}}),
        ) as mock_send:
            result = asyncio.run(generate_image(ctx, **kwargs))
    return result, mock_send.call_args.args


def _sent_command(sent_args):
    return sent_args[2]


def _sent_params(sent_args):
    return sent_args[3]


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config():
    return CLIConfig(host="127.0.0.1", port=8080, timeout=30, format="text", unity_instance=None)


@pytest.fixture
def cli_runner(runner, mock_config):
    def _invoke(args):
        with patch("cli.commands.asset_gen.get_config", return_value=mock_config):
            with patch(
                "cli.commands.asset_gen.run_command",
                return_value={"success": True, "message": "OK", "data": {}},
            ) as mock_run:
                result = runner.invoke(asset_gen, args)
                return result, mock_run

    return _invoke


class TestGenerateImageRegistration:
    def test_tool_registered_under_asset_gen_group(self):
        tools = get_registered_tools()
        tool = next((t for t in tools if t["name"] == "generate_image"), None)
        assert tool is not None
        assert tool["group"] == "asset_gen"


class TestGenerateImageRouting:
    def test_generate_routes_to_command(self):
        _, sent = _call_tool(action="generate", provider="fal", prompt="a stone texture")
        assert _sent_command(sent) == COMMAND
        assert _sent_params(sent)["action"] == "generate"

    @pytest.mark.parametrize("action", ["remove_background", "REMOVE_BACKGROUND"])
    def test_remove_background_rejected_before_resolution_transport_or_files(
        self, action, tmp_path
    ):
        source = tmp_path / "original.png"
        original = b"unchanged source fixture"
        source.write_bytes(original)
        output = tmp_path / "must-not-create"
        with (
            patch.object(
                generate_image_module,
                "get_unity_instance_from_context",
                new=AsyncMock(
                    side_effect=AssertionError("unsupported requests must not resolve Unity")
                ),
            ) as resolve,
            patch.object(
                generate_image_module,
                "send_with_unity_instance",
                new=AsyncMock(side_effect=AssertionError("unsupported requests must not dispatch")),
            ) as send,
        ):
            response = asyncio.run(
                generate_image(
                    MagicMock(),
                    action=action,
                    provider="fal",
                    image_path=str(source),
                    output_folder=str(output),
                )
            )

        assert response["success"] is False
        assert "unsupported" in response["error"].lower()
        assert response["data"] == {
            "reason": "unsupported_capability",
            "capability": "remove_background",
            "supported": False,
        }
        assert "job_id" not in response.get("data", {})
        resolve.assert_not_awaited()
        send.assert_not_awaited()
        assert source.read_bytes() == original
        assert not output.exists()

    def test_status_and_job_id_mapping(self):
        _, sent = _call_tool(action="status", job_id="j5")
        assert _sent_params(sent) == {"action": "status", "jobId": "j5"}

    def test_param_camelcase_mapping(self):
        _, sent = _call_tool(
            action="generate",
            provider="fal",
            mode="image",
            image_path="a.png",
            image_url="http://b",
            width=512,
            height=512,
            output_folder="Assets/Generated/Images",
        )
        params = _sent_params(sent)
        assert params["imagePath"] == "a.png"
        assert params["imageUrl"] == "http://b"
        assert params["outputFolder"] == "Assets/Generated/Images"
        assert params["width"] == 512 and params["height"] == 512
        for snake in ("image_path", "image_url", "output_folder"):
            assert snake not in params

    def test_action_is_lowercased(self):
        _, sent = _call_tool(action="GENERATE", prompt="p")
        assert _sent_params(sent)["action"] == "generate"

    def test_none_values_stripped(self):
        _, sent = _call_tool(action="generate", provider="fal", prompt="p")
        assert _sent_params(sent) == {"action": "generate", "provider": "fal", "prompt": "p"}

    def test_no_secret_keys_in_payload(self):
        _, sent = _call_tool(
            action="generate",
            provider="fal",
            mode="text",
            prompt="p",
            image_path="a.png",
            image_url="b",
            model="flux",
            transparent=True,
            width=256,
            height=256,
            name="Tex",
            output_folder="Assets/Generated/Images",
            job_id="j",
        )
        params = _sent_params(sent)
        assert set(params.keys()).issubset(ALLOWED_KEYS)
        joined = " ".join(params.keys()).lower()
        for forbidden in ("key", "secret", "token", "apikey", "password"):
            assert forbidden not in joined

    def test_non_dict_response_guarded(self):
        ctx = MagicMock()
        with patch.object(
            generate_image_module,
            "get_unity_instance_from_context",
            new=AsyncMock(return_value="u"),
        ):
            with patch.object(
                generate_image_module, "send_with_unity_instance", new=AsyncMock(return_value=42)
            ):
                result = asyncio.run(generate_image(ctx, action="status", job_id="j"))
        assert result["success"] is False
        assert "42" in result["message"]


def test_sdk_discovery_excludes_background_removal_and_validation_prevents_dispatch():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import Client, FastMCP
        from services.registry import get_registered_tools
        module = importlib.import_module("services.tools.generate_image")
        sent = []
        resolutions = []
        async def instance(ctx):
            resolutions.append(True)
            return None
        async def send(fn, instance, command, params):
            sent.append((command, params))
            return {"success": True, "data": {"job_id": "fixture-image"}}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = send
        server = FastMCP("image-capabilities")
        metadata = next(tool for tool in get_registered_tools() if tool["name"] == "generate_image")
        server.tool(name="generate_image", description=metadata["description"])(module.generate_image)
        async def main():
            for protocol in ("2026-07-28", "legacy"):
                async with Client(server, mode=protocol) as client:
                    tool = next(tool for tool in await client.list_tools() if tool.name == "generate_image")
                    assert set(tool.input_schema["properties"]["action"]["enum"]) == {
                        "generate", "status", "cancel", "list_providers", "list_models", "refresh_models"
                    }
                    assert "unsupported" in tool.description.lower()
                    before = len(sent), len(resolutions)
                    rejected = await client.call_tool("generate_image", {
                        "action": "remove_background", "image_path": "Assets/original.png"
                    }, raise_on_error=False)
                    assert rejected.is_error
                    assert (len(sent), len(resolutions)) == before
                    result = await client.call_tool("generate_image", {
                        "action": "generate", "prompt": "fixture", "transparent": True
                    })
                    assert result.structured_content == {"success": True, "data": {"job_id": "fixture-image"}}
                    assert sent[-1] == ("generate_image", {
                        "action": "generate", "prompt": "fixture", "transparent": True
                    })
            assert len(sent) == len(resolutions) == 2
            print("image capability discovery and SDK rejection passed")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
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
    assert "image capability discovery and SDK rejection passed" in result.stdout


class TestGenerateImageCLI:
    def test_invalid_mode_preserves_unity_failure(self, runner, mock_config):
        failure = {"success": False, "error": "'mode' must be 'text' or 'image'."}
        with (
            patch("cli.commands.asset_gen.get_config", return_value=mock_config),
            patch("cli.commands.asset_gen.run_command", return_value=failure) as send,
        ):
            result = runner.invoke(
                asset_gen, ["generate-image", "--mode", "garbage", "--prompt", "fixture"]
            )
        assert result.exit_code == 0
        assert send.call_args.args[1]["mode"] == "garbage"
        assert "mode" in result.output and "text" in result.output
        assert "started" not in result.output and "Poll with" not in result.output

    def test_generate_image_cli(self, cli_runner):
        result, mock_run = cli_runner(
            [
                "generate-image",
                "--provider",
                "fal",
                "--prompt",
                "a stone texture",
                "--width",
                "512",
                "--transparent",
            ]
        )
        assert result.exit_code == 0
        command = mock_run.call_args.args[0]
        params = mock_run.call_args.args[1]
        assert command == COMMAND
        assert params["action"] == "generate"
        assert params["provider"] == "fal"
        assert params["width"] == 512
        assert params["transparent"] is True
        assert set(params.keys()).issubset(ALLOWED_KEYS)


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("state", ["running", "failed", "canceled", "done"])
def test_recovery_status_evidence_through_registered_sdk(protocol, state):
    from fastmcp import Client, FastMCP
    from core.logging_decorator import log_execution
    from core.telemetry_decorator import telemetry_tool

    async def exercise():
        entry = next(tool for tool in get_registered_tools() if tool["name"] == COMMAND)
        server = FastMCP("generation-recovery-contract")
        wrapped = log_execution(COMMAND, "Tool")(entry["func"])
        server.tool(name=entry["name"], description=entry["description"], **entry["kwargs"])(
            telemetry_tool(COMMAND)(wrapped)
        )
        response = {
            "success": state != "failed",
            "data": {
                "state": state,
                "job_id": "owned-local-job",
                "recovery_state": "resuming"
                if state == "running"
                else "outcome_unknown"
                if state == "failed"
                else "terminal",
                "recovery_code": "resumed"
                if state == "running"
                else "import_outcome_unknown"
                if state == "failed"
                else state,
                "provider_task_id": "owned-provider-task",
                "provider_phase": "poll" if state == "running" else "import",
                "asset_path": "Assets/Owned/output.obj" if state != "running" else None,
                "asset_guid": "a" * 32 if state != "running" else None,
                "model_import_stage": "registered"
                if state == "failed"
                else "usable"
                if state == "done"
                else None,
                "archive_selection": {
                    "requested_path": "main/model.obj",
                    "selected_path": "main/model.obj",
                    "candidates": ["main/model.obj", "preview/model.obj"],
                    "extraction": "complete",
                },
            },
        }
        if state == "failed":
            response["error"] = (
                "Prior import outcome is unknown; retained output requires inspection."
            )
        with patch.object(
            generate_image_module,
            "get_unity_instance_from_context",
            new=AsyncMock(return_value="owned"),
        ):
            with patch.object(
                generate_image_module,
                "send_with_unity_instance",
                new=AsyncMock(return_value=response),
            ) as send:
                async with Client(server, mode=protocol) as client:
                    result = await client.call_tool(
                        COMMAND, {"action": "status", "job_id": "owned-local-job"}
                    )
                assert result.structured_content == response
                send.assert_awaited_once()
                assert send.call_args.args[2:] == (
                    COMMAND,
                    {"action": "status", "jobId": "owned-local-job"},
                )

    asyncio.run(exercise())
