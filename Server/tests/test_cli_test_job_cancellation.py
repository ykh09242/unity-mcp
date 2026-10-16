"""Actual Click command parsing and native-boundary dispatch contracts."""

import importlib
import json
from unittest.mock import Mock

from click.testing import CliRunner
import pytest

from cli.utils.config import CLIConfig


@pytest.mark.parametrize("supported", [True, False])
def test_cancel_cli_preserves_accepted_or_unsupported_result(monkeypatch, supported):
    module = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(module, "get_config", lambda: CLIConfig(format="json"))
    response = (
        {
            "success": supported,
            "data": {
                "job_id": "job",
                "status": "running",
                "native_control": {"termination": "cleanup_pending"},
            },
        }
        if supported
        else {"success": False, "error": "unsupported_native_test_control"}
    )
    send = Mock(return_value=response)
    monkeypatch.setattr(module, "run_command", send)
    result = CliRunner().invoke(module.editor, ["cancel-test", "job"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == response
    assert send.call_args.args[0:2] == ("cancel_test_job", {"job_id": "job"})
    send.assert_called_once()


def test_native_deadline_cli_forwards_opt_in_without_wait(monkeypatch):
    module = importlib.import_module("cli.commands.editor")
    monkeypatch.setattr(module, "get_config", lambda: CLIConfig(format="json"))
    send = Mock(return_value={"success": True, "data": {"job_id": "job", "status": "running"}})
    monkeypatch.setattr(module, "run_command", send)
    result = CliRunner().invoke(module.editor, ["tests", "--async", "--run-timeout-ms", "250"])
    assert result.exit_code == 0, result.output
    assert send.call_args.args[1] == {"mode": "EditMode", "runTimeoutMs": 250}
    send.assert_called_once()
