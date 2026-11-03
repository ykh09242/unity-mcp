"""Effective configuration through real CLI parsing and server startup decisions."""

import json
import sys
import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    for name in (
        "HOST",
        "HTTP_PORT",
        "TIMEOUT",
        "FORMAT",
        "INSTANCE",
        "HTTP_URL",
        "TRANSPORT",
        "API_KEY_CACHE_TTL",
        "API_KEY_MAX_AUTH_AGE",
        "API_KEY_SERVICE_TOKEN",
        "API_KEY_SERVICE_TOKEN_HEADER",
        "API_KEY_LOGIN_URL",
        "API_KEY_VALIDATION_URL",
        "HTTP_REMOTE_HOSTED",
        "HTTP_BEHIND_TLS_PROXY",
    ):
        monkeypatch.delenv("UNITY_MCP_" + name, raising=False)
    monkeypatch.chdir(tmp_path)


def offline(*arguments):
    from cli.main import cli

    return CliRunner().invoke(cli, [*arguments, "--format", "json", "doctor", "--config"])


def test_offline_defaults_need_neither_target_auth_nor_network(monkeypatch):
    import cli.utils.connection as connection
    import core.local_auth as auth

    monkeypatch.setattr(connection, "_auth_headers", lambda *_: pytest.fail("auth read"))
    monkeypatch.setattr(auth, "read_local_auth_token", lambda *_: pytest.fail("token read"))
    result = offline()
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["scope"] == "local_cli_configuration"
    assert report["running_server_config_observed"] is False
    assert report["fields"]["port"]["value"] == 8080
    assert report["fields"]["port"]["source"] == "default"
    assert report["fields"]["timeout"]["default"] == 300


def test_offline_records_actual_click_precedence_and_ignored_empty(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "9191")
    monkeypatch.setenv("UNITY_MCP_TIMEOUT", "")
    result = offline("--port", "8080")
    assert result.exit_code == 0, result.output
    fields = json.loads(result.stdout)["fields"]
    assert fields["port"]["value"] == 8080
    assert fields["port"]["source"] == "cli"
    assert "UNITY_MCP_HTTP_PORT" in fields["port"]["overrides"]
    assert fields["timeout"]["source"] == "default"
    assert "empty_environment_ignored" in fields["timeout"]["diagnostics"]
    result = offline()
    assert json.loads(result.stdout)["fields"]["port"]["source"] == "environment"


def test_invalid_cli_environment_still_rejects_before_diagnostic(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "not-an-integer")
    result = offline()
    assert result.exit_code == 2
    assert "Invalid value" in result.output
    # An unused invalid value is overridden by the actual Click resolver.
    result = offline("--port", "8080")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["fields"]["port"]["source"] == "cli"


def test_offline_never_dumps_unrelated_secrets_or_credential_urls(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_API_KEY_SERVICE_TOKEN", "service-secret-sentinel")
    monkeypatch.setenv("OPENAI_API_KEY", "key-secret-sentinel")
    monkeypatch.setenv(
        "UNITY_MCP_HOST", "https://user:password-sentinel@host.invalid/?token=query-sentinel"
    )
    result = offline()
    assert result.exit_code == 0, result.output
    for secret in (
        "service-secret-sentinel",
        "key-secret-sentinel",
        "password-sentinel",
        "query-sentinel",
    ):
        assert secret not in result.output


def test_project_context_source_is_recorded_even_when_value_equals_default(monkeypatch, tmp_path):
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.0")
    (tmp_path / ".unity-mcp.json").write_text(
        json.dumps({"schema_version": "unity-mcp/cli-context@1", "port": 8080})
    )
    result = offline()
    assert result.exit_code == 0, result.output
    fields = json.loads(result.stdout)["fields"]
    assert fields["port"]["source"] == "project_context"
    assert fields["timeout"]["value"] == 30
    assert fields["timeout"]["source"] == "project_context"


def start_server(monkeypatch, arguments):
    import os
    from pathlib import Path
    import subprocess

    root = Path(__file__).resolve().parents[1]
    code = """
import json
import main as entry
from unittest.mock import MagicMock
from core.config import effective_server_configuration
server = MagicMock()
entry.create_mcp_server = MagicMock(return_value=server)
entry.main()
print(json.dumps({"report": effective_server_configuration().model_dump(mode="json"), "run": server.run.call_args.kwargs}))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, *arguments],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    return payload["report"], payload["run"]


def test_server_preserves_field_specific_precedence_and_redacts_credentials(monkeypatch):
    monkeypatch.setenv(
        "UNITY_MCP_HTTP_URL", "http://user:password-sentinel@127.0.0.1:9191/?token=query-sentinel"
    )
    monkeypatch.setenv("UNITY_MCP_API_KEY_CACHE_TTL", "12")
    monkeypatch.setenv("UNITY_MCP_API_KEY_SERVICE_TOKEN", "service-secret-sentinel")
    monkeypatch.setenv("UNITY_MCP_API_KEY_SERVICE_TOKEN_HEADER", "Authorization")
    report, server = start_server(
        monkeypatch, ["--http-url", "http://localhost:8181", "--api-key-cache-ttl", "99"]
    )
    fields = report["fields"]
    assert report["scope"] == "running_server_configuration"
    assert report["running_server_config_observed"] is True
    assert fields["http_url"]["source"] == "environment"
    assert fields["api_key_cache_ttl"]["value"] == 12.0
    assert fields["api_key_cache_ttl"]["source"] == "environment"
    assert "--api-key-cache-ttl" in fields["api_key_cache_ttl"]["overrides"]
    assert fields["http_port"]["value"] == 9191
    assert server == {"transport": "stdio"}
    text = json.dumps(report)
    for secret in (
        "password-sentinel",
        "query-sentinel",
        "service-secret-sentinel",
        "Authorization",
    ):
        assert secret not in text


def test_server_reports_ignored_invalid_and_zero_environment_values(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "bad-port")
    monkeypatch.setenv("UNITY_MCP_API_KEY_CACHE_TTL", "nan")
    monkeypatch.setenv("UNITY_MCP_API_KEY_MAX_AUTH_AGE", "0")
    report, _ = start_server(monkeypatch, [])
    fields = report["fields"]
    assert fields["http_port"]["value"] == 8080
    assert "invalid_environment_ignored" in fields["http_port"]["diagnostics"]
    assert fields["api_key_cache_ttl"]["value"] == 300.0
    assert fields["api_key_cache_ttl"]["source"] == "default"
    assert "invalid_environment_ignored" in fields["api_key_cache_ttl"]["diagnostics"]
    assert fields["api_key_max_auth_age"]["value"] == 300.0


@pytest.mark.parametrize(
    "setting", ["UNITY_MCP_HTTP_PORT", "UNITY_MCP_TIMEOUT", "UNITY_MCP_FORMAT"]
)
def test_invalid_cli_setting_diagnostic_does_not_echo_credentials(monkeypatch, setting):
    monkeypatch.setenv(
        setting, "https://user:invalid-password-sentinel@host/?token=invalid-query-sentinel"
    )
    from cli.main import cli

    result = CliRunner().invoke(cli, ["doctor", "--config"])
    assert result.exit_code == 2
    assert "invalid-password-sentinel" not in result.output
    assert "invalid-query-sentinel" not in result.output


@pytest.mark.asyncio
async def test_server_config_resource_is_scoped_and_needs_no_unity(monkeypatch):
    from fastmcp import Client, FastMCP
    from services.resources import _serialize_pydantic
    from services.resources.doctor import get_config_doctor
    from transport.unity_instance_middleware import UnityInstanceMiddleware
    import services.capabilities as capabilities

    monkeypatch.setattr(
        capabilities, "_capture_project_capabilities", lambda *_: pytest.fail("Unity probe")
    )
    server = FastMCP("configuration-fixture")
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(
        middleware, "_inject_unity_instance", lambda *_: pytest.fail("Unexpected Editor selection")
    )
    server.add_middleware(middleware)
    server.resource("mcpforunity://doctor/config")(_serialize_pydantic(get_config_doctor))
    async with Client(server, cache=False) as client:
        blocks = await client.read_resource("mcpforunity://doctor/config")
    report = json.loads(blocks[0].text)
    assert report["scope"] == "running_server_configuration"
    assert report["running_server_config_observed"] is True
    assert "connection_timeout" in report["fields"]
    assert len(json.dumps(report).encode()) < 32768


@pytest.mark.parametrize(
    "raw,expected,source,diagnostic",
    [
        ("42", 42.0, "environment", None),
        ("", 300.0, "default", "empty_environment_ignored"),
        ("nan", 300.0, "default", "invalid_environment_ignored"),
        ("-1", 300.0, "default", "invalid_environment_ignored"),
    ],
)
def test_fresh_process_captures_actual_timeout_resolution(
    monkeypatch, raw, expected, source, diagnostic
):
    import os
    from pathlib import Path
    import subprocess

    root = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "UNITY_MCP_CONNECTION_TIMEOUT": raw,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from core.config import effective_server_configuration; print(effective_server_configuration().model_dump_json())",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)["fields"]["connection_timeout"]
    assert entry["value"] == expected
    assert entry["default"] == 300.0
    assert entry["source"] == source
    assert "http_host" not in json.loads(result.stdout)["fields"]
    if diagnostic:
        assert diagnostic in entry["diagnostics"]


def test_unobserved_runtime_change_does_not_invent_environment_origin(monkeypatch):
    from core.config import config, effective_server_configuration

    monkeypatch.setattr(config, "connection_timeout", 12.5)
    monkeypatch.setenv("UNITY_MCP_CONNECTION_TIMEOUT", "12.5")
    entry = effective_server_configuration().fields["connection_timeout"]
    assert entry.value == 12.5
    assert entry.source == "runtime"
    assert "origin_unavailable" in entry.diagnostics


@pytest.mark.parametrize(
    "raw,expected,source", [(None, 8080, "default"), ("9191", 9191, "environment")]
)
def test_programmatic_env_config_has_its_own_captured_origin(monkeypatch, raw, expected, source):
    from cli.utils.config import CLIConfig
    from cli.utils.config_diagnostics import local_configuration

    if raw is not None:
        monkeypatch.setenv("UNITY_MCP_HTTP_PORT", raw)
    config = CLIConfig.from_env()
    # Later environment edits do not rewrite the origin of an already-resolved value.
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "9292")
    entry = local_configuration(config).fields["port"]
    assert entry.value == expected
    assert entry.source == source


def test_cli_source_overrides_loaded_project_context(monkeypatch, tmp_path):
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.0")
    (tmp_path / ".unity-mcp.json").write_text(
        json.dumps({"schema_version": "unity-mcp/cli-context@1", "port": 9191})
    )
    result = offline("--port", "8080")
    assert result.exit_code == 0, result.output
    entry = json.loads(result.stdout)["fields"]["port"]
    assert entry["source"] == "cli"
    assert "project_context" in entry["overrides"]


def test_server_transport_default_really_masks_environment(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_TRANSPORT", "http")
    report, server = start_server(monkeypatch, [])
    entry = report["fields"]["transport_mode"]
    assert entry["value"] == "stdio"
    assert entry["source"] == "default"
    assert "UNITY_MCP_TRANSPORT" in entry["overrides"]
    assert server == {"transport": "stdio"}


def test_server_argparse_abbreviation_records_cli_origin(monkeypatch):
    report, _ = start_server(monkeypatch, ["--trans", "stdio"])
    assert report["fields"]["transport_mode"]["source"] == "cli"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:scheme-password-sentinel@host.invalid/path?code=scheme-query-sentinel",
        "//user:scheme-password-sentinel@host.invalid/path?code=scheme-query-sentinel",
        "user:scheme-password-sentinel@host.invalid/path?code=scheme-query-sentinel",
    ],
)
def test_server_redacts_credential_urls_including_invalid_and_relative_forms(monkeypatch, url):
    monkeypatch.setenv("UNITY_MCP_API_KEY_LOGIN_URL", url)
    report, _ = start_server(monkeypatch, [])
    text = json.dumps(report)
    assert "scheme-password-sentinel" not in text
    assert "scheme-query-sentinel" not in text
