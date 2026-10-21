import importlib
import json
from unittest.mock import Mock

from click.testing import CliRunner
import pytest

from cli.utils.config import CLIConfig


@pytest.fixture
def editor_module(monkeypatch):
    module = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(module, "get_config", lambda: CLIConfig(format="json"))
    monkeypatch.setattr("cli.utils.connection.get_config", lambda: CLIConfig(format="json"))
    return module


@pytest.mark.parametrize("command", [["tests"], ["poll-test", "job-1"]])
def test_cli_test_job_details_use_unity_parameter_names(editor_module, monkeypatch, command):
    send = Mock(return_value={"success": True, "data": {"job_id": "job-1", "status": "succeeded"}})
    monkeypatch.setattr(editor_module, "run_command", send)

    result = CliRunner().invoke(editor_module.editor, command + ["--details", "--failed-only"])

    assert result.exit_code == 0, result.output
    assert send.call_args.args[1]["includeDetails"] is True
    assert send.call_args.args[1]["includeFailedTests"] is True


@pytest.mark.parametrize("command", [["tests"], ["poll-test", "job-1"]])
def test_cli_wait_polls_until_job_finishes(editor_module, monkeypatch, command):
    now = [0.0]
    monkeypatch.setattr(
        editor_module,
        "time",
        Mock(
            monotonic=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        ),
        raising=False,
    )
    statuses = (
        ["running", "running", "succeeded"] if command == ["tests"] else ["running", "succeeded"]
    )
    send = Mock(
        side_effect=[{"success": True, "data": {"job_id": "job-1", "status": s}} for s in statuses]
    )
    monkeypatch.setattr(editor_module, "run_command", send)

    result = CliRunner().invoke(editor_module.editor, command + ["--wait", "5"])

    assert result.exit_code == 0, result.output
    assert '"status": "succeeded"' in result.output
    assert send.call_count == len(statuses)
    assert all("wait_timeout" not in call.args[1] for call in send.call_args_list)


def test_cli_wait_stops_without_polling_after_deadline(editor_module, monkeypatch):
    now = [0.0]
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(
        editor_module, "time", Mock(monotonic=lambda: now[0], sleep=sleep), raising=False
    )
    send = Mock(return_value={"success": True, "data": {"job_id": "job-1", "status": "running"}})
    monkeypatch.setattr(editor_module, "run_command", send)

    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "1"])

    assert result.exit_code == 124, result.output
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["code"] == "test_wait_timeout"
    assert payload["data"]["status"] == "running"
    assert sleeps == [1]
    send.assert_called_once()


def test_cli_wait_returns_unity_errors_immediately(editor_module, monkeypatch):
    send = Mock(return_value={"success": False, "error": "Job not found"})
    monkeypatch.setattr(editor_module, "run_command", send)
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "missing", "--wait", "5"])
    assert "Job not found" in result.output
    send.assert_called_once()


def test_cli_wait_passes_remaining_budget_to_each_request(editor_module, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(
        editor_module,
        "time",
        Mock(
            monotonic=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        ),
    )
    send = Mock(
        side_effect=[
            {"success": True, "data": {"job_id": "job-1", "status": "running"}},
            {"success": True, "data": {"job_id": "job-1", "status": "succeeded"}},
        ]
    )
    monkeypatch.setattr(editor_module, "run_command", send)

    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "3"])

    assert result.exit_code == 0, result.output
    assert [call.kwargs["timeout"] for call in send.call_args_list] == [3.0, 1.0]


def test_cli_wait_does_not_accept_a_terminal_result_after_deadline(editor_module, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(
        editor_module,
        "time",
        Mock(
            monotonic=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        ),
    )
    responses = iter(["running", "succeeded"])

    def send(*args, **kwargs):
        status = next(responses)
        if status == "succeeded":
            now[0] += 2
        return {"success": True, "data": {"job_id": "job-1", "status": status}}

    monkeypatch.setattr(editor_module, "run_command", send)
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "3"])

    assert result.exit_code == 124, result.output
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["code"] == "test_wait_timeout"
    assert payload["data"]["status"] == "succeeded"


@pytest.mark.parametrize("data", [None, [], {"job_id": "job-1"}])
def test_cli_wait_does_not_poll_malformed_job_status(editor_module, monkeypatch, data):
    send = Mock(return_value={"success": True, "data": data})
    monkeypatch.setattr(editor_module, "run_command", send)
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "3"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["code"] == "invalid_test_job_response"
    send.assert_called_once()


def test_cli_no_wait_keeps_the_default_request_timeout(editor_module, monkeypatch):
    send = Mock(return_value={"success": True, "data": {"status": "running"}})
    monkeypatch.setattr(editor_module, "run_command", send)
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "0"])
    assert result.exit_code == 0, result.output
    assert send.call_args.kwargs == {}


def test_cli_async_tests_return_job_without_polling(editor_module, monkeypatch):
    send = Mock(return_value={"success": True, "data": {"job_id": "job-1", "status": "running"}})
    monkeypatch.setattr(editor_module, "run_command", send)
    result = CliRunner().invoke(editor_module.editor, ["tests", "--async", "--wait", "3"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == send.return_value
    send.assert_called_once()


def test_cli_async_tests_keep_text_job_guidance(editor_module, monkeypatch):
    monkeypatch.setattr(editor_module, "get_config", lambda: CLIConfig(format="text"))
    send = Mock(return_value={"success": True, "data": {"job_id": "job-1", "status": "running"}})
    monkeypatch.setattr(editor_module, "run_command", send)
    result = CliRunner().invoke(editor_module.editor, ["tests", "--async", "--wait", "3"])
    assert result.exit_code == 0, result.output
    assert "Test job started: job-1" in result.output
    assert "poll-test job-1" in result.output
    send.assert_called_once()


@pytest.mark.parametrize("command", [["tests"], ["poll-test", "job-1"]])
@pytest.mark.parametrize("status, expected", [("succeeded", 0), ("failed", 1), ("cancelled", 1)])
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_wait_exit_matches_observed_completion(
    editor_module, monkeypatch, command, status, expected, output_format
):
    # Given an acknowledged job followed by its terminal observation.
    monkeypatch.setattr(editor_module, "get_config", lambda: CLIConfig(format=output_format))
    terminal = {"success": True, "data": {"job_id": "job-1", "status": status}}
    replies = ([{"success": True, "data": {"job_id": "job-1"}}] if command == ["tests"] else []) + [
        terminal
    ]
    dispatches = []

    def send(name, params, config, **kwargs):
        dispatches.append(name)
        return replies.pop(0)

    monkeypatch.setattr(editor_module, "run_command", send)
    # When the real CLI waits for completion.
    result = CliRunner().invoke(editor_module.editor, command + ["--wait", "5"])
    # Then the process outcome reflects the job, without restarting or cancelling it.
    assert result.exit_code == expected, result.output
    assert dispatches == (
        ["run_tests", "get_test_job"] if command == ["tests"] else ["get_test_job"]
    )
    if output_format == "json":
        assert json.loads(result.output) == terminal


@pytest.mark.parametrize("status", ["running", "failed", "cancelled"])
def test_status_only_query_keeps_successful_observation_exit(editor_module, monkeypatch, status):
    # Given a readable job, including an unsuccessful job outcome.
    payload = {"success": True, "data": {"job_id": "job-1", "status": status}}
    monkeypatch.setattr(editor_module, "run_command", lambda *args, **kwargs: payload)
    # When the caller requests one status observation.
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "0"])
    # Then observation success is independent of completion success.
    assert result.exit_code == 0
    assert json.loads(result.output) == payload


@pytest.mark.parametrize("status", ["running", "succeeded"])
def test_first_response_after_wait_deadline_preserves_observation(
    editor_module, monkeypatch, status
):
    # Given a first response arriving exactly at the requested deadline.
    now = [0.0]
    monkeypatch.setattr(editor_module, "time", Mock(monotonic=lambda: now[0]))

    def send(*args, **kwargs):
        now[0] = 1.0
        return {"success": True, "data": {"job_id": "job-1", "status": status}}

    monkeypatch.setattr(editor_module, "run_command", send)
    # When the wait expires during the first read.
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "1"])
    # Then expiry is nonzero, with the last observation and no implied cancellation.
    assert result.exit_code == 124
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["code"] == "test_wait_timeout"
    assert payload["data"]["status"] == status


def test_wait_start_without_job_identity_fails_once(editor_module, monkeypatch):
    # Given an invalid acknowledgement that cannot be observed.
    calls = []

    def send(name, *args, **kwargs):
        calls.append(name)
        return {"success": True, "data": {"status": "running"}}

    monkeypatch.setattr(editor_module, "run_command", send)
    # When tests are started with an explicit wait.
    result = CliRunner().invoke(editor_module.editor, ["tests", "--wait", "5"])
    # Then the CLI refuses to claim completion and never repeats the start.
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "invalid_test_job_response"
    assert calls == ["run_tests"]


@pytest.mark.parametrize("command", [["tests"], ["poll-test", "job-1"]])
def test_wait_transport_error_is_one_json_document(editor_module, monkeypatch, command):
    # Given a failed HTTP read through the real CLI command boundary.
    from cli.utils import connection
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        connection,
        "send_command",
        AsyncMock(side_effect=connection.UnityConnectionError("connection timed out")),
    )
    # When the real CLI starts or observes a job.
    result = CliRunner().invoke(editor_module.editor, command + ["--wait", "5"])
    # Then callers receive a parseable failure and cannot mistake it for success.
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["code"] == "test_job_transport_error"
    assert result.stderr == ""


@pytest.mark.parametrize("command", [["tests"], ["poll-test", "job-1"]])
@pytest.mark.parametrize("response", [None, [], "invalid", {"data": {}}, {"success": "true"}])
def test_wait_malformed_response_is_explicit_failure(editor_module, monkeypatch, command, response):
    # Given malformed input through real run_command (only the network read is replaced).
    from cli.utils import connection
    from unittest.mock import AsyncMock

    monkeypatch.setattr(connection, "send_command", AsyncMock(return_value=response))
    # When a waiting command receives that input.
    result = CliRunner().invoke(editor_module.editor, command + ["--wait", "5"])
    # Then it returns a machine-readable invalid-observation failure.
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "invalid_test_job_response"


def test_wait_whitespace_status_is_invalid_without_sleep(editor_module, monkeypatch):
    # Given an observation without a meaningful status.
    monkeypatch.setattr(
        editor_module, "run_command", lambda *a, **kw: {"success": True, "data": {"status": " "}}
    )
    clock = Mock(monotonic=lambda: 0, sleep=Mock(side_effect=AssertionError("must not wait")))
    monkeypatch.setattr(editor_module, "time", clock)
    # When polling for completion.
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "5"])
    # Then malformed observation fails immediately instead of consuming the wait budget.
    assert result.exit_code == 1
    assert json.loads(result.stdout)["code"] == "invalid_test_job_response"
