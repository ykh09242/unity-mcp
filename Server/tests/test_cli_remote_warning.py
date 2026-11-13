"""Remote security notices follow successful local admission and precede traffic."""

import json
from types import SimpleNamespace

import click
import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    for name in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv("UNITY_MCP_" + name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(connection, "read_local_auth_token", lambda *_args: "owned-token")


@pytest.mark.parametrize("environment", [False, True])
@pytest.mark.parametrize(
    "command,source,code",
    [
        (["component", "modify", "Owned", "T", "--properties", "{"], None, "invalid_json_input"),
        (["shader", "create", "Owned"], b"PRIVATE_MARKER\xff", "invalid_source_input"),
        (
            ["shader", "update", "Assets/Owned.shader"],
            b"PRIVATE_MARKER\xff",
            "invalid_source_input",
        ),
        (["code", "execute", "--file", "PRIVATE_MARKER-missing.cs"], None, "invalid_source_input"),
        (["raw", "fixture", "{"], None, "invalid_json_params"),
        (["raw", "fixture", '{"value":NaN}'], None, "invalid_json_params"),
    ],
)
def test_remote_local_json_errors_are_silent_before_any_client(
    monkeypatch, environment, command, source, code
):
    def forbidden_client(**_options):
        pytest.fail("Local input rejection constructed an HTTP client")

    monkeypatch.setattr(connection.httpx, "AsyncClient", forbidden_client)
    if environment:
        monkeypatch.setenv("UNITY_MCP_HOST", "example.invalid")
        monkeypatch.setenv("UNITY_MCP_FORMAT", "json")
        args = []
    else:
        args = ["--host", "example.invalid", "--format", "json"]
    result = CliRunner().invoke(cli, [*args, *command], input=source)
    assert result.exit_code == 1, (result.output, result.exception)
    assert json.loads(result.stdout)["code"] == code
    assert result.stderr == ""
    assert "PRIVATE_MARKER" not in result.output


@pytest.mark.parametrize(
    "command", [["batch", "template"], ["doctor", "--config"], ["component", "modify", "--help"]]
)
def test_remote_offline_operations_do_not_warn_or_connect(monkeypatch, command):
    def forbidden_client(**_options):
        pytest.fail("Offline operation constructed an HTTP client")

    monkeypatch.setattr(connection.httpx, "AsyncClient", forbidden_client)
    result = CliRunner().invoke(cli, ["--host", "example.invalid", "--format", "json", *command])
    assert result.exit_code == 0, (result.output, result.exception)
    assert result.stderr == ""


@pytest.fixture
def remote_wire(monkeypatch):
    events = []
    original_echo = click.echo
    client_type = httpx.AsyncClient

    def echo(message=None, *args, **kwargs):
        if "Security Warning" in str(message):
            events.append("warning")
        return original_echo(message, *args, **kwargs)

    def respond(request):
        assert events and events[0] == "warning"
        assert events.count("warning") == 1
        events.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path == "/api/instances":
            return httpx.Response(200, json={"instances": [{"project": "Owned", "hash": "owned"}]})
        if request.url.path == "/api/custom-tools":
            return httpx.Response(200, json={"success": True, "tools": []})
        return httpx.Response(200, json={"success": True, "data": {}})

    monkeypatch.setattr(click, "echo", echo)
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond)),
    )
    return events


@pytest.mark.parametrize("output_format", ["json", "text", "table"])
@pytest.mark.parametrize(
    "command,paths",
    [
        (["raw", "fixture"], ["/api/command"]),
        (["status"], ["/health", "/api/instances"]),
        (["status", "--ready"], ["/health", "/api/instances"]),
        (["instances"], ["/api/instances"]),
        (["tool", "list"], ["/api/custom-tools"]),
    ],
)
def test_remote_connections_warn_once_before_traffic_for_each_invocation(
    remote_wire, output_format, command, paths
):
    for _ in range(2):
        remote_wire.clear()
        result = CliRunner().invoke(
            cli, ["--host", "example.invalid", "--format", output_format, *command]
        )
        assert result.exit_code == 0, (result.output, result.exception)
        assert remote_wire == ["warning", *paths]
        assert result.stderr.count("Security Warning") == 1
        assert "Security Warning" not in result.stdout


def test_doctor_auxiliary_transport_warns_once_across_worker_and_async_contexts(monkeypatch):
    import httpx2
    from cli.utils import doctor_connection

    events = []
    original_echo = click.echo

    def echo(message=None, *args, **kwargs):
        if "Security Warning" in str(message):
            events.append("warning")
        return original_echo(message, *args, **kwargs)

    def respond(_request):
        assert events == ["warning"]
        events.append("request")
        return httpx2.Response(200, json={})

    class DiagnosticClient:
        protocol_version = "2026-07-28"

        def __init__(self, config, _protocol, _factory, _headers):
            self.config = config

        async def __aenter__(self):
            # Actual diagnose already called this helper in its AnyIO worker.
            connection._auth_headers(self.config)
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                await client.get("http://example.invalid/mcp")
            return self

        async def __aexit__(self, *_args):
            return False

        async def list_resources_mcp(self, **_kwargs):
            return SimpleNamespace(resources=[], next_cursor=None)

    monkeypatch.setattr(click, "echo", echo)
    monkeypatch.setattr(doctor_connection, "DiagnosticClient", DiagnosticClient)
    for _ in range(2):
        events.clear()
        result = CliRunner().invoke(
            cli,
            [
                "--host",
                "example.invalid",
                "--format",
                "json",
                "--instance",
                "Owned@aaaa1111",
                "doctor",
            ],
        )
        assert result.exit_code == 3, (result.output, result.exception)
        assert json.loads(result.stdout)["reason"] == "doctor_unavailable"
        assert events == ["warning", "request"]
        assert result.stderr.count("Security Warning") == 1
