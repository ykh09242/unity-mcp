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
    monkeypatch.setattr("cli.utils.connection.get_config", lambda: CLIConfig(format="json"))
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
        "manage_script",
        {"action": expected_action, "name": "Foo", "path": "Assets/Scripts"},
    )


def test_edit_uses_unity_manage_script_action(command_sender):
    # Given: the documented CLI text edit payload.
    edits = [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 2, "newText": "X"}]
    command_sender.side_effect = [
        {"success": True, "data": {"sha256": "a" * 64}},
        {"success": True, "data": {"editsApplied": 1}},
    ]
    # When: the CLI executes an edit.
    result = CliRunner().invoke(
        script_cli.script, ["edit", "Assets/Scripts/Foo.cs", "--edits", json.dumps(edits)]
    )
    # Then: it calls the real Unity command, not the Python-only MCP wrapper name.
    assert result.exit_code == 0, result.output
    assert command_sender.call_args.args[:2] == (
        "manage_script",
        {
            "action": "apply_text_edits",
            "name": "Foo",
            "path": "Assets/Scripts",
            "edits": edits,
            "precondition_sha256": "a" * 64,
        },
    )
    assert command_sender.call_count == 2
    assert command_sender.call_args_list[0].args[:2] == (
        "manage_script",
        {"action": "get_sha", "name": "Foo", "path": "Assets/Scripts"},
    )


@pytest.mark.parametrize(
    "data", [None, "unexpected", {}, {"sha256": False}, {"sha256": "not-a-hash"}]
)
def test_edit_rejects_missing_or_invalid_sha_without_mutating(command_sender, data):
    command_sender.return_value = {"success": True, "data": data}
    result = CliRunner().invoke(script_cli.script, ["edit", "Assets/Foo.cs", "--edits", "[]"])
    assert result.exit_code == 1
    assert json.loads(result.output)["success"] is False
    assert "SHA-256" in result.output
    assert command_sender.call_count == 1


def test_edit_keeps_failed_sha_lookup_and_does_not_mutate(command_sender):
    from cli.utils.connection import UnityCommandError

    failure = {"success": False, "message": "Script not found"}
    command_sender.side_effect = UnityCommandError(failure)
    result = CliRunner().invoke(script_cli.script, ["edit", "Assets/Foo.cs", "--edits", "[]"])
    assert result.exit_code == 1
    assert json.loads(result.output) == failure
    assert command_sender.call_count == 1


def test_validate_uses_unity_manage_script_action(command_sender):
    # Given: a script path and standard validation.
    # When: the CLI executes validation.
    result = CliRunner().invoke(
        script_cli.script, ["validate", "Assets/Scripts/Foo.cs", "--level", "standard"]
    )
    # Then: the supported Unity action gets the name/directory locator.
    assert result.exit_code == 0, result.output
    assert command_sender.call_args.args[:2] == (
        "manage_script",
        {"action": "validate", "name": "Foo", "path": "Assets/Scripts", "level": "standard"},
    )


def test_read_respects_requested_line_window(command_sender):
    # Given: Unity's read action returns the full source.
    # When: the CLI requests only the second line.
    result = CliRunner().invoke(
        script_cli.script, ["read", "Assets/Foo.cs", "--start-line", "2", "--line-count", "1"]
    )
    # Then: output contains precisely the requested window, with no ignored wire fields.
    assert result.exit_code == 0, result.output
    import json

    assert json.loads(result.output)["data"]["contents"] == "second\n"
    assert command_sender.call_args.args[1] == {
        "action": "read",
        "name": "Foo",
        "path": "Assets",
        "start_line": 2,
        "line_count": 1,
    }


@pytest.mark.parametrize(
    "option,value", [("--start-line", "0"), ("--start-line", "-1"), ("--line-count", "0")]
)
def test_read_rejects_nonpositive_line_bounds(command_sender, option, value):
    # Given: a line bound that cannot name a 1-based window.
    # When: Click parses it.
    result = CliRunner().invoke(script_cli.script, ["read", "Assets/Foo.cs", option, value])
    # Then: validation fails before Unity is contacted.
    assert result.exit_code == 2
    command_sender.assert_not_called()


@pytest.mark.parametrize("encoded", [False, True])
def test_read_native_window_is_not_sliced_twice(command_sender, encoded):
    import base64

    contents = "한😀\r\n"
    data = {"lineWindowApplied": True, "sha256": "b" * 64, "contentsEncoded": encoded}
    if encoded:
        data["encodedContents"] = base64.b64encode(contents.encode()).decode()
    else:
        data["contents"] = contents
    command_sender.return_value = {"success": True, "data": data}
    result = CliRunner().invoke(
        script_cli.script, ["read", "Assets/Foo.cs", "--start-line", "2", "--line-count", "1"]
    )
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)["data"]
    assert response["contents"] == contents
    assert response["sha256"] == "b" * 64
    if encoded:
        assert base64.b64decode(response["encodedContents"]).decode() == contents


def test_edit_forwards_observed_sha_without_a_fresh_lookup(command_sender):
    sha = "AB" * 32
    command_sender.return_value = {"success": True, "data": {}}
    result = CliRunner().invoke(
        script_cli.script, ["edit", "Assets/Foo.cs", "--edits", "[]", "--expected-sha", sha]
    )
    assert result.exit_code == 0, result.output
    assert command_sender.call_count == 1
    assert command_sender.call_args.args[1]["precondition_sha256"] == sha


def test_edit_rejects_invalid_expected_sha_before_contacting_unity(command_sender):
    result = CliRunner().invoke(
        script_cli.script, ["edit", "Assets/Foo.cs", "--edits", "[]", "--expected-sha", "bad"]
    )
    assert result.exit_code == 1
    command_sender.assert_not_called()


@pytest.mark.parametrize("format_type", ["json", "text"])
def test_legacy_encoded_read_preserves_unicode_and_crlf(monkeypatch, command_sender, format_type):
    import base64

    monkeypatch.setattr(script_cli, "get_config", lambda: CLIConfig(format=format_type))
    source = "one\r\n한😀\r\nlast"
    command_sender.return_value = {
        "success": True,
        "data": {
            "contentsEncoded": True,
            "encodedContents": base64.b64encode(source.encode()).decode(),
        },
    }
    result = CliRunner().invoke(
        script_cli.script, ["read", "Assets/Foo.cs", "--start-line", "2", "--line-count", "1"]
    )
    assert result.exit_code == 0, result.output
    if format_type == "json":
        data = json.loads(result.output)["data"]
        assert data["contents"] == "한😀\r\n"
        assert base64.b64decode(data["encodedContents"]).decode() == data["contents"]
    else:
        assert result.stdout_bytes == "한😀\r\n".encode("utf-8") + b"\n"


def test_edit_surfaces_stale_file_from_observed_sha(command_sender):
    from cli.utils.connection import UnityCommandError

    failure = {"success": False, "error": "stale_file", "data": {"status": "stale_file"}}
    command_sender.side_effect = UnityCommandError(failure)
    result = CliRunner().invoke(
        script_cli.script, ["edit", "Assets/Foo.cs", "--edits", "[]", "--expected-sha", "a" * 64]
    )
    assert result.exit_code == 1
    assert json.loads(result.output) == failure
    assert command_sender.call_count == 1


@pytest.mark.parametrize("command", ["code", "script"])
@pytest.mark.parametrize("format_type", ["text", "json"])
def test_read_preserves_crlf_in_real_stdout_subprocess(command, format_type):
    import os
    import subprocess
    import sys

    program = r"""
import importlib
import sys
from cli.utils.config import CLIConfig
module = importlib.import_module('cli.commands.' + sys.argv[1])
module.get_config = lambda: CLIConfig(format=sys.argv[2])
module.run_command = lambda *args: {'success': True, 'data': {'contents': 'first\r\n한😀\r\n'}}
getattr(module, sys.argv[1]).main(args=['read', 'Assets/Foo.cs', '-s', '2', '-n', '1'])
"""
    process = subprocess.run(
        [sys.executable, "-c", program, command, format_type],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    assert process.returncode == 0, process.stderr.decode("utf-8", "replace")
    if format_type == "json":
        assert json.loads(process.stdout) == {"success": True, "data": {"contents": "한😀\r\n"}}
    else:
        assert process.stdout == "한😀\r\n".encode("utf-8") + b"\n"
