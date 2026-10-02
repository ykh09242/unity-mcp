"""CLI script commands must match Unity's manage_script endpoint."""
import importlib
import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from cli.utils.config import CLIConfig

script_cli = importlib.import_module("cli.commands.script")


@pytest.fixture
def command_sender(monkeypatch):
    monkeypatch.setattr(script_cli, "get_config", lambda: CLIConfig(format="json"))
    sender = Mock(return_value={"success": True, "data": {"contents": "first\nsecond\nthird\n"}})
    monkeypatch.setattr(script_cli, "run_command", sender)
    return sender


@pytest.mark.parametrize("command,expected_action", [("read", "read"), ("delete", "delete")])
def test_windows_script_path_routes_to_requested_file(command_sender, command, expected_action):
    # Given: a Windows-separated script path.
    args = [command, r"Assets\Scripts\Foo.cs"] + (["--force"] if command == "delete" else [])
    # When: the actual Click command runs.
    result = CliRunner().invoke(script_cli.script, args)
    # Then: Unity receives its supported locator/action.
    assert result.exit_code == 0, result.output
    assert command_sender.call_args.args[:2] == (
        "manage_script", {"action": expected_action, "name": "Foo", "path": "Assets/Scripts"},
    )


def test_edit_uses_unity_manage_script_action(command_sender):
    # Given: the documented CLI text edit payload.
    edits = [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 2, "newText": "X"}]
    # When: the CLI executes an edit.
    result = CliRunner().invoke(script_cli.script, ["edit", "Assets/Scripts/Foo.cs", "--edits", json.dumps(edits)])
    # Then: it calls the real Unity command, not the Python-only MCP wrapper name.
    assert result.exit_code == 0, result.output
    assert command_sender.call_args.args[:2] == (
        "manage_script", {"action": "apply_text_edits", "name": "Foo", "path": "Assets/Scripts", "edits": edits},
    )


def test_validate_uses_unity_manage_script_action(command_sender):
    # Given: a script path and standard validation.
    # When: the CLI executes validation.
    result = CliRunner().invoke(script_cli.script, ["validate", "Assets/Scripts/Foo.cs", "--level", "standard"])
    # Then: the supported Unity action gets the name/directory locator.
    assert result.exit_code == 0, result.output
    assert command_sender.call_args.args[:2] == (
        "manage_script", {"action": "validate", "name": "Foo", "path": "Assets/Scripts", "level": "standard"},
    )


def test_read_respects_requested_line_window(command_sender):
    # Given: Unity's read action returns the full source.
    # When: the CLI requests only the second line.
    result = CliRunner().invoke(script_cli.script, ["read", "Assets/Foo.cs", "--start-line", "2", "--line-count", "1"])
    # Then: output contains precisely the requested window, with no ignored wire fields.
    assert result.exit_code == 0, result.output
    import json
    assert json.loads(result.output)["data"]["contents"] == "second\n"
    assert command_sender.call_args.args[1] == {"action": "read", "name": "Foo", "path": "Assets"}


@pytest.mark.parametrize("option,value", [("--start-line", "0"), ("--start-line", "-1"), ("--line-count", "0")])
def test_read_rejects_nonpositive_line_bounds(command_sender, option, value):
    # Given: a line bound that cannot name a 1-based window.
    # When: Click parses it.
    result = CliRunner().invoke(script_cli.script, ["read", "Assets/Foo.cs", option, value])
    # Then: validation fails before Unity is contacted.
    assert result.exit_code == 2
    command_sender.assert_not_called()
