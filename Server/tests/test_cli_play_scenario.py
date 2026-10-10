"""Public CLI validation and single REST request contracts for play scenarios."""

import json

import httpx
import pytest
from cli.main import cli
from cli.utils import connection
from click.testing import CliRunner

from .test_manage_play_scenario import DEFINITION, JOB_ID


@pytest.fixture
def wire(monkeypatch):
    # Given: the real HTTP helper talks to an inert boundary, never a live server.
    calls = []
    reply = {"success": True, "data": {"job_id": JOB_ID, "status": "queued"}}
    client_type = httpx.AsyncClient

    def respond(request):
        assert request.url.path == "/api/command"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    monkeypatch.setattr(connection, "read_local_auth_token", lambda host, port: "fixture")
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda: client_type(
            transport=httpx.MockTransport(respond),
            trust_env=False,
        ),
    )
    return calls, reply


@pytest.mark.parametrize(
    "action,args,params",
    [
        ("get", ["menu-start"], {"name": "menu-start"}),
        ("list", [], {}),
        ("delete", ["menu-start"], {"name": "menu-start"}),
        ("run", ["menu-start"], {"name": "menu-start", "repeat_count": 1, "timeout_seconds": 300}),
        (
            "run",
            ["menu-start", "--job-id", JOB_ID, "--repeat-count", "2", "--timeout-seconds", "60"],
            {"name": "menu-start", "job_id": JOB_ID, "repeat_count": 2, "timeout_seconds": 60},
        ),
        ("status", [JOB_ID], {"job_id": JOB_ID}),
        ("cancel", [JOB_ID], {"job_id": JOB_ID}),
    ],
)
def test_cli_action_preserves_selector_and_dispatches_once(wire, action, args, params):
    # Given: a valid command and selected editor.
    calls, reply = wire
    # When: the public registered command executes.
    result = CliRunner().invoke(
        cli, ["--instance", "Selected@owned", "--format", "json", "play-scenario", action, *args]
    )
    # Then: output is the original structured report and there is one request only.
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == reply
    assert calls == [
        {
            "type": "manage_play_scenario",
            "params": {"action": action, **params},
            "unity_instance": "Selected@owned",
        }
    ]


def test_cli_save_validates_whole_definition_before_dispatch(wire, tmp_path):
    # Given: an authored definition file.
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(DEFINITION), encoding="utf-8")
    # When: save is invoked through the public CLI.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", "save", str(path)])
    # Then: nested validated steps and defaults survive the single request.
    assert result.exit_code == 0, result.output
    calls, reply = wire
    assert len(calls) == 1
    assert calls[0]["params"]["scenario"]["steps"][1] == {
        "name": "Start",
        "action": "click_ui",
        "target": "Canvas/Start",
        "timeout_seconds": 30,
    }
    assert json.loads(result.stdout) == reply


@pytest.mark.parametrize(
    "args",
    [
        ["run", "Menu"],
        ["run", "menu-start", "--repeat-count", "0"],
        ["run", "menu-start", "--timeout-seconds", "1801"],
        ["run", "menu-start", "--job-id", "short"],
        ["status", "short"],
        ["cancel", JOB_ID.upper()],
        ["get", "../unsafe"],
        ["list", "--job-id", JOB_ID],
    ],
)
def test_invalid_cli_arguments_have_no_request_or_stdout(wire, args):
    # Given: invalid bounds, selectors or incompatible options.
    # When: Click and the shared command boundary parse them.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", *args])
    # Then: machine stdout remains clean and Unity sees no side effect.
    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr
    assert not wire[0]


@pytest.mark.parametrize("raw", ["{", "[]", '{"name":"menu","steps":[],"extra":true}'])
def test_invalid_definition_file_has_no_request(wire, tmp_path, raw):
    # Given: malformed JSON or definition fields.
    path = tmp_path / "invalid.json"
    path.write_text(raw, encoding="utf-8")
    # When: save parses the whole file before HTTP.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", "save", str(path)])
    # Then: diagnostics are on stderr and no request is sent.
    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr
    assert not wire[0]


def test_cli_unity_failure_keeps_full_machine_report(wire):
    # Given: a failed Unity step report.
    calls, reply = wire
    reply.update(
        success=False,
        error="scenario_failed",
        data={"job_id": JOB_ID, "status": "failed", "logs": [{"message": "Player missing"}]},
    )
    # When: status reads it once.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", "status", JOB_ID])
    # Then: the CLI exits unsuccessfully but retains the original structured diagnostics.
    assert result.exit_code == 1
    assert json.loads(result.stdout) == reply
    assert len(calls) == 1
