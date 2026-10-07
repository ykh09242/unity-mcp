"""Batch file inputs preserve Unicode and report local file errors cleanly."""

import builtins
import json
from pathlib import Path
import socket
import sys

import httpx
import pytest
from click.testing import CliRunner


@pytest.fixture
def batch_transport(monkeypatch, tmp_path):
    # Own application paths before importing the real CLI and connection code.
    for key in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "XDG_DATA_HOME",
        "UNITY_MCP_LOG_DIR",
        "UNITY_MCP_STATUS_DIR",
    ):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    for key in (
        "UNITY_MCP_INSTANCE",
        "UNITY_MCP_FORMAT",
        "UNITY_MCP_HTTP_PORT",
        "UNITY_MCP_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def deny_network(*args, **kwargs):
        raise AssertionError("Batch tests must not access the network")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    from cli.main import cli
    from cli.utils import connection

    requests, clients = [], []
    client_type = httpx.AsyncClient
    response = {"success": True, "data": {"results": []}}

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    def client():
        instance = client_type(transport=httpx.MockTransport(respond))
        clients.append(instance)
        return instance

    monkeypatch.setattr(connection.httpx, "AsyncClient", client)
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    yield cli, requests, response
    assert all(instance.is_closed for instance in clients)


@pytest.mark.parametrize("name", ["Caf\u00e9", "\uc601\uc6c5", "\U0001f680"])
@pytest.mark.parametrize("legacy_encoding", [False, True])
def test_utf8_file_preserves_unicode_at_real_http_boundary(
    batch_transport, tmp_path, monkeypatch, name, legacy_encoding
):
    cli, requests, response = batch_transport
    commands = [{"tool": "manage_gameobject", "params": {"action": "create", "name": name}}]
    path = tmp_path / "commands.json"
    path.write_text(json.dumps(commands, ensure_ascii=False), encoding="utf-8")
    if legacy_encoding:
        # Real TextIOWrapper decoding; only the ambient encoding is controlled.
        def locale_open(file, mode="r", **kwargs):
            kwargs.setdefault("encoding", "cp1252")
            return builtins.open(file, mode, **kwargs)

        monkeypatch.setattr(sys.modules["cli.commands.batch"], "open", locale_open, raising=False)
    result = CliRunner().invoke(
        cli,
        [
            "--instance",
            "Owned@aaaa1111",
            "--format",
            "json",
            "batch",
            "run",
            str(path),
            "--parallel",
            "--fail-fast",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response
    assert requests == [
        {
            "type": "batch_execute",
            "unity_instance": "Owned@aaaa1111",
            "params": {"commands": commands, "parallel": True, "failFast": True},
        }
    ]


@pytest.mark.parametrize("format", ["text", "json", "table"])
def test_invalid_utf8_has_diagnostic_and_does_not_send(batch_transport, tmp_path, format):
    cli, requests, _ = batch_transport
    path = tmp_path / "commands.json"
    path.write_bytes(b'["\xff"]')
    result = CliRunner().invoke(cli, ["--format", format, "batch", "run", str(path)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "UTF-8" in result.stderr
    assert result.stdout == ""
    assert requests == []


@pytest.mark.parametrize("destination", ["directory", "missing_parent"])
def test_template_write_failure_has_local_diagnostic(batch_transport, tmp_path, destination):
    cli, requests, _ = batch_transport
    path = tmp_path if destination == "directory" else tmp_path / "missing" / "commands.json"
    result = CliRunner().invoke(cli, ["batch", "template", "--output", str(path)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error writing template file" in result.stderr
    assert "Template written" not in result.output
    assert requests == []


def test_stdout_template_and_owned_file_roundtrip(batch_transport, tmp_path):
    cli, requests, response = batch_transport
    stdout = CliRunner().invoke(cli, ["batch", "template"])
    assert stdout.exit_code == 0, stdout.output
    commands = json.loads(stdout.stdout)
    assert commands[1]["params"]["primitiveType"] == "Cube"
    assert commands[2]["params"]["componentType"] == "Rigidbody"
    path = tmp_path / "template.json"
    written = CliRunner().invoke(cli, ["batch", "template", "--output", str(path)])
    assert written.exit_code == 0, written.output
    assert json.loads(path.read_text(encoding="utf-8")) == commands
    executed = CliRunner().invoke(cli, ["--format", "json", "batch", "run", str(path)])
    assert executed.exit_code == 0, executed.output
    assert json.loads(executed.stdout) == response
    assert requests == [{"type": "batch_execute", "params": {"commands": commands}}]
