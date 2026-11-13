"""Package operation output remains machine-readable and describes scheduling honestly."""

import json
import shlex
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.commands.packages import packages
from cli.utils.config import CLIConfig


@pytest.mark.parametrize("count", [2, 12])
@pytest.mark.parametrize("format_type", ["text", "json"])
@pytest.mark.parametrize("command", ["list", "info"])
def test_package_reads_keep_versions_and_bounded_details(count, format_type, command):
    rows = [{"name": f"com.example.fixture{i}", "version": f"1.0.{i}"} for i in range(count)]
    if command == "list":
        for row in rows:
            row.update(display_name="Fixture", source="Registry")
        data = {"packages": rows, "count": count}
        args = ["list"]
        expected = {"action": "list_packages"}
    else:
        data = {
            "name": "com.example.parent",
            "version": "2.0.0",
            "display_name": "Parent",
            "description": "Fixture parent",
            "source": "Embedded",
            "resolved_path": "Packages/com.example.parent",
            "author": "Fixture",
            "dependencies": rows,
            "dependency_count": count,
        }
        args = ["info", "com.example.parent"]
        expected = {"action": "get_package_info", "package": "com.example.parent"}
    response = {"success": True, "message": "Package query complete.", "data": data}
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format=format_type)):
        with patch("cli.commands.packages.run_command", return_value=response) as send:
            result = CliRunner().invoke(packages, args)
    assert result.exit_code == 0, result.output
    assert send.call_args.args[:2] == ("manage_packages", expected)
    if format_type == "json":
        assert json.loads(result.output) == response
    else:
        visible = range(count) if count <= 10 else [*range(5), *range(count - 5, count)]
        for index in visible:
            assert f"name: com.example.fixture{index}\n" in result.output
            assert f"version: 1.0.{index}\n" in result.output
        if command == "list":
            assert result.output.count("source: Registry") == min(count, 10)
        if count > 10:
            assert "name: com.example.fixture5\n" not in result.output
            assert f"... ({count - 10} more)" in result.output


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


@pytest.mark.parametrize("format_type", ["text", "json"])
def test_empty_package_status_shows_native_acknowledgement(format_type):
    response = {"success": True, "message": "No package jobs found."}
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format=format_type)):
        with patch("cli.commands.packages.run_command", return_value=response):
            result = CliRunner().invoke(packages, ["status"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    if format_type == "json":
        assert json.loads(result.stdout) == response
    else:
        assert result.stdout.strip() == response["message"]


def test_package_text_retains_job_polling_guidance():
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format="text")):
        with patch(
            "cli.commands.packages.run_command",
            return_value={"success": True, "data": {"job_id": "fixture-package"}},
        ):
            result = CliRunner().invoke(packages, ["add", "com.example.fixture"])
    assert result.exit_code == 0, result.output
    hint = result.output.split("Poll with: ", 1)[1].splitlines()[0]
    assert shlex.split(hint)[-3:] == ["packages", "status", "fixture-package"]


@pytest.fixture(autouse=True)
def isolated_shared_output_config(monkeypatch):
    import cli.utils.config as shared_config

    monkeypatch.setattr(shared_config, "_config", CLIConfig(format="json"))


def test_resolve_unknown_receipt_prints_job_and_limit_without_completion_claim():
    response = {
        "success": True,
        "message": "Package resolution triggered; completion is unknown.",
        "data": {
            "job_id": "resolve-owned",
            "status": "unknown",
            "resolve": {"completion": "unknown", "persistence_scope": "editor_session"},
        },
    }
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format="text")):
        with patch("cli.commands.packages.run_command", return_value=response) as send:
            result = CliRunner().invoke(packages, ["resolve"])
    assert result.exit_code == 0, result.output
    hint = result.output.split("Observe with: ", 1)[1].splitlines()[0]
    assert shlex.split(hint)[-3:] == ["packages", "status", "resolve-owned"]
    assert "unknown" in result.output.lower()
    assert "safe to retry" not in result.output.lower()
    send.assert_called_once()


@pytest.mark.parametrize("success", [True, False])
def test_resolve_job_json_is_exact_native_receipt(success):
    response = {
        "success": success,
        "data": {"job_id": "resolve-owned", "status": "unknown"},
    }
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format="json")):
        with patch("cli.commands.packages.run_command", return_value=response) as send:
            result = CliRunner().invoke(packages, ["resolve"])
    assert json.loads(result.output) == response
    send.assert_called_once()


def test_failed_resolve_text_exposes_receipt_without_claiming_triggered():
    response = {
        "success": False,
        "error": "Resolve invocation did not return normally; native completion remains unknown.",
        "data": {"job_id": "resolve-failed", "status": "failed"},
    }
    with patch("cli.commands.packages.get_config", return_value=CLIConfig(format="text")):
        with patch("cli.commands.packages.run_command", return_value=response) as send:
            result = CliRunner().invoke(packages, ["resolve"])
    assert result.exit_code == 0, result.output
    hint = result.output.split("Observe with: ", 1)[1].splitlines()[0]
    assert shlex.split(hint)[-3:] == ["packages", "status", "resolve-failed"]
    assert "unknown" in result.output.lower()
    assert "Editor session" in result.output
    assert "resolution triggered" not in result.output.lower()
    send.assert_called_once()
