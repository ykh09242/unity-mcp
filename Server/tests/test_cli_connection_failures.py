"""Machine-readable connection failures at the real CLI command boundary."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.mark.parametrize(
    "failure",
    [
        "refused",
        "timeout",
        "missing_token",
        "http_error",
        "proxy_503",
        "unvalidated_503",
        "invalid_success_503",
        "oversized_503",
        "unauthorized",
        "redirect",
        "informational",
    ],
)
@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize(
    "command",
    [
        ["scene", "active"],
        ["raw", "fixture"],
        ["instances"],
        ["tool", "list"],
        ["code", "read", "Assets/Owned.cs"],
        ["code", "search", "Owned", "Assets/Owned.cs"],
    ],
)
def test_connection_failure_returns_one_sanitized_json_document(
    monkeypatch, failure, command, verbose
):
    # Given: a failing transport/setup boundary whose diagnostics may contain credentials.
    credential = "synthetic-credential-must-not-escape"
    client_type = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        match failure:
            case "refused":
                raise httpx.ConnectError(f"refused Authorization: Bearer {credential}")
            case "timeout":
                raise httpx.ReadTimeout(f"deadline X-API-Key: {credential}")
            case "http_error":
                return httpx.Response(500, text=f"sensitive response: {credential}")
            case "proxy_503":
                return httpx.Response(503, json={"success": False, "error": credential})
            case "unauthorized":
                return httpx.Response(
                    401,
                    json={
                        "success": False,
                        "code": "no_unity_instances",
                        "dispatch": "not_dispatched",
                        "error": credential,
                    },
                )
            case "unvalidated_503":
                return httpx.Response(
                    503,
                    json={
                        "success": False,
                        "code": "no_unity_instances",
                        "dispatch": "not_dispatched",
                        "error": credential,
                    },
                )
            case "invalid_success_503" | "oversized_503":
                from models.connection_diagnosis import no_unity_instances_response

                payload = dict(no_unity_instances_response())
                if failure == "invalid_success_503":
                    payload["success"] = 0
                else:
                    payload["unexpected"] = credential * 200
                return httpx.Response(503, json=payload)
            case "redirect":
                return httpx.Response(
                    302,
                    headers={"Location": "https://synthetic.example.test/redirect"},
                    text=f"sensitive redirect: {credential}",
                )
            case "informational":
                return httpx.Response(103, text=f"sensitive interim response: {credential}")
            case _:
                pytest.fail("A missing token must prevent dispatch")

    monkeypatch.setattr(
        connection,
        "read_local_auth_token",
        lambda *_: None if failure == "missing_token" else credential,
    )
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond)),
    )

    # When: a standard command requests structured output.
    result = CliRunner().invoke(
        cli, ["--format", "json", *(["--verbose"] if verbose else []), *command]
    )

    # Then: stdout is exactly one sanitized error document with a nonzero status.
    assert result.exit_code == 1, result.output
    document = json.loads(result.stdout)
    assert document["success"] is False
    assert document["code"] == "connection_error"
    assert document["error"]
    assert credential not in result.output
    if not verbose:
        assert result.stderr == ""
    assert len(requests) == (0 if failure == "missing_token" else 1)


def test_connection_failure_keeps_human_text_on_stderr(monkeypatch):
    # Given: a command that cannot connect.
    def fail(*_args, **_kwargs):
        raise connection.UnityConnectionError("Synthetic server is unavailable")

    monkeypatch.setattr("cli.commands.scene.run_command", fail)

    # When: the user chooses text output.
    result = CliRunner().invoke(cli, ["--format", "text", "scene", "active"])

    # Then: the human diagnostic retains its existing stderr contract.
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Synthetic server is unavailable" in result.stderr


@pytest.mark.parametrize("command", [["scene", "active"], ["raw", "fixture"], ["tool", "list"]])
def test_known_no_editor_json_drops_untrusted_extra_fields(monkeypatch, command):
    from models.connection_diagnosis import no_unity_instances_response

    # Given: the known no-session contract plus an untrusted transport annotation.
    credential = "synthetic-no-editor-credential"
    client_type = httpx.AsyncClient
    expected = no_unity_instances_response()
    monkeypatch.setattr(connection, "read_local_auth_token", lambda *_: credential)
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_: client_type(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(503, json={**expected, "unexpected": credential})
            )
        ),
    )

    # When: the CLI receives that known refusal.
    result = CliRunner().invoke(cli, ["--no-project-context", "--format", "json", *command])

    # Then: only the fixed bounded diagnostic reaches stdout.
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == expected
    assert credential not in result.output


@pytest.mark.parametrize("verbose", [False, True])
def test_status_keeps_health_success_and_sanitizes_instance_warning(monkeypatch, verbose):
    credential = "synthetic-status-credential-must-not-escape"
    client_type = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        return httpx.Response(500, text=f"sensitive response: {credential}")

    monkeypatch.setattr(connection, "read_local_auth_token", lambda *_: credential)
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond), trust_env=False),
    )
    result = CliRunner().invoke(
        cli, ["--format", "json", *(["--verbose"] if verbose else []), "status"]
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["success"] is True
    assert document["instances"] == []
    assert document["warning"]
    assert credential not in result.output
    assert result.stderr == ""
    assert requests == ["/health", "/api/instances"]
