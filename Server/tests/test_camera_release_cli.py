"""Native camera release acknowledgements remain visible in CLI text output."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.commands.camera import camera
from cli.utils.config import CLIConfig


@pytest.mark.parametrize("format_type", ["text", "json"])
@pytest.mark.parametrize(
    "message",
    [
        "No active camera override to release.",
        "The override owner was destroyed; override state cleared.",
    ],
)
def test_message_only_release_acknowledgement_is_visible_once(format_type, message):
    response = {"success": True, "message": message}
    with patch("cli.commands.camera.get_config", return_value=CLIConfig(format=format_type)):
        with patch("cli.commands.camera.run_command", return_value=response) as send:
            result = CliRunner().invoke(camera, ["release"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert send.call_args.args[:2] == ("manage_camera", {"action": "release_override"})
    if format_type == "json":
        assert json.loads(result.stdout) == response
    else:
        assert result.stdout.strip() == message


@pytest.mark.parametrize("format_type", ["text", "json"])
def test_release_with_receipt_preserves_native_data(format_type):
    response = {
        "success": True,
        "message": "Camera override released.",
        "data": {"releasedOverrideId": 4},
    }
    with patch("cli.commands.camera.get_config", return_value=CLIConfig(format=format_type)):
        with patch("cli.commands.camera.run_command", return_value=response):
            result = CliRunner().invoke(camera, ["release"])
    assert result.exit_code == 0, result.output
    if format_type == "json":
        assert json.loads(result.stdout) == response
    else:
        assert "releasedOverrideId: 4" in result.stdout
