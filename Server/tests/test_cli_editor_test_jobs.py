import importlib
from unittest.mock import Mock

from click.testing import CliRunner
import pytest

from cli.utils.config import CLIConfig


@pytest.fixture
def editor_module(monkeypatch):
    module = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(module, "get_config", lambda: CLIConfig(format="json"))
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
    monkeypatch.setattr(editor_module, "time", Mock(
        monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay),
    ), raising=False)
    statuses = ["running", "running", "succeeded"] if command == ["tests"] else ["running", "succeeded"]
    send = Mock(side_effect=[{"success": True, "data": {"job_id": "job-1", "status": s}} for s in statuses])
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

    monkeypatch.setattr(editor_module, "time", Mock(monotonic=lambda: now[0], sleep=sleep), raising=False)
    send = Mock(return_value={"success": True, "data": {"job_id": "job-1", "status": "running"}})
    monkeypatch.setattr(editor_module, "run_command", send)

    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "1"])

    assert result.exit_code == 0, result.output
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
    monkeypatch.setattr(editor_module, "time", Mock(
        monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay),
    ))
    send = Mock(side_effect=[
        {"success": True, "data": {"job_id": "job-1", "status": "running"}},
        {"success": True, "data": {"job_id": "job-1", "status": "succeeded"}},
    ])
    monkeypatch.setattr(editor_module, "run_command", send)

    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "3"])

    assert result.exit_code == 0, result.output
    assert [call.kwargs["timeout"] for call in send.call_args_list] == [3.0, 1.0]


def test_cli_wait_does_not_accept_a_terminal_result_after_deadline(editor_module, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(editor_module, "time", Mock(
        monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay),
    ))
    responses = iter(["running", "succeeded"])

    def send(*args, **kwargs):
        status = next(responses)
        if status == "succeeded":
            now[0] += 2
        return {"success": True, "data": {"job_id": "job-1", "status": status}}

    monkeypatch.setattr(editor_module, "run_command", send)
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "3"])

    assert result.exit_code == 0, result.output
    assert '"status": "running"' in result.output
    assert '"status": "succeeded"' not in result.output


@pytest.mark.parametrize("data", [None, [], {"job_id": "job-1"}])
def test_cli_wait_does_not_poll_malformed_job_status(editor_module, monkeypatch, data):
    send = Mock(return_value={"success": True, "data": data})
    monkeypatch.setattr(editor_module, "run_command", send)
    result = CliRunner().invoke(editor_module.editor, ["poll-test", "job-1", "--wait", "3"])
    assert result.exit_code == 0, result.output
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
    assert "Test job started: job-1" in result.output
    send.assert_called_once()
