"""CLI regressions using real HTTP decoding and Unity's dispatcher envelope."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture
def command_response(monkeypatch):
    requests = []
    response = {"success": True, "data": {}}
    client_type = httpx.AsyncClient

    def respond(request):
        if request.method == "GET":
            requests.append({"method": "GET", "path": request.url.path})
            return httpx.Response(200, json={"success": True, "tools": [{"name": "BuildPipeline"}]})
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    monkeypatch.setattr(
        connection.httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    return response, requests


@pytest.mark.parametrize(
    "command",
    [
        ["asset", "info", "Assets/Missing.asset"],
        ["component", "add", "Cube", "MissingComponent"],
        ["raw", "manage_asset", '{"action":"get_info"}'],
    ],
)
@pytest.mark.parametrize("wrapped", [False, True])
def test_domain_failure_exits_nonzero_and_preserves_json(command_response, command, wrapped):
    response, requests = command_response
    failure = {"success": False, "error": "Target not found", "data": {"target": "Missing"}}
    response.clear()
    response.update({"status": "success", "result": failure} if wrapped else failure)
    result = CliRunner().invoke(cli, ["--format", "json", *command])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == failure
    assert len(requests) == 1


def test_dispatcher_error_exits_nonzero(command_response):
    response, _ = command_response
    response.clear()
    response.update({"status": "error", "error": "Tool disabled"})
    result = CliRunner().invoke(cli, ["--format", "json", "raw", "disabled"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "Tool disabled"


def test_success_envelope_is_normalized_and_keeps_instance(command_response):
    response, requests = command_response
    response.clear()
    response.update(
        {"status": "success", "result": {"success": True, "data": {"path": "Assets/A.asset"}}}
    )
    result = CliRunner().invoke(
        cli, ["--instance", "Project@abc", "--format", "json", "asset", "info", "Assets/A.asset"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response["result"]
    assert requests[0]["unity_instance"] == "Project@abc"


@pytest.mark.parametrize("mode", ["run", "inline"])
@pytest.mark.parametrize("count", [41, 100])
def test_batch_accepts_editor_supported_sizes(command_response, tmp_path, mode, count):
    _, requests = command_response
    commands = [{"tool": "manage_scene", "params": {"action": "get_active"}}] * count
    path = tmp_path / "commands.json"
    path.write_text(json.dumps(commands))
    argument = str(path) if mode == "run" else json.dumps(commands)
    result = CliRunner().invoke(cli, ["batch", mode, argument])
    assert result.exit_code == 0, result.output
    assert requests[0]["params"]["commands"] == commands


@pytest.mark.parametrize("mode", ["run", "inline"])
def test_batch_failure_preserves_partial_results(command_response, tmp_path, mode):
    response, _ = command_response
    failure = {
        "success": False,
        "error": "One command failed",
        "data": {
            "results": [
                {
                    "tool": "manage_scene",
                    "callSucceeded": False,
                    "result": {"success": False, "error": "Failed"},
                }
            ],
            "callSuccessCount": 0,
            "callFailureCount": 1,
        },
    }
    response.clear()
    response.update({"status": "success", "result": failure})
    commands = [{"tool": "manage_scene", "params": {}}]
    path = tmp_path / "commands.json"
    path.write_text(json.dumps(commands))
    argument = str(path) if mode == "run" else json.dumps(commands)
    result = CliRunner().invoke(cli, ["--format", "json", "batch", mode, argument])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == failure


def test_batch_run_counts_call_success_and_null_results(command_response, tmp_path):
    response, _ = command_response
    response["data"] = {
        "results": [{"tool": "custom", "callSucceeded": True, "result": None}],
        "callSuccessCount": 1,
        "callFailureCount": 0,
    }
    path = tmp_path / "commands.json"
    path.write_text('[{"tool":"custom","params":{}}]')
    result = CliRunner().invoke(cli, ["batch", "run", str(path)])
    assert result.exit_code == 0, result.output
    assert "All 1 commands completed successfully" in result.output


def test_batch_run_json_is_one_document(command_response, tmp_path):
    response, _ = command_response
    response["data"] = {"results": []}
    path = tmp_path / "commands.json"
    path.write_text('[{"tool":"custom","params":{}}]')
    result = CliRunner().invoke(cli, ["--format", "json", "batch", "run", str(path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response


@pytest.mark.parametrize(
    "command",
    [
        ["gameobject", "find", "Player"],
        ["gameobject", "create", "Player"],
        ["scene", "active"],
        ["scene", "save"],
        ["editor", "play"],
        ["asset", "mkdir", "Assets/New"],
        ["component", "set", "Cube", "Transform", "position", "[1,2,3]"],
    ],
)
def test_other_domains_propagate_operation_failure(command_response, command):
    response, requests = command_response
    response.clear()
    response.update(
        {"status": "success", "result": {"success": False, "error": "Operation failed"}}
    )
    result = CliRunner().invoke(cli, command)
    assert result.exit_code == 1, result.output
    assert "Operation failed" in result.output
    assert len(requests) == 1


def test_custom_tool_failure_retains_suggestions(command_response):
    response, requests = command_response
    response.clear()
    response.update({"success": False, "error": "Tool BuildPipline not found"})
    result = CliRunner().invoke(cli, ["editor", "custom-tool", "BuildPipline"])
    assert result.exit_code == 1, result.output
    assert "BuildPipeline" in result.output
    assert requests[1] == {"method": "GET", "path": "/api/custom-tools"}


@pytest.mark.parametrize("mode", ["run", "inline"])
def test_batch_over_hard_ceiling_does_not_send(command_response, tmp_path, mode):
    _, requests = command_response
    commands = [{"tool": "manage_scene", "params": {}}] * 101
    path = tmp_path / "commands.json"
    path.write_text(json.dumps(commands))
    argument = str(path) if mode == "run" else json.dumps(commands)
    result = CliRunner().invoke(cli, ["batch", mode, argument])
    assert result.exit_code == 1
    assert "Maximum 100" in result.output
    assert requests == []


def test_raw_custom_payload_without_status_is_not_failure(command_response):
    response, _ = command_response
    response.clear()
    response.update({"payload": "custom result"})
    result = CliRunner().invoke(cli, ["--format", "json", "raw", "custom"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == response


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [{"name": "First"}, {"name": "Second", "diagnostic": "LaterField"}],
            ["First", "Second", "diagnostic", "LaterField"],
        ),
        ([["First"], ["Second", "LaterCell"]], ["First", "Second", "LaterCell"]),
        ([[], ["LaterCell"]], ["LaterCell"]),
        ([{"name": "First"}, None, ["LaterCell"], 42], ["First", "None", "LaterCell", "42"]),
    ],
)
def test_raw_table_preserves_heterogeneous_visible_rows(command_response, rows, expected):
    response, requests = command_response
    response["data"] = rows
    result = CliRunner().invoke(
        cli,
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
            "--timeout",
            "30",
            "--instance",
            "Owned@fixture",
            "--format",
            "table",
            "raw",
            "owned_tool",
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    assert result.stderr == ""
    for value in expected:
        assert value in result.stdout, result.stdout
    assert requests == [{"type": "owned_tool", "params": {}, "unity_instance": "Owned@fixture"}]


@pytest.mark.parametrize("failure", ["http", "connect", "timeout"])
def test_transport_errors_still_exit_nonzero(monkeypatch, failure):
    client_type = httpx.AsyncClient

    def respond(request):
        if failure == "connect":
            raise httpx.ConnectError("Unavailable", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Too slow", request=request)
        return httpx.Response(503, json={"success": False, "error": "No Unity connected"})

    monkeypatch.setattr(
        connection.httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    result = CliRunner().invoke(cli, ["scene", "active"])
    assert result.exit_code == 1
    assert {"http": "HTTP error", "connect": "Cannot connect", "timeout": "timed out"}[
        failure
    ] in result.output
