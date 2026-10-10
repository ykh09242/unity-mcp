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
    reply = {"success": True, "data": {"job_id": JOB_ID, "status": "running"}}
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


@pytest.mark.parametrize("action", ["status", "run"])
@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
def test_cli_native_terminal_failure_keeps_full_machine_report(wire, action, status):
    # Given: native status and idempotent run retries return a successful query envelope.
    calls, reply = wire
    reply.update(
        message="Existing scenario job." if action == "run" else "Scenario job status.",
        data={
            "job_id": JOB_ID,
            "status": status,
            "error": "Run did not complete.",
            "steps": [{"name": "Start", "status": status, "detail": "Player missing"}],
            "logs": [{"message": "Player missing"}],
        },
    )
    args = ["menu-start", "--job-id", JOB_ID] if action == "run" else [JOB_ID]
    # When: the command observes the terminal job in one response.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", action, *args])
    # Then: failed jobs produce a failure exit without replacing their native diagnostics.
    assert result.exit_code == 1
    assert result.stderr == ""
    assert json.loads(result.stdout) == reply
    assert len(calls) == 1
    assert calls[0]["params"]["action"] == action
    assert calls[0]["params"]["job_id"] == JOB_ID


@pytest.mark.parametrize("action", ["status", "run"])
@pytest.mark.parametrize("status", ["accepted", "running", "succeeded"])
def test_cli_nonfailure_job_status_exits_successfully(wire, action, status):
    # Given: an accepted job, ongoing execution, or a successful completed run.
    calls, reply = wire
    reply["data"]["status"] = status
    args = ["menu-start", "--job-id", JOB_ID] if action == "run" else [JOB_ID]
    # When: the CLI observes the job once, without waiting for completion.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", action, *args])
    # Then: submitting or observing a nonfailure remains successful.
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == reply
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["cancelled", "failed", "timed_out", "succeeded"])
def test_cli_successful_cancel_keeps_terminal_report_and_exit_zero(wire, status):
    # Given: cancellation succeeded or an idempotent cancel returns an existing terminal job.
    calls, reply = wire
    reply["data"]["status"] = status
    # When: the caller intentionally requests cancellation rather than run success.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", "cancel", JOB_ID])
    # Then: the command succeeds while preserving the job's reported outcome.
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == reply
    assert len(calls) == 1
    assert calls[0]["params"] == {"action": "cancel", "job_id": JOB_ID}


@pytest.mark.parametrize("action", ["status", "run", "cancel"])
@pytest.mark.parametrize("failure", ["http", "connect", "timeout"])
def test_cli_job_transport_failure_keeps_stderr_and_single_request(monkeypatch, action, failure):
    # Given: the request boundary fails rather than returning a native job report.
    calls = []
    client_type = httpx.AsyncClient

    def respond(request):
        calls.append(json.loads(request.content))
        if failure == "connect":
            raise httpx.ConnectError("Unavailable", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Too slow", request=request)
        return httpx.Response(503, json={"error": "No Unity connected"})

    monkeypatch.setattr(connection, "read_local_auth_token", lambda host, port: "fixture")
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False),
    )
    args = ["menu-start", "--job-id", JOB_ID] if action == "run" else [JOB_ID]
    # When: a job command encounters a transport error.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", action, *args])
    # Then: existing transport diagnostics stay on stderr with no report or retry.
    assert result.exit_code == 1
    assert result.stdout == ""
    assert {"http": "HTTP error", "connect": "Cannot connect", "timeout": "timed out"}[
        failure
    ] in result.stderr
    assert len(calls) == 1


@pytest.mark.parametrize("name", [None, "menu-start"])
def test_cli_reports_preserves_failed_history_and_exits_zero(wire, name):
    # Given: a successful history query contains a failed retained job.
    calls, reply = wire
    reply["data"] = {"reports": [{"job_id": JOB_ID, "status": "failed"}]}
    args = ["--name", name] if name else []
    # When: the registered reports command requests history once.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", "reports", *args])
    # Then: query success does not depend on historical scenario outcomes.
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == reply
    assert calls[0]["params"] == {"action": "reports", **({"name": name} if name else {})}
    assert len(calls) == 1


def test_cli_save_preserves_expanded_lifecycle_conditions_and_options(wire, tmp_path):
    # Given: one complete authored definition includes all new options.
    from .test_play_scenario_hardening import HARDENED

    path = tmp_path / "expanded.json"
    path.write_text(json.dumps(HARDENED), encoding="utf-8")
    # When: the public CLI saves it once.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", "save", str(path)])
    # Then: false/zero conditions and each stage survive the actual HTTP boundary.
    assert result.exit_code == 0, result.output
    calls, reply = wire
    assert len(calls) == 1
    definition = calls[0]["params"]["scenario"]
    for field, value in HARDENED.items():
        if field.endswith("steps"):
            for expected, actual in zip(value, definition[field], strict=True):
                assert actual == {"timeout_seconds": 30, **expected}
        else:
            assert definition[field] == value
    assert json.loads(result.stdout) == reply


@pytest.mark.parametrize("args", [["--name", "../unsafe"], ["--job-id", JOB_ID], ["--limit", "1"]])
def test_cli_reports_invalid_arguments_have_no_request(wire, args):
    # Given: reports supports an optional canonical name and no additional wire options.
    # When: invalid history arguments enter the registered CLI.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", "reports", *args])
    # Then: diagnostics stay on stderr and no REST command is sent.
    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr
    assert not wire[0]


def test_cli_oversized_definition_has_no_request(wire, tmp_path):
    # Given: a valid-shaped definition exceeds the existing 64 KiB file budget.
    definition = {
        "name": "oversized",
        "steps": [DEFINITION["steps"][0]]
        + [{"name": "Player", "action": "wait_object", "target": "x" * 4096}] * 31,
    }
    path = tmp_path / "oversized.json"
    path.write_text(json.dumps(definition), encoding="utf-8")
    # When: save reads the authored file through the public CLI.
    result = CliRunner().invoke(cli, ["--format", "json", "play-scenario", "save", str(path)])
    # Then: size rejection precedes native execution and keeps machine stdout empty.
    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr
    assert not wire[0]
