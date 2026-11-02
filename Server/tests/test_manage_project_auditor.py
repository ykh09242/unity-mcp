"""Public Project Auditor dispatch validation and observation semantics."""

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from services.tools.manage_project_auditor import manage_project_auditor
from cli.commands.project_auditor import project_auditor

AUDIT_ID = "a" * 32


@pytest.fixture
def transport(monkeypatch):
    sender = AsyncMock(return_value={"success": True, "data": {"state": "completed"}})
    monkeypatch.setattr(
        "services.tools.manage_project_auditor.get_unity_instance_from_context",
        AsyncMock(return_value="selected-editor"),
    )
    monkeypatch.setattr("services.tools.manage_project_auditor.send_with_unity_instance", sender)
    return sender


def call(**kwargs):
    return asyncio.run(manage_project_auditor(SimpleNamespace(), **kwargs))


def test_start_allocates_one_identity_before_transport(transport):
    assert call(action="start")["success"]
    args = transport.call_args.args
    assert args[1:3] == ("selected-editor", "manage_project_auditor")
    assert re.fullmatch("[a-f0-9]{32}", args[3]["audit_id"])
    assert args[3]["action"] == "start"


@pytest.mark.parametrize("state", ["completed", "failed", "unavailable", "interrupted", "busy"])
def test_native_outcomes_are_preserved_without_restarting(transport, state):
    response = {"success": True, "data": {"audit_id": AUDIT_ID, "state": state}}
    transport.return_value = response
    assert call(action="status", audit_id=AUDIT_ID) == response
    assert transport.call_args.args[3] == {"action": "status", "audit_id": AUDIT_ID}


@pytest.mark.parametrize(
    "params",
    [
        {"action": "cancel", "audit_id": AUDIT_ID},
        {"action": "status"},
        {"action": "issues", "audit_id": "foreign"},
        {"action": "issues", "audit_id": AUDIT_ID, "page_size": True},
        {"action": "issues", "audit_id": AUDIT_ID, "page_size": 101},
        {"action": "issues", "audit_id": AUDIT_ID, "cursor": "x" * 129},
        {"action": "status", "audit_id": AUDIT_ID, "page_size": 5},
        {"action": "availability", "audit_id": AUDIT_ID},
    ],
)
def test_invalid_requests_do_not_dispatch(transport, params):
    assert call(**params)["success"] is False
    transport.assert_not_awaited()


def test_paged_issue_read_forwards_only_the_owned_query(transport):
    call(action="issues", audit_id=AUDIT_ID, page_size=25, cursor=AUDIT_ID + ":25")
    assert transport.call_args.args[3] == {
        "action": "issues",
        "audit_id": AUDIT_ID,
        "page_size": 25,
        "cursor": AUDIT_ID + ":25",
    }


def test_availability_does_not_allocate_scan_identity(transport):
    call(action="availability")
    assert transport.call_args.args[3] == {"action": "availability"}


def test_cli_start_reuses_explicit_identity(monkeypatch):
    calls = []

    def send(command, params, config):
        calls.append((command, params))
        return {"success": True, "data": {"audit_id": params["audit_id"], "state": "queued"}}

    monkeypatch.setattr("cli.commands.project_auditor.run_command", send)
    result = CliRunner().invoke(project_auditor, ["start", "--audit-id", AUDIT_ID])
    assert result.exit_code == 0, result.output
    assert calls == [("manage_project_auditor", {"action": "start", "audit_id": AUDIT_ID})]


def test_cli_issue_pages_are_read_only_and_bounded(monkeypatch):
    calls = []

    def send(command, params, config):
        calls.append(params)
        return {"success": True, "data": {"items": [], "has_more": False}}

    monkeypatch.setattr("cli.commands.project_auditor.run_command", send)
    result = CliRunner().invoke(project_auditor, ["issues", AUDIT_ID, "--page-size", "100"])
    assert result.exit_code == 0, result.output
    assert calls == [{"action": "issues", "audit_id": AUDIT_ID, "page_size": 100}]
    rejected = CliRunner().invoke(project_auditor, ["issues", AUDIT_ID, "--page-size", "101"])
    assert rejected.exit_code == 2
    assert len(calls) == 1


def test_transport_interruption_retains_the_original_start_identity(transport):
    transport.side_effect = TimeoutError()
    result = call(action="start", audit_id=AUDIT_ID)
    assert result["code"] == "project_auditor_transport_unknown"
    assert result["audit_id"] == AUDIT_ID
    assert transport.await_count == 1


@pytest.mark.parametrize("failure", ["connection", "command", "timeout"])
def test_cli_generated_identity_survives_dispatch_uncertainty(monkeypatch, failure):
    from cli.utils.connection import UnityCommandError, UnityConnectionError

    captured = []
    config = SimpleNamespace(format="json")
    monkeypatch.setattr("cli.commands.project_auditor.get_config", lambda: config)
    monkeypatch.setattr("cli.utils.connection.get_config", lambda: config)

    def send(command, params, selected):
        captured.append(params.copy())
        match failure:
            case "connection":
                raise UnityConnectionError("timed out after dispatch")
            case "command":
                raise UnityCommandError(
                    {
                        "success": False,
                        "code": "outcome_unknown",
                        "error": "Native outcome unknown.",
                    }
                )
            case "timeout":
                raise TimeoutError()

    monkeypatch.setattr("cli.commands.project_auditor.run_command", send)
    result = CliRunner().invoke(project_auditor, ["start"])
    assert result.exit_code == 1, result.output
    response = json.loads(result.output)
    assert len(captured) == 1
    assert re.fullmatch("[a-f0-9]{32}", captured[0]["audit_id"])
    assert response["audit_id"] == captured[0]["audit_id"]
    assert "status" in response["observation_hint"]


@pytest.mark.parametrize("as_model", [False, True])
def test_generated_identity_survives_real_transport_response_envelopes(transport, as_model):
    from models import MCPResponse

    payload = {
        "success": False,
        "error": "Unity command deadline expired while observing its original invocation",
        "hint": "inspect_state_before_retry",
        "data": {"reason": "outcome_unknown", "command": "manage_project_auditor"},
    }
    transport.return_value = MCPResponse(**payload) if as_model else payload
    result = call(action="start")
    assert isinstance(result, dict)
    assert result["audit_id"] == transport.call_args.args[3]["audit_id"]
    assert result["data"] == payload["data"]
    assert result["hint"] == payload["hint"]
    assert transport.await_count == 1
