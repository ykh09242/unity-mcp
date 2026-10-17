"""Explicit upload acknowledgement at the real Click command boundary, with fake dispatch."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.commands.asset_gen import asset_gen
from cli.utils.config import CLIConfig


@pytest.mark.parametrize("acknowledged", [False, True])
def test_local_image_consent_is_opt_in(acknowledged):
    config = CLIConfig(host="127.0.0.1", port=8080, timeout=30, format="text", unity_instance=None)
    arguments = [
        "generate-model",
        "--provider",
        "tripo",
        "--mode",
        "image",
        "--image-path",
        "Assets/reference.png",
    ]
    if acknowledged:
        arguments.append("--allow-image-upload")
    with patch("cli.commands.asset_gen.get_config", return_value=config):
        with patch(
            "cli.commands.asset_gen.run_command", return_value={"success": True, "data": {}}
        ) as dispatch:
            result = CliRunner().invoke(asset_gen, arguments)
    assert result.exit_code == 0, result.output
    command, payload, sent_config = dispatch.call_args.args
    assert command == "generate_model" and sent_config is config
    assert payload["imagePath"] == "Assets/reference.png"
    assert ("allowImageUpload" in payload) is acknowledged
    if acknowledged:
        assert payload["allowImageUpload"] is True


def test_hosted_input_remains_one_url_payload_without_implicit_consent():
    config = CLIConfig(host="127.0.0.1", port=8080, timeout=30, format="text", unity_instance=None)
    with patch("cli.commands.asset_gen.get_config", return_value=config):
        with patch(
            "cli.commands.asset_gen.run_command", return_value={"success": True, "data": {}}
        ) as dispatch:
            result = CliRunner().invoke(
                asset_gen,
                [
                    "generate-model",
                    "--mode",
                    "image",
                    "--image-url",
                    "https://fixture.invalid/reference.png",
                ],
            )
    assert result.exit_code == 0, result.output
    assert dispatch.call_args.args[1] == {
        "action": "generate",
        "mode": "image",
        "imageUrl": "https://fixture.invalid/reference.png",
    }


def test_upload_option_is_a_flag_without_a_value():
    result = CliRunner().invoke(asset_gen, ["generate-model", "--allow-image-upload", "true"])
    assert result.exit_code == 2
