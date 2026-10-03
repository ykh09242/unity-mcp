"""Exercise status via real Click and the actual HTTP connection helpers."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_status_formats_health_failures_and_environment_precedence(tmp_path):
    source = '''
        import copy
        import json
        import sys
        sys.path.insert(0, "src")
        import httpx
        from click.testing import CliRunner
        from cli.main import cli
        from cli.utils import connection

        current = None
        requests = []
        rows = [{"project": "Fixture", "hash": "fullfixturehash", "unity_version": "6000.fixture",
                 "session_id": "fixture-session", "extra": {"active": False, "count": 0}}]
        client_type = httpx.AsyncClient
        def respond(request):
            requests.append(str(request.url))
            if request.url.path == "/health":
                if current == "health_transport":
                    raise httpx.ConnectError("controlled health transport failure", request=request)
                return httpx.Response(503 if current == "health_failure" else 200, json={"status": "fixture"})
            assert request.url.path == "/api/instances"
            if current == "instances_transport":
                raise httpx.ConnectError("controlled discovery transport failure", request=request)
            if current == "instances_failure":
                return httpx.Response(503, json={"error": "controlled discovery failure"})
            return httpx.Response(200, json={"instances": [] if current == "empty" else copy.deepcopy(rows)})
        connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond))
        connection._auth_headers = lambda config: {}
        runner = CliRunner()
        for current in ("connected", "empty", "health_failure", "health_transport", "instances_failure", "instances_transport"):
            healthy = not current.startswith("health_")
            for output_format in ("json", "table", "text"):
                before = len(requests)
                result = runner.invoke(cli, ["--host", "127.0.0.1", "--port", "18080", "--instance", "Selected@hash", "--format", output_format, "status"])
                assert result.exit_code == (0 if healthy else 1), (result.exception, result.output)
                expected = ["http://127.0.0.1:18080/health"]
                if healthy:
                    expected.append("http://127.0.0.1:18080/api/instances")
                assert requests[before:] == expected
                if output_format == "json":
                    response = json.loads(result.stdout)
                    assert result.stderr == ""
                    assert response["success"] is healthy and response["host"] == "127.0.0.1" and response["port"] == 18080
                    if not healthy:
                        assert "Cannot connect" in response["error"] and response["instances"] == []
                    elif current.startswith("instances_"):
                        assert "Could not retrieve" in response["warning"] and response["instances"] == []
                    else:
                        assert response["instances"] == ([] if current == "empty" else rows)
                elif output_format == "table":
                    assert "Key" in result.stdout and "Value" in result.stdout
                    assert "Checking connection" not in result.stdout
                else:
                    assert "Checking connection" in result.stdout
                    if healthy:
                        assert "Connected" in result.stdout
                    else:
                        assert "Cannot connect" in result.stderr
        current = "connected"
        inherited = runner.invoke(cli, ["status"], env={"UNITY_MCP_FORMAT": "json"})
        assert inherited.exit_code == 0 and json.loads(inherited.stdout)["instances"] == rows
        override = runner.invoke(cli, ["--format", "text", "status"], env={"UNITY_MCP_FORMAT": "json"})
        assert override.exit_code == 0 and "Checking connection" in override.stdout
        remote = runner.invoke(cli, ["--host", "fixture.invalid", "--format", "json", "status"])
        assert remote.exit_code == 0 and json.loads(remote.stdout)["host"] == "fixture.invalid"
        assert "Security Warning" in remote.stderr and "Security Warning" not in remote.stdout
        help_result = runner.invoke(cli, ["--help"])
        assert help_result.exit_code == 0 and "unity-mcp --format json scene hierarchy" in help_result.output
    '''
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    for key in ("UNITY_MCP_FORMAT", "UNITY_MCP_HOST", "UNITY_MCP_HTTP_PORT", "UNITY_MCP_TIMEOUT", "UNITY_MCP_INSTANCE"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
