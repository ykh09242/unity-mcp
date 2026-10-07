"""Package operation output remains machine-readable and describes scheduling honestly."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.commands.packages import packages
from cli.utils.config import CLIConfig


@pytest.mark.parametrize(
    "args",
    [
        ["add", "com.example.fixture"],
        ["remove", "com.example.fixture", "--force"],
        ["embed", "com.example.fixture"],
        ["resolve"],
        ["add-registry", "Fixture", "--url", "https://example.com", "--scope", "com.example"],
        ["remove-registry", "Fixture"],
    ],
)
@pytest.mark.parametrize("success", [True, False])
def test_package_operation_json_is_one_document(args, success):
    response = {"success": success, "message": "Fixture", "data": {"job_id": "fixture-package"}}
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format="json")):
        with patch("cli.commands.packages.run_command", return_value=response):
            result = CliRunner().invoke(packages, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == response


def test_resolve_text_does_not_claim_completion():
    response = {
        "success": True,
        "message": "Package resolution triggered. Unity will re-resolve all packages.",
    }
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format="text")):
        with patch("cli.commands.packages.run_command", return_value=response):
            result = CliRunner().invoke(packages, ["resolve"])
    assert result.exit_code == 0, result.output
    assert "Packages resolved" not in result.output
    assert "resolution triggered" in result.output


def test_package_text_retains_job_polling_guidance():
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format="text")):
        with patch(
            "cli.commands.packages.run_command",
            return_value={"success": True, "data": {"job_id": "fixture-package"}},
        ):
            result = CliRunner().invoke(packages, ["add", "com.example.fixture"])
    assert result.exit_code == 0, result.output
    assert "unity-mcp packages status fixture-package" in result.output
