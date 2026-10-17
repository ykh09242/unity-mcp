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
        with patch(
            "cli.commands.build.run_command",
            return_value={"success": True, "data": {"job_id": "fixture-build"}},
        ):
            result = CliRunner().invoke(build, args)
    assert result.exit_code == 0, result.output
    assert "unity-mcp build status fixture-build" in result.output


@pytest.mark.parametrize(
    "args,expected",
    [
        (["plan"], {"action": "plan"}),
        (
            [
                "plan",
                "--target",
                "windows64",
                "--output",
                "Builds/Game.exe",
                "--development",
                "--backend",
                "il2cpp",
                "--subtarget",
                "server",
                "--clean",
                "--auto-run",
            ],
            {
                "action": "plan",
                "target": "windows64",
                "output_path": "Builds/Game.exe",
                "development": True,
                "scripting_backend": "il2cpp",
                "subtarget": "server",
                "options": ["clean_build", "auto_run"],
            },
        ),
        (
            ["run", "--expected-plan", "a" * 64],
            {"action": "build_from_plan", "expected_plan_sha256": "a" * 64},
        ),
        (["run"], {"action": "build"}),
    ],
)
def test_plan_cli_payload_and_terminal_json(args, expected):
    reply = {"success": True, "data": {"plan_sha256": "a" * 64, "ready_to_schedule": True}}
    with patch("cli.commands.build.get_config", return_value=CLIConfig(format="json")):
        with patch("cli.commands.build.run_command", return_value=reply) as send:
            result = CliRunner().invoke(build, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == reply
    assert send.call_count == 1
    assert send.call_args.args[:2] == ("manage_build", expected)


@pytest.mark.parametrize("value", ["", "a" * 63, "g" * 64, "a" * 65, " a" * 32])
def test_cli_bad_expected_hash_never_sends(value):
    with patch("cli.commands.build.run_command") as send:
        result = CliRunner().invoke(build, ["run", "--expected-plan", value])
    assert result.exit_code != 0
    assert not send.called


def test_plan_has_no_started_or_polling_guidance():
    with patch("cli.commands.build.get_config", return_value=CLIConfig(format="text")):
        with patch(
            "cli.commands.build.run_command",
            return_value={"success": True, "data": {"plan_sha256": "a" * 64}},
        ):
            result = CliRunner().invoke(build, ["plan"])
    assert result.exit_code == 0
    assert "Build started" not in result.output
    assert "build status" not in result.output
