"""Editor control CLI keeps JSON parseable and reports actual pause toggle outcomes."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.commands.editor import editor
from cli.utils.config import CLIConfig


@pytest.mark.parametrize(
    "args",
    [
        ["play"],
        ["pause"],
        ["stop"],
        ["console", "--clear"],
        ["add-tag", "Fixture"],
        ["remove-tag", "Fixture"],
        ["add-layer", "Fixture"],
        ["remove-layer", "Fixture"],
        ["tool", "Move"],
        ["deploy"],
        ["restore"],
        ["undo"],
        ["redo"],
        ["menu", "Fixture/Action"],
        ["refresh", "--no-wait"],
        ["custom-tool", "Fixture"],
    ],
)
@pytest.mark.parametrize("success", [True, False])
def test_editor_command_json_is_one_document(args, success):
    response = {"success": success, "message": "Fixture outcome", "data": {"value": 0}}
    with patch("cli.commands.editor.get_config", return_value=CLIConfig(format="json")):
        with patch("cli.commands.editor.run_command", return_value=response):
            result = CliRunner().invoke(editor, args)
    assert result.exit_code == (1 if args[0] == "custom-tool" and not success else 0), result.output
    assert json.loads(result.output) == response


@pytest.mark.parametrize(
    "args,message",
    [
        (["play"], "Already in play mode."),
        (["pause"], "Game paused."),
        (["pause"], "Game resumed."),
        (["stop"], "Already stopped (not in play mode)."),
    ],
)
def test_play_control_text_uses_unity_state_message(args, message):
    response = {"success": True, "message": message}
    with patch("cli.commands.editor.get_config", return_value=CLIConfig(format="text")):
        with patch("cli.commands.editor.run_command", return_value=response):
            result = CliRunner().invoke(editor, args)
    assert result.exit_code == 0, result.output
    assert message in result.output
    for misleading in ("Entered play mode", "Paused play mode", "Stopped play mode"):
        assert misleading not in result.output


def test_pause_help_describes_toggle():
    result = CliRunner().invoke(editor, ["pause", "--help"])
    assert "resume" in result.output.lower()


@pytest.mark.parametrize(
    "args", [["tests", "--async"], ["poll-test", "fixture-job", "--wait", "0"]]
)
@pytest.mark.parametrize("status", ["running", "succeeded", "failed", "cancelled"])
def test_test_job_json_is_one_document(args, status):
    response = {
        "success": True,
        "data": {
            "job_id": "fixture-job",
            "status": status,
            "result": {"summary": {"failed": 1}},
            "progress": {"completed": 0, "total": 2},
        },
    }
    with patch("cli.commands.editor.get_config", return_value=CLIConfig(format="json")):
        with patch("cli.commands.editor.run_command", return_value=response) as send:
            result = CliRunner().invoke(editor, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == response
    send.assert_called_once()


@pytest.mark.parametrize("format", ["json", "text"])
def test_missing_custom_tool_only_fetches_suggestions_for_text(format):
    response = {"success": False, "error": "Tool 'Fxiture' not found"}
    with patch("cli.commands.editor.get_config", return_value=CLIConfig(format=format)):
        with patch("cli.commands.editor.run_command", return_value=response):
            with patch(
                "cli.commands.editor.run_list_custom_tools",
                return_value={"tools": [{"name": "Fixture"}]},
            ) as listing:
                result = CliRunner().invoke(editor, ["custom-tool", "Fxiture"])
    assert result.exit_code == 1, result.output
    if format == "json":
        assert json.loads(result.output) == response
        listing.assert_not_called()
    else:
        assert "Fixture" in result.output
        listing.assert_called_once()
