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
def test_switch_between_same_named_editors_cannot_receive_a_second_write(monkeypatch, created_id):
    active = ["Fixture@0123456789abcdea", "Fixture@0123456789abcdeb"]
    calls = []
    created = {"instanceID": created_id, "name": "SharedName"}

    def respond(request):
        payload = json.loads(request.content)
        calls.append((active[min(len(calls), 1)], payload))
        return httpx.Response(200, json={"success": True, "data": created})

    client = httpx.AsyncClient
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    monkeypatch.setattr(
        connection, "_http_client", lambda config: client(transport=httpx.MockTransport(respond))
    )
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "lighting",
            "create",
            "SharedName",
            "--color",
            "0",
            "0",
            "0",
            "--intensity",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"] == created
    assert [target for target, _ in calls] == active[:1]
    assert calls[0][1]["type"] == "manage_gameobject"
    assert calls[0][1]["params"]["componentsToAdd"] == [
        {
            "typeName": "Light",
            "properties": {
                "type": "Point",
                "color": {"r": 0, "g": 0, "b": 0, "a": 1},
                "intensity": 0,
            },
        }
    ]


@pytest.mark.parametrize("data", [None, {}, {"instanceID": None}])
def test_light_creation_without_returned_id_reports_incomplete_evidence(data):
    with patch(
        "cli.utils.connection.send_command",
        new_callable=AsyncMock,
        return_value={"success": True, "data": data},
    ) as send:
        result = CliRunner().invoke(lighting, ["create", "SharedName"])
    assert result.exit_code == 1 and send.call_count == 1
    assert "instanceID" in json.loads(result.stdout)["error"]


def test_uncertain_native_create_preserves_receipt_without_replaying():
    response = {
        "success": False,
        "code": "command_outcome_unknown",
        "data": {"command_id": "fixture", "dispatched": True},
    }
    with patch(
        "cli.utils.connection.send_command", new_callable=AsyncMock, return_value=response
    ) as send:
        result = CliRunner().invoke(lighting, ["create", "Owned"])
    assert result.exit_code == 1 and send.call_count == 1
    assert json.loads(result.stdout) == response


@pytest.mark.parametrize("failure", ["http_error", "refused", "timeout", "read_error"])
@pytest.mark.parametrize("verbose", [False, True])
def test_lost_response_does_not_replay_the_create_or_print_credentials(
    monkeypatch, failure, verbose
):
    credential = "synthetic-lighting-credential-must-not-escape"
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if failure == "http_error":
            return httpx.Response(503, text=credential)
        raise {
            "refused": httpx.ConnectError,
            "timeout": httpx.ReadTimeout,
            "read_error": httpx.ReadError,
        }[failure](credential)

    client = httpx.AsyncClient
    monkeypatch.setattr(connection, "read_local_auth_token", lambda *_: credential)
    monkeypatch.setattr(
        connection.httpx, "AsyncClient", lambda **_: client(transport=httpx.MockTransport(respond))
    )
    result = CliRunner().invoke(
        cli,
        ["--format", "json", *(["--verbose"] if verbose else []), "lighting", "create", "Owned"],
    )
    assert result.exit_code == 1 and len(requests) == 1
    assert credential not in result.output
    assert json.loads(result.stdout)["code"] == "connection_error"


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
