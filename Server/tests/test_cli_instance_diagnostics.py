"""Public instance commands retain machine-readable results and close transports."""

import json

import httpx
import pytest
from click.testing import CliRunner


@pytest.fixture
def instance_transport(monkeypatch, tmp_path):
    # Own all application-data/log paths before importing production modules.
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        monkeypatch.setenv(key, str(directory))
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    monkeypatch.delenv("UNITY_MCP_INSTANCE", raising=False)
    from cli.main import cli
    from cli.utils import connection

    catalog = {
        "success": True,
        "instances": [
            {
                "project": "Controlled",
                "hash": "aaaa1111",
                "unity_version": "6000.fixture",
                "session_id": "fixture-session",
                "connected_at": "fixture-time",
            }
        ],
    }
    selected = {"success": True, "data": {"instance": "Controlled@aaaa1111"}}
    requests, clients = [], []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.url.path == "/api/instances":
            return httpx.Response(200, json=catalog)
        assert request.url.path == "/api/command"
        return httpx.Response(200, json=selected)

    def create_client():
        client = client_type(transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    monkeypatch.setattr(connection.httpx, "AsyncClient", create_client)
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    yield cli, catalog, selected, requests
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("empty", [False, True])
def test_instance_list_json_retains_entire_catalog(instance_transport, empty):
    cli, catalog, _, requests = instance_transport
    if empty:
        catalog["instances"] = []
    result = CliRunner().invoke(cli, ["--format", "json", "instance", "list"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == catalog
    assert len(requests) == 1


@pytest.mark.parametrize("selector", [None, "Controlled@aaaa1111"])
def test_current_json_reports_only_configured_selector_without_discovery(
    instance_transport, selector
):
    cli, _, _, requests = instance_transport
    args = ["--format", "json"]
    if selector:
        args += ["--instance", selector]
    result = CliRunner().invoke(cli, [*args, "instance", "current"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"success": True, "data": {"instance": selector}}
    assert requests == []


@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_set_fails_locally_with_targeting_guidance(instance_transport, output_format):
    cli, _, _, requests = instance_transport
    result = CliRunner().invoke(cli, ["--format", output_format, "instance", "set", "aaaa1111"])
    assert result.exit_code == 1, result.output
    if output_format == "json":
        response = json.loads(result.stdout)
        assert response["success"] is False
        assert response["data"] == {"requested_instance": "aaaa1111"}
        assert "--instance" in response["error"] and "UNITY_MCP_INSTANCE" in response["error"]
    else:
        assert "--instance" in result.stdout and "UNITY_MCP_INSTANCE" in result.stdout
    assert requests == []


def test_instance_table_uses_instance_rows(instance_transport):
    cli, _, _, _ = instance_transport
    result = CliRunner().invoke(cli, ["--format", "table", "instance", "list"])
    assert result.exit_code == 0, result.output
    assert "project" in result.stdout and "session_id" in result.stdout
    assert " | " in result.stdout


def test_instance_text_retains_human_listing(instance_transport):
    cli, _, _, _ = instance_transport
    result = CliRunner().invoke(cli, ["instance", "list"])
    assert result.exit_code == 0, result.output
    assert "Available Unity instances:" in result.stdout
    assert "Controlled@aaaa1111 (Unity 6000.fixture)" in result.stdout
