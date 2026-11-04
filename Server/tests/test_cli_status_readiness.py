"""Readiness uses inert HTTP responses and never executes an Editor command."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.mark.parametrize("output_format", ["json", "table", "text"])
@pytest.mark.parametrize(
    ("health", "discovery", "rows", "selector", "ready", "authentication", "discovered"),
    [
        (200, 200, 1, None, True, "accepted", True),
        (503, 200, 1, None, False, "unknown", False),
        (0, 200, 1, None, False, "unknown", False),
        (200, 401, 1, None, False, "rejected", False),
        (200, 403, 1, None, False, "rejected", False),
        (200, 503, 1, None, False, "unknown", False),
        (200, 0, 1, None, False, "unknown", False),
        (200, 200, 0, None, False, "accepted", True),
        (200, 200, 2, None, False, "accepted", True),
        (200, 200, 2, "Fixture@0123456789abcdef", True, "accepted", True),
        (200, 200, 2, "missing", False, "accepted", True),
        (200, 200, 2, "Fixture", False, "accepted", True),
        (200, 200, 1, "0123456789abcdef", True, "accepted", True),
    ],
)
def test_requested_readiness_reports_each_boundary(
    monkeypatch, output_format, health, discovery, rows, selector, ready, authentication, discovered
):
    requests = []

    def respond(request):
        requests.append(request.url.path)
        status = health if request.url.path == "/health" else discovery
        if status == 0:
            raise httpx.ConnectError("controlled transport failure", request=request)
        instances = [
            {"project": "Fixture", "hash": "0123456789abcdef", "unity_version": "6000.fixture"},
            {"project": "Fixture", "hash": "fedcba9876543210", "unity_version": "6000.fixture"},
        ][:rows]
        return httpx.Response(status, json={"instances": instances})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_: client_type(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda _: {})
    arguments = ["--no-project-context", "--format", output_format]
    if selector:
        arguments += ["--instance", selector]
    result = CliRunner().invoke(cli, [*arguments, "status", "--ready"])
    assert result.exit_code == (0 if ready else 1), (result.exception, result.output)
    assert requests == (["/health", "/api/instances"] if health == 200 else ["/health"])
    if output_format == "json":
        response = json.loads(result.stdout)
        assert response["success"] is ready
        assert response["server_reachable"] is (health == 200)
        assert response["authentication"] == authentication
        assert response["instance_discovery"] is discovered
        assert response["target_ready"] is ready
        if not ready:
            assert response["error"]
    elif output_format == "text":
        assert ("Ready" in result.stdout) if ready else ("Readiness failed" in result.stderr)


@pytest.mark.parametrize("output_format", ["json", "table", "text"])
@pytest.mark.parametrize("credential", ["missing", "unreadable", "available"])
@pytest.mark.parametrize("readiness_required", [True, False])
def test_status_resolves_owned_credentials_without_escaping_errors(
    monkeypatch, tmp_path, output_format, credential, readiness_required
):
    """Keep the real credential resolver and isolate its filesystem inputs completely."""
    token_path = tmp_path / "fixture-launch-token"
    token = "inert-fixture-launch-credential"
    if credential == "available":
        token_path.write_text(token, encoding="utf-8")
    elif credential == "unreadable":
        # An existing directory cannot be read as the expected UTF-8 credential file.
        token_path.mkdir()
    monkeypatch.delenv("UNITY_MCP_LOCAL_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", str(token_path))
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/health":
            assert "X-Unity-MCP-Token" not in request.headers
            return httpx.Response(200, json={"status": "healthy"})
        assert credential == "available", "Credential failure must prevent protected discovery."
        assert request.url.path == "/api/instances"
        assert request.headers["X-Unity-MCP-Token"] == token
        return httpx.Response(
            200, json={"instances": [{"project": "Fixture", "hash": "0123456789abcdef"}]}
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_: client_type(transport=httpx.MockTransport(respond)),
    )
    arguments = [
        "--host",
        "127.0.0.1",
        "--port",
        "18080",
        "--no-project-context",
        "--format",
        output_format,
        "status",
    ]
    if readiness_required:
        arguments.append("--ready")
    result = CliRunner().invoke(cli, arguments)
    ready = credential == "available"
    assert result.exit_code == (1 if readiness_required and not ready else 0)
    if result.exit_code:
        assert isinstance(result.exception, SystemExit), (
            "Expected credential failures must not escape."
        )
    assert requests == (["/health", "/api/instances"] if ready else ["/health"])
    assert token not in result.output
    if readiness_required:
        assert str(token_path) not in result.output
        if output_format == "json":
            response = json.loads(result.stdout)
            assert response["success"] is ready
            assert response["server_reachable"] is True
            assert response["authentication"] == ("accepted" if ready else "unknown")
            assert response["instance_discovery"] is ready
            assert response["target_ready"] is ready
            if not ready:
                assert response["error"]
        elif not ready and output_format == "text":
            assert "Readiness failed" in result.stderr
    elif output_format == "json":
        assert json.loads(result.stdout)["success"] is True
