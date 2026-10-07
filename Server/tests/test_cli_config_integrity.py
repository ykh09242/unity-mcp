"""Configuration validation and precedence through Click and real HTTP decoding."""

import json
import os
import subprocess
import sys
import textwrap
from dataclasses import asdict
from importlib import import_module

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import config as config_module, connection
from cli.utils.config import CLIConfig


ENV_NAMES = ("UNITY_MCP_HOST", "UNITY_MCP_HTTP_PORT", "UNITY_MCP_TIMEOUT", "UNITY_MCP_FORMAT", "UNITY_MCP_INSTANCE")
INVALID_VALUES = [
    ("port", "UNITY_MCP_HTTP_PORT", "0"),
    ("port", "UNITY_MCP_HTTP_PORT", "-1"),
    ("port", "UNITY_MCP_HTTP_PORT", "65536"),
    ("port", "UNITY_MCP_HTTP_PORT", "999999999999999999999"),
    ("timeout", "UNITY_MCP_TIMEOUT", "0"),
    ("timeout", "UNITY_MCP_TIMEOUT", "-1"),
    ("format", "UNITY_MCP_FORMAT", "csv"),
    ("format", "UNITY_MCP_FORMAT", "JSON"),
    ("port", "UNITY_MCP_HTTP_PORT", "not-an-integer"),
    ("timeout", "UNITY_MCP_TIMEOUT", "1.5"),
]


@pytest.fixture(autouse=True)
def isolated_cli_config(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module, "_config", None)


@pytest.fixture
def config_transport(monkeypatch):
    trace = {"warnings": [], "clients": [], "requests": [], "auth": []}
    client_type = httpx.AsyncClient

    def respond(request):
        trace["requests"].append({"url": str(request.url), "timeout": request.extensions["timeout"], "body": json.loads(request.content)})
        return httpx.Response(200, json={"success": True, "data": {"count": 0, "enabled": False}})

    def client():
        trace["clients"].append(True)
        return client_type(transport=httpx.MockTransport(respond))

    def auth(config):
        trace["auth"].append(config)
        return {}

    monkeypatch.setattr(connection.httpx, "AsyncClient", client)
    monkeypatch.setattr(connection, "_auth_headers", auth)
    monkeypatch.setattr(import_module("cli.main"), "warn_if_remote_host", lambda config: trace["warnings"].append(config))
    return trace


@pytest.mark.parametrize("option,env_name,value", INVALID_VALUES)
@pytest.mark.parametrize("source", ["option", "environment"])
def test_invalid_settings_stop_before_configuration_or_transport(monkeypatch, config_transport, option, env_name, value, source):
    sentinel = CLIConfig(port=9090)
    monkeypatch.setattr(config_module, "_config", sentinel)
    args = []
    if source == "environment":
        monkeypatch.setenv(env_name, value)
    else:
        args += ["--" + option, value]
    result = CliRunner().invoke(cli, [*args, "raw", "fixture"])
    assert result.exit_code == 2, result.output
    assert "Invalid value" in result.output and "--" + option in result.output
    assert "Traceback" not in result.output
    assert config_module._config is sentinel
    assert config_transport == {"warnings": [], "clients": [], "requests": [], "auth": []}


@pytest.mark.parametrize("option,env_name,value", INVALID_VALUES)
def test_from_env_rejects_invalid_settings_with_variable_diagnostic(monkeypatch, option, env_name, value):
    monkeypatch.setenv(env_name, value)
    with pytest.raises(ValueError, match=env_name):
        CLIConfig.from_env()


@pytest.mark.parametrize("source", ["default", "environment", "option"])
@pytest.mark.parametrize("port,timeout,output_format", [(1, 1, "text"), (65535, 3600, "json"), (9090, 60, "table")])
def test_valid_settings_resolve_and_reach_transport(monkeypatch, config_transport, source, port, timeout, output_format):
    args = []
    if source == "default":
        expected = CLIConfig()
    else:
        expected = CLIConfig(host="localhost", port=port, timeout=timeout, format=output_format, unity_instance="Project@fixture")
        if source == "environment":
            for name, value in zip(ENV_NAMES, (expected.host, str(port), str(timeout), output_format, expected.unity_instance)):
                monkeypatch.setenv(name, value)
        else:
            args = ["--host", expected.host, "--port", str(port), "--timeout", str(timeout), "--format", output_format, "--instance", expected.unity_instance]
    result = CliRunner().invoke(cli, [*args, "raw", "fixture", '{"zero":0,"false":false,"null":null}'])
    assert result.exit_code == 0, result.output
    assert config_module._config == expected
    request = config_transport["requests"][0]
    assert request["url"] == f"http://{expected.host}:{expected.port}/api/command"
    assert set(request["timeout"].values()) == {expected.timeout}
    expected_body = {"type": "fixture", "params": {"zero": 0, "false": False, "null": None}}
    if expected.unity_instance is not None:
        expected_body["unity_instance"] = expected.unity_instance
    assert request["body"] == expected_body
    if expected.format == "json":
        assert json.loads(result.stdout) == {"success": True, "data": {"count": 0, "enabled": False}}
    assert config_transport["warnings"] == [expected] and config_transport["auth"] == [expected]


@pytest.mark.parametrize("port,timeout,output_format", [(1, 1, "text"), (65535, 3600, "json"), (9090, 60, "table")])
def test_from_env_preserves_valid_boundaries(monkeypatch, port, timeout, output_format):
    for name, value in zip(ENV_NAMES, ("localhost", str(port), str(timeout), output_format, "Project@fixture")):
        monkeypatch.setenv(name, value)
    assert asdict(CLIConfig.from_env()) == {"host": "localhost", "port": port, "timeout": timeout, "format": output_format, "unity_instance": "Project@fixture", "verbose": False}


def test_explicit_options_override_even_invalid_unused_environment(monkeypatch, config_transport):
    for name, value in zip(ENV_NAMES, ("unused.fixture.invalid", "not-an-integer", "-1", "csv", "Unused@fixture")):
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(cli, ["--host", "localhost", "--port", "9090", "--timeout", "45", "--format", "json", "--instance", "Chosen@fixture", "raw", "fixture"])
    assert result.exit_code == 0, result.output
    assert config_module._config == CLIConfig(host="localhost", port=9090, timeout=45, format="json", unity_instance="Chosen@fixture")
    assert config_transport["requests"][0]["body"]["unity_instance"] == "Chosen@fixture"
    assert json.loads(result.stdout)["success"] is True


def test_partial_override_keeps_remaining_environment_settings(monkeypatch, config_transport):
    for name, value in zip(ENV_NAMES, ("localhost", "8081", "45", "json", "Env@fixture")):
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(cli, ["--port", "9090", "raw", "fixture"])
    assert result.exit_code == 0, result.output
    assert config_module._config == CLIConfig(host="localhost", port=9090, timeout=45, format="json", unity_instance="Env@fixture")
    assert len(config_transport["requests"]) == 1


def test_empty_environment_values_keep_click_defaults(monkeypatch, config_transport):
    for name in ENV_NAMES:
        monkeypatch.setenv(name, "")
    result = CliRunner().invoke(cli, ["raw", "fixture"])
    assert result.exit_code == 0, result.output
    assert config_module._config == CLIConfig()
    assert len(config_transport["requests"]) == 1


@pytest.mark.parametrize("env_name", ["UNITY_MCP_HTTP_PORT", "UNITY_MCP_TIMEOUT"])
def test_from_env_explicit_blank_numeric_value_remains_an_error(monkeypatch, env_name):
    monkeypatch.setenv(env_name, "")
    with pytest.raises(ValueError, match=env_name):
        CLIConfig.from_env()


def test_failed_fallback_does_not_cache_invalid_configuration(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "0")
    with pytest.raises(ValueError, match="UNITY_MCP_HTTP_PORT"):
        config_module.get_config()
    assert config_module._config is None
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "8081")
    assert config_module.get_config().port == 8081
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "9090")
    assert config_module.get_config().port == 8081


def test_configuration_smoke_in_fresh_process():
    code = textwrap.dedent('''
        import json, os
        import httpx
        from click.testing import CliRunner
        from cli.main import cli
        from cli.utils import connection
        requests = []
        client_type = httpx.AsyncClient
        def respond(request):
            requests.append((str(request.url), request.extensions["timeout"]))
            return httpx.Response(200, json={"success": True, "data": {"count": 0}})
        connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
        connection._auth_headers = lambda config: {}
        runner = CliRunner()
        result = runner.invoke(cli, ["--format", "json", "raw", "fixture"])
        assert result.exit_code == 0 and json.loads(result.stdout)["data"]["count"] == 0
        assert requests[-1][0] == "http://127.0.0.1:8080/api/command"
        os.environ["UNITY_MCP_HTTP_PORT"] = "0"
        result = runner.invoke(cli, ["raw", "fixture"])
        assert result.exit_code == 2 and len(requests) == 1
        os.environ["UNITY_MCP_HTTP_PORT"] = "garbage"
        result = runner.invoke(cli, ["--port", "65535", "--timeout", "1", "raw", "fixture"])
        assert result.exit_code == 0 and len(requests) == 2
        assert requests[-1][0] == "http://127.0.0.1:65535/api/command" and set(requests[-1][1].values()) == {1}
        print("fresh configuration defaults/invalid/override checks passed")
    ''')
    env = {name: value for name, value in os.environ.items() if name not in ENV_NAMES}
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    result = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fresh configuration defaults/invalid/override checks passed" in result.stdout
