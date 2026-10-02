"""Build CLI settings and machine-readable pending responses."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.commands.build import build
from cli.utils.config import CLIConfig


@pytest.mark.parametrize("value", [None, "", "NEW"])
def test_settings_preserves_explicit_empty_value(value):
    args = ["settings", "defines"]
    if value is not None:
        args += ["--value", value]
    with patch("cli.commands.build.get_config", return_value=CLIConfig(format="json")):
        with patch("cli.commands.build.run_command", return_value={"success": True}) as send:
            result = CliRunner().invoke(build, args)
    assert result.exit_code == 0, result.output
    params = send.call_args.args[1]
    assert ("value" in params) is (value is not None)
    if value is not None:
        assert params["value"] == value


@pytest.mark.parametrize("args", [["run"], ["batch", "--targets", "windows64"]])
def test_pending_build_json_is_one_document(args):
    response = {"success": True, "_mcp_status": "pending", "data": {"job_id": "fixture-build"}}
    with patch("cli.commands.build.get_config", return_value=CLIConfig(format="json")):
        with patch("cli.commands.build.run_command", return_value=response):
            result = CliRunner().invoke(build, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == response


@pytest.mark.parametrize("args", [["run"], ["batch", "--targets", "windows64"]])
def test_text_pending_build_retains_polling_guidance(args):
    with patch("cli.commands.build.get_config", return_value=CLIConfig(format="text")):
        with patch("cli.commands.build.run_command", return_value={"success": True, "data": {"job_id": "fixture-build"}}):
            result = CliRunner().invoke(build, args)
    assert result.exit_code == 0, result.output
    assert "unity-mcp build status fixture-build" in result.output
