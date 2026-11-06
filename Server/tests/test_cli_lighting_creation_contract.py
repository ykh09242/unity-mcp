"""Exercise the real Click command with only its Unity transport replaced."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from click.testing import CliRunner

from cli.commands.lighting import lighting
from cli.main import cli
from cli.utils import connection
from cli.utils.config import CLIConfig
from cli.utils.connection import UnityConnectionError


@pytest.fixture(autouse=True)
def isolated_cli_config(monkeypatch):
    """The error decorator reads shared config independently of the command getter."""
    monkeypatch.setattr("cli.utils.config._config", CLIConfig(format="json"))


@pytest.mark.parametrize("created_id", [-204, 204])
def test_light_creation_configures_returned_id_instead_of_colliding_name(created_id):
    calls = []

    async def send(command, params, config=None, timeout=None):
        calls.append((command, params))
        if command == "manage_gameobject":
            return {"success": True, "data": {"instanceID": created_id, "name": "SharedName"}}
        return {"success": True}

    with (
        patch("cli.commands.lighting.get_config", return_value=CLIConfig(format="json")),
        patch("cli.utils.connection.send_command", side_effect=send),
    ):
        result = CliRunner().invoke(
            lighting, ["create", "SharedName", "--color", "0", "0", "0", "--intensity", "0"]
        )

    assert result.exit_code == 0, result.output
    assert len(calls) == 5
    assert all(params["target"] == created_id for _, params in calls[1:]), calls
    assert all(params["search_method"] == "by_id" for _, params in calls[1:])
    assert calls[-1][1]["value"] == 0
    assert calls[-2][1]["value"] == {"r": 0, "g": 0, "b": 0, "a": 1}


@pytest.mark.parametrize("data", [None, {}, {"instanceID": None}])
def test_light_creation_without_returned_id_stops_before_component_writes(data):
    with (
        patch("cli.commands.lighting.get_config", return_value=CLIConfig(format="json")),
        patch(
            "cli.utils.connection.send_command",
            new_callable=AsyncMock,
            return_value={"success": True, "data": data},
        ) as send,
    ):
        result = CliRunner().invoke(lighting, ["create", "SharedName"])

    assert result.exit_code == 1
    assert send.call_count == 1
    assert "instanceID" in result.output
    progress = json.loads(result.stdout)["lightingCreation"]
    assert progress["createdObject"] == data
    assert progress["completedSteps"] == ["create_gameobject"]
    assert progress["failedStep"] == "resolve_created_identity"


def test_failed_light_creation_stops_before_component_writes():
    with (
        patch("cli.commands.lighting.get_config", return_value=CLIConfig(format="json")),
        patch(
            "cli.utils.connection.send_command",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "creation failed"},
        ) as send,
    ):
        result = CliRunner().invoke(lighting, ["create", "SharedName"])

    assert send.call_count == 1
    assert result.exit_code == 1
    assert "creation failed" in result.output
    assert "lightingCreation" not in json.loads(result.stdout)


STEPS = ["create_gameobject", "add_light_component", "set_type", "set_color", "set_intensity"]


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("failure", ["http_error", "refused", "timeout", "read_error"])
@pytest.mark.parametrize("verbose", [False, True])
def test_transport_failure_sanitizes_json_and_preserves_light_creation_progress(
    monkeypatch, stage, failure, verbose
):
    # Given transport diagnostics contain credentials at any creation/configuration step.
    credential = "synthetic-lighting-credential-must-not-escape"
    created = {"instanceID": 1234, "name": "Owned", "enabled": False}
    client_type = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == stage:
            match failure:
                case "http_error":
                    return httpx.Response(503, text=f"Authorization: Bearer {credential}")
                case "refused":
                    raise httpx.ConnectError(f"refused X-API-Key: {credential}")
                case "timeout":
                    raise httpx.ReadTimeout(f"deadline X-API-Key: {credential}")
                case "read_error":
                    raise httpx.ReadError(f"failed read Authorization: Bearer {credential}")
                case _:
                    pytest.fail("Unexpected transport failure fixture")
        return httpx.Response(
            200,
            json={"success": True, "data": created} if len(requests) == 1 else {"success": True},
        )

    monkeypatch.setattr(connection, "read_local_auth_token", lambda *_: credential)
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond)),
    )

    # When the full CLI receives a transport failure, including with verbose diagnostics.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            *(["--verbose"] if verbose else []),
            "lighting",
            "create",
            "Owned",
            "--color",
            "0",
            "0",
            "0",
            "--intensity",
            "0",
        ],
    )

    # Then output is one sanitized failure document and no uncertain work is replayed.
    assert result.exit_code == 1, result.output
    assert credential not in result.output
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["code"] == "connection_error"
    assert payload["error"]
    assert len(requests) == stage
    if not verbose:
        assert result.stderr == ""
    if stage == 1:
        assert "lightingCreation" not in payload
    else:
        progress = payload["lightingCreation"]
        assert progress["partialExecution"] is True
        assert progress["createdObject"] == created
        assert progress["requestedName"] == "Owned"
        assert progress["completedSteps"] == STEPS[: stage - 1]
        assert progress["failedStep"] == STEPS[stage - 1]
        assert progress["failedStepOutcome"] == "unknown"
        assert "retry" in progress["hint"].lower()
        assert all(request["params"]["target"] == 1234 for request in requests[1:])


@pytest.mark.parametrize("stage", [2, 3, 4, 5])
@pytest.mark.parametrize("failure", ["native", "connection"])
@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_light_configuration_failure_retains_identity_and_confirmed_progress(
    monkeypatch, stage, failure, output_format
):
    # Given a created object, including falsey native fields and zero-valued options.
    config = CLIConfig(format=output_format)
    monkeypatch.setattr("cli.utils.config._config", config)
    created = {"instanceID": -204, "name": "SharedName", "enabled": False}
    native_error = {
        "success": False,
        "error": "Controlled rejection",
        "code": 0,
        "data": {"zero": 0, "enabled": False, "reference": None},
        "hint": "Inspect the existing object",
    }
    calls = []

    async def send(command, params, config=None, timeout=None):
        calls.append((command, params))
        if len(calls) == stage:
            if failure == "connection":
                raise UnityConnectionError("Connection timed out")
            return native_error
        return {"success": True, "data": created} if len(calls) == 1 else {"success": True}

    # When the real command reaches a rejected or unconfirmed configuration step.
    with patch("cli.utils.connection.send_command", side_effect=send):
        result = CliRunner().invoke(
            lighting, ["create", "SharedName", "--color", "0", "0", "0", "--intensity", "0"]
        )

    # Then it stops without replay/deletion and reports only confirmed completed steps.
    assert result.exit_code == 1, result.output
    assert len(calls) == stage
    assert result.stderr == ""
    if output_format == "json":
        payload = json.loads(result.stdout)
        if failure == "native":
            assert {key: payload[key] for key in native_error} == native_error
        else:
            assert payload["code"] == "connection_error"
            assert payload["error"]
            assert "Connection timed out" not in payload["error"]
        progress = payload["lightingCreation"]
        assert progress["partialExecution"] is True
        assert progress["createdObject"] == created
        assert progress["completedSteps"] == STEPS[: stage - 1]
        assert progress["failedStep"] == STEPS[stage - 1]
        assert progress["failedStepOutcome"] == ("unknown" if failure == "connection" else "failed")
        assert "retry" in progress["hint"].lower()
    else:
        for detail in ("-204", "partialExecution", STEPS[stage - 1], "create_gameobject"):
            assert detail in result.stdout


def test_intensity_failure_does_not_claim_omitted_color_step_completed():
    # Given optional color is omitted, the fourth request configures zero intensity.
    replies = [
        {"success": True, "data": {"instanceID": 204}},
        {"success": True},
        {"success": True},
        {"success": False, "error": "intensity rejected"},
    ]
    with patch("cli.utils.connection.send_command", new_callable=AsyncMock, side_effect=replies):
        # When intensity fails, progress contains no skipped color step.
        result = CliRunner().invoke(lighting, ["create", "Owned", "--intensity", "0"])
    assert result.exit_code == 1
    progress = json.loads(result.stdout)["lightingCreation"]
    assert progress["completedSteps"] == STEPS[:3]
    assert progress["failedStep"] == "set_intensity"


@pytest.mark.parametrize("output_format", ["json", "text"])
def test_initial_connection_error_does_not_claim_an_object_was_created(monkeypatch, output_format):
    # Given no successful create response, its outcome and object identity are unknown.
    monkeypatch.setattr("cli.utils.config._config", CLIConfig(format=output_format))
    with patch(
        "cli.utils.connection.send_command",
        new_callable=AsyncMock,
        side_effect=UnityConnectionError("Connection timed out"),
    ) as send:
        # When the initial request fails, preserve the existing connection error path.
        result = CliRunner().invoke(lighting, ["create", "Owned"])
    assert result.exit_code == 1
    assert send.call_count == 1
    if output_format == "json":
        document = json.loads(result.stdout)
        assert document["success"] is False
        assert document["code"] == "connection_error"
        assert document["error"]
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert "Connection timed out" in result.stderr
    assert "lightingCreation" not in result.output
    assert "Created" not in result.output


@pytest.mark.parametrize(
    "indicator",
    [
        {"code": "command_outcome_unknown"},
        {"data": {"reason": "outcome_unknown", "command_id": "receipt-204", "dispatched": True}},
    ],
)
def test_native_unknown_outcome_retains_receipt_and_uncertainty(indicator):
    # Given the transport reports an uncertain outcome as a structured failed response.
    failure = {"success": False, "error": "Reply lost after dispatch", **indicator}
    replies = [{"success": True, "data": {"instanceID": -204}}, failure]
    with patch(
        "cli.utils.connection.send_command", new_callable=AsyncMock, side_effect=replies
    ) as send:
        # When configuring an already created light, do not convert uncertainty to refusal.
        result = CliRunner().invoke(lighting, ["create", "Owned"])
    assert result.exit_code == 1
    assert send.call_count == 2
    payload = json.loads(result.stdout)
    assert {key: payload[key] for key in failure} == failure
    progress = payload["lightingCreation"]
    assert progress["failedStepOutcome"] == "unknown"
    assert progress["createdObject"]["instanceID"] == -204
    assert progress["completedSteps"] == ["create_gameobject"]
