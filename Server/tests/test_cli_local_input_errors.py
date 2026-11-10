"""Command input failures use the selected output format before any HTTP dispatch."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture
def requests(monkeypatch, tmp_path):
    for name in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv("UNITY_MCP_" + name, raising=False)
    monkeypatch.chdir(tmp_path)
    captured = []
    client_type = httpx.AsyncClient

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "data": {"count": 0}})

    monkeypatch.setattr(connection, "_auth_headers", lambda _config: {})
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond)),
    )
    return captured


@pytest.mark.parametrize(
    "command,code,diagnostic",
    [
        (["raw", "manage_scene", "not_json"], "invalid_json_params", "Invalid JSON params"),
        (["raw", "manage_scene", "[]"], "invalid_json_params", "expected an object"),
        (["code", "execute"], "missing_code", "Provide code as an argument or use --file."),
        (
            ["scene", "load", "not_an_index", "--by-index"],
            "invalid_scene_index",
            "Invalid build index: not_an_index",
        ),
    ],
)
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_invalid_input_is_local_and_uses_selected_error_stream(
    requests, command, code, diagnostic, output_format
):
    # Given: invalid input handled by a command callback rather than Click's parser.
    # When: the complete CLI runs in each supported output mode.
    result = CliRunner().invoke(cli, ["--format", output_format, *command])
    # Then: JSON has one structured document; human diagnostics remain on stderr.
    assert result.exit_code == 1, result.output
    assert requests == []
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        assert payload["code"] == code
        assert diagnostic in payload["error"]
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert diagnostic in result.stderr


@pytest.mark.parametrize(
    "command,tool,params",
    [
        (["raw", "owned_tool", '{"count":0}'], "owned_tool", {"count": 0}),
        (
            ["code", "execute", "return 0;"],
            "execute_code",
            {"action": "execute", "code": "return 0;", "safety_checks": True},
        ),
        (["scene", "load", "0", "--by-index"], "manage_scene", {"action": "load", "buildIndex": 0}),
    ],
)
def test_valid_inputs_preserve_single_json_response_and_exact_request(
    requests, command, tool, params
):
    # Given: valid raw, code and zero-valued scene-index input.
    # When: the real CLI dispatches the command.
    result = CliRunner().invoke(cli, ["--format", "json", *command])
    # Then: it emits one unchanged response and dispatches the original parameters once.
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"success": True, "data": {"count": 0}}
    assert requests == [{"type": tool, "params": params}]


@pytest.mark.parametrize("command", [["raw"], ["code", "execute"], ["scene", "load"]])
def test_command_help_remains_successful_without_dispatch(requests, command):
    # Given: JSON output is selected but the user asks for help.
    # When: Click renders a command's help.
    result = CliRunner().invoke(cli, ["--format", "json", *command, "--help"])
    # Then: normal human-readable help and success are preserved.
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.stdout
    assert result.stderr == ""
    assert requests == []


def test_environment_json_input_error_has_bounded_diagnostic(requests, monkeypatch):
    # Given: environment selects JSON and the rejected scene index is very long.
    monkeypatch.setenv("UNITY_MCP_FORMAT", "json")
    # When: the real command rejects that index locally.
    result = CliRunner().invoke(cli, ["scene", "load", "x" * 4000, "--by-index"])
    # Then: one bounded JSON error is emitted with no HTTP dispatch.
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["code"] == "invalid_scene_index"
    assert payload["error"].startswith("Invalid build index:")
    assert len(payload["error"]) <= 1000
    assert result.stderr == ""
    assert requests == []
