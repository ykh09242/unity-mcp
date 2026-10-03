"""Malformed CLI input keeps human diagnostics off machine-readable stdout."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROGRAM = r'''
import copy
import json
import os
from pathlib import Path
import socket
import sys
Path.home = classmethod(lambda cls: Path(os.environ["HOME"]))
def denied(*args, **kwargs):
    raise AssertionError("Application network prohibited")
original_connect, original_pair = socket.socket.connect, socket.socketpair
def internal_pair(*args, **kwargs):
    socket.socket.connect = original_connect
    try:
        return original_pair(*args, **kwargs)
    finally:
        socket.socket.connect = denied
socket.socketpair = internal_pair
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection
client_type = httpx.AsyncClient
connection.read_local_auth_token = lambda host, port: "owned-fixture"
requests = []
reply = {"success": True, "data": {"changed": False, "count": 0}}
def respond(request):
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "owned"})
    if request.url.path == "/api/instances":
        return httpx.Response(200, json={"instances": []})
    assert request.url.path == "/api/command"
    body = json.loads(request.content)
    assert body["unity_instance"] == "Selected@owned"
    requests.append(body)
    return httpx.Response(200, json=copy.deepcopy(reply))
connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False)
failures = []
for value, syntax_error in [("{'missing':}", True), ('{"unterminated":"x}', True),
    ("[]", False), ("null", False), ("false", False)]:
    # Given malformed syntax or the wrong JSON container for component properties.
    requests.clear()
    # When the complete public command parses the argument.
    result = CliRunner().invoke(cli, ["--instance", "Selected@owned", "--format", sys.argv[1],
        "component", "modify", "Owned", "Fixture", "--properties", value])
    # Then every diagnostic is on stderr; no request is dispatched.
    passed = result.exit_code == 1 and not requests and result.stdout == "" and "Invalid JSON for properties" in result.stderr
    if syntax_error:
        passed = passed and "ℹ️  Example: --params" in result.stderr and "ℹ️  Tip: wrap JSON in single quotes" in result.stderr
    if not passed:
        failures.append({"input": value, "exit": result.exit_code, "stdout": result.stdout, "stderr": result.stderr, "exception": repr(result.exception)})
for value, expected in [("{'label':'True False', 'enabled':True}", {"label": "True False", "enabled": True}),
    ('{"label":"apostrophe \' True", "enabled":True}', {"label": "apostrophe ' True", "enabled": True}),
    ('{"label":"False","enabled":false}', {"label": "False", "enabled": False})]:
    # Given supported shell quoting/Python booleans and literal string values.
    requests.clear()
    # When successful repair is needed (or valid JSON is supplied).
    result = CliRunner().invoke(cli, ["--instance", "Selected@owned", "--format", sys.argv[1],
        "component", "modify", "Owned", "Fixture", "--properties", value])
    # Then the original payload and output/exit behavior survive.
    assert result.exit_code == 0 and result.stderr == "", (result.exception, result.output)
    assert len(requests) == 1 and requests[0]["params"]["properties"] == expected
    if sys.argv[1] == "json":
        assert json.loads(result.stdout) == reply
    else:
        assert "Modified Fixture" in result.stdout
# The default print_info consumers still write normal informational output to stdout.
result = CliRunner().invoke(cli, ["status"])
assert result.exit_code == 0 and result.stderr == "" and "ℹ️  No Unity instances currently connected" in result.stdout
print(json.dumps({"format": sys.argv[1], "calls": 9, "failures": failures}))
assert not failures, failures
'''


@pytest.mark.parametrize("output_format", ["json", "text", "table"])
def test_input_diagnostics_do_not_pollute_stdout(tmp_path, output_format):
    env = {key: value for key, value in os.environ.items() if not key.startswith("UNITY_MCP_")}
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "TEMP", "TMP", "UNITY_MCP_LOG_DIR", "UNITY_MCP_STATUS_DIR"):
        env[key] = str(tmp_path)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run([sys.executable, "-B", "-c", PROGRAM, output_format], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
