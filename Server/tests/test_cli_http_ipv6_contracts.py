"""Public CLI requests use actual HTTPX with an inert transport boundary."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


COMMON = '''
import json, os, socket, sys
from pathlib import Path
owned = Path(os.environ["APPDATA"]).parent / "owned-home"
owned.mkdir()
Path.home = lambda: owned
original_connect, original_pair = socket.socket.connect, socket.socketpair
def deny(*args, **kwargs): raise AssertionError("application socket forbidden")
def internal_pair(*args, **kwargs):
    current = socket.socket.connect
    socket.socket.connect = original_connect
    try: return original_pair(*args, **kwargs)
    finally: socket.socket.connect = current
socket.socketpair = internal_pair
socket.socket.connect = deny
socket.create_connection = deny
sys.path.insert(0, "src")
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection
from cli.utils.config import CLIConfig
requests, auth_calls = [], []
def auth(host, port):
    auth_calls.append((host, port))
    return "owned-fixture-value"
connection.read_local_auth_token = auth
runner = CliRunner()
target = "Selected Project@owned+value"
commands = (["status"], ["instances"], ["tool", "list"], ["raw", "fixture_query", '{"zero":0,"enabled":false}'])
client_type = httpx.AsyncClient
'''


def _run(source, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    for key in ("UNITY_MCP_HOST", "UNITY_MCP_HTTP_PORT", "UNITY_MCP_TIMEOUT", "UNITY_MCP_FORMAT", "UNITY_MCP_INSTANCE"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-c", COMMON + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("host", ["::1", "2001:db8::1", "[::1]", "127.0.0.1", "localhost"])
def test_public_cli_all_endpoints_preserve_host_target_auth_and_timeout(host, tmp_path):
    _run(f'''
# Given the real CLI/connection module and HTTPX parser with an inert transport.
host = {host!r}
def respond(request):
    requests.append(request)
    if request.url.path == "/health": return httpx.Response(200, json={{"status": "healthy"}})
    if request.url.path == "/api/instances": return httpx.Response(200, json={{"instances": [{{"project": "Owned", "hash": "owned"}}]}})
    if request.url.path == "/api/custom-tools": return httpx.Response(200, json={{"success": True, "tools": [{{"name": "owned_tool"}}]}})
    assert request.url.path == "/api/command"
    return httpx.Response(200, json={{"success": True, "data": {{"accepted": True}}}})
connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False)
for command in commands:
    before = len(requests)
    # When all public CLI endpoint paths run against the supplied host.
    result = runner.invoke(cli, ["--host", host, "--port", "18080", "--timeout", "17", "--instance", target, "--format", "json"] + command)
    # Then successful requests retain target/auth/timeout and valid IPv6 authority.
    assert result.exit_code == 0, (result.exception, result.output)
    assert json.loads(result.stdout)
    expected_paths = ["/health", "/api/instances"] if command == ["status"] else ["/api/instances"] if command == ["instances"] else ["/api/custom-tools"] if command[0] == "tool" else ["/api/command"]
    captured = requests[before:]
    assert [request.url.path for request in captured] == expected_paths
    for request in captured:
        assert request.url.host == host.strip("[]") and request.url.port == 18080
        path = request.url.path
        assert request.method == ("POST" if path == "/api/command" else "GET")
        expected_timeout = 5 if path == "/health" else 10 if path == "/api/instances" else 17
        assert request.extensions["timeout"] == {{name: expected_timeout for name in ("connect", "read", "write", "pool")}}
        assert request.headers.get(connection.LOCAL_AUTH_HEADER) == (None if path == "/health" else "owned-fixture-value")
        if path == "/api/command": assert json.loads(request.content) == {{"type": "fixture_query", "params": {{"zero": 0, "enabled": False}}, "unity_instance": target}}
        if path == "/api/custom-tools": assert dict(request.url.params) == {{"instance": target}}
    local = host in ("::1", "127.0.0.1", "localhost")
    assert ("Security Warning" in result.stderr) is (not local)
assert auth_calls == [(host, 18080)] * 4
''', tmp_path)


@pytest.mark.parametrize("failure", ["connect", "timeout", "http_status"])
def test_public_cli_ipv6_preserves_transport_error_exits(failure, tmp_path):
    _run(f'''
# Given a valid IPv6 request that fails at the inert HTTP transport boundary.
failure = {failure!r}
def respond(request):
    requests.append(request)
    if failure == "connect": raise httpx.ConnectError("owned connection failure", request=request)
    if failure == "timeout": raise httpx.ReadTimeout("owned timeout", request=request)
    return httpx.Response(503, json={{"error": "owned HTTP failure"}})
connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False)
for command in commands:
    before = len(requests)
    # When the public CLI invokes each of the four endpoint contracts.
    result = runner.invoke(cli, ["--host", "::1", "--port", "18080", "--timeout", "17", "--instance", target, "--format", "json"] + command)
    # Then the existing error output/exit contract is preserved.
    assert result.exit_code == 1 and len(requests) == before + 1, (result.exception, result.output)
    if command == ["status"]:
        assert json.loads(result.stdout)["success"] is False and result.stderr == ""
    else:
        assert result.stdout == ""
        expected = "Cannot connect" if failure == "connect" else "timed out" if failure == "timeout" else "HTTP error from server: 503"
        assert expected in result.stderr
''', tmp_path)


def test_ipv6_command_timeout_override_and_completed_error_metadata(tmp_path):
    _run('''
# Given completed Unity errors and a controlled launch credential.
reply = {"success": False, "error": "owned rejected", "hint": "retry", "data": {"reason": "reloading", "zero": 0, "enabled": False}}
def respond(request):
    requests.append(request)
    return httpx.Response(200, json=reply)
connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False)
# When the public raw command receives a completed Unity failure.
result = runner.invoke(cli, ["--host", "::1", "--format", "json", "--instance", target, "raw", "fixture_query", "{}"])
# Then its requested output and diagnostic payload survive with failed exit.
assert result.exit_code == 1 and result.stderr == ""
payload = json.loads(result.stdout)
for key, value in reply.items(): assert payload[key] == value
# The public connection wrapper also retains its explicit timeout override.
reply = {"success": True, "data": {"accepted": True}}
cfg = CLIConfig(host="::1", port=18080, timeout=17, unity_instance=target)
assert connection.run_command("fixture_query", {"zero": 0}, cfg, timeout=3)["success"] is True
assert requests[-1].extensions["timeout"] == {name: 3 for name in ("connect", "read", "write", "pool")}
assert auth_calls[-1] == ("::1", 18080) and cfg.timeout == 17
''', tmp_path)
