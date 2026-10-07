"""Real CLI and SDK callers preserve job failures that have no test result."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


SETUP = r"""
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
def failed_job(error):
    # Native ClearStuckJob and initialization timeout have no TestRunResult.
    return {"success": True, "message": "Test job status retrieved.", "data": {
        "job_id": "owned-job", "status": "failed", "mode": "EditMode",
        "error": error, "result": None}}
"""

CLI_PROGRAM = (
    SETUP
    + r"""
import httpx
from click.testing import CliRunner
from cli.main import cli
from cli.utils import connection
import cli.commands.editor as editor
from types import SimpleNamespace
client_type = httpx.AsyncClient
connection.read_local_auth_token = lambda host, port: "owned-fixture"
requests = []
reply = None
def respond(request):
    body = json.loads(request.content)
    assert request.url.path == "/api/command"
    assert body["unity_instance"] == "Selected@owned"
    requests.append(body)
    if body["type"] == "run_tests":
        return httpx.Response(200, json={"success": True, "data": {
            "job_id": "owned-job", "status": "running"}})
    assert body["type"] == "get_test_job"
    assert body["params"] == {"job_id": "owned-job", "includeDetails": True, "includeFailedTests": True}
    return httpx.Response(200, json=copy.deepcopy(reply))
connection.httpx.AsyncClient = lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False)
cases = [
    ("manual-clear", failed_job("Job cleared manually (stuck or orphaned)"), "Job cleared manually (stuck or orphaned)"),
    ("initialization-timeout", failed_job("Test job failed to initialize (tests did not start within timeout)"), "Test job failed to initialize"),
    ("no-error", failed_job(None), "No test results available"),
    ("assertion-failure", {"success": True, "data": {"job_id": "owned-job", "status": "failed", "result": {"summary": {"failed": 2}}}}, "Tests failed: 2 failures"),
    ("succeeded", {"success": True, "data": {"job_id": "owned-job", "status": "succeeded"}}, "Tests completed successfully"),
    ("cancelled", {"success": True, "data": {"job_id": "owned-job", "status": "cancelled", "result": None}}, "cancelled"),
    ("running", {"success": True, "data": {"job_id": "owned-job", "status": "running", "progress": {"completed": 0, "total": 2}}}, "running"),
    ("unknown", {"success": False, "error": "Unknown job_id.", "hint": "retry", "data": {"job_id": "owned-job"}}, "Unknown job_id."),
]
failures = []
count = 0
for label, reply, expected in cases:
    for output_format in ("text", "table", "json"):
        commands = [["editor", "poll-test", "owned-job", "--wait", "0"]]
        if label in ("manual-clear", "initialization-timeout", "assertion-failure", "succeeded", "cancelled", "unknown"):
            commands += [["editor", "poll-test", "owned-job", "--wait", "1"], ["editor", "tests", "--wait", "1"]]
        for command in commands:
            now = [0.0]
            editor.time = SimpleNamespace(monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay))
            requests.clear()
            result = CliRunner().invoke(cli, ["--instance", "Selected@owned", "--format", output_format, *command, "--details", "--failed-only"])
            expected_exit = 0 if reply["success"] else 1
            passed = result.exit_code == expected_exit and len(requests) == (2 if command[1] == "tests" else 1)
            if output_format == "json":
                passed = passed and json.loads(result.stdout) == reply
            elif output_format == "table" and label == "cancelled":
                passed = passed and "Key" in result.stdout
            else:
                passed = passed and expected in result.stdout + result.stderr
            if not passed:
                failures.append({"label": label, "format": output_format, "command": command, "exit": result.exit_code,
                    "exception": repr(result.exception), "stdout": result.stdout, "stderr": result.stderr})
            count += 1
print(json.dumps({"calls": count, "failures": failures}))
assert not failures, failures
"""
)

SDK_PROGRAM = (
    SETUP
    + r"""
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from core.config import config
import importlib
jobs = importlib.import_module("services.tools.run_tests")
from transport.plugin_hub import PluginHub
class Selected(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state("unity_instance", "Selected@owned")
        return await call_next(context)
async def main():
    config.transport_mode = "http"
    config.http_remote_hosted = False
    app = FastMCP("test-job-result-contract")
    app.add_middleware(Selected())
    app.tool(name="get_test_job")(jobs.get_test_job)
    cases = [failed_job("Job cleared manually (stuck or orphaned)"),
        failed_job("Test job failed to initialize (tests did not start within timeout)"),
        {"success": True, "data": {"job_id": "owned-job", "status": "failed", "result": {
            "mode": "EditMode", "summary": {"total": 2, "passed": 0, "failed": 2, "skipped": 0, "durationSeconds": .1, "resultState": "Failed"}}}},
        {"success": True, "data": {"job_id": "owned-job", "status": "succeeded"}},
        {"success": True, "data": {"job_id": "owned-job", "status": "cancelled", "result": None}},
        {"success": True, "data": {"job_id": "owned-job", "status": "running", "progress": {"editor_is_focused": True}}},
        {"success": False, "error": "Unknown job_id.", "hint": "retry", "data": {"job_id": "owned-job"}}]
    count = 0
    async with Client(app, mode=sys.argv[1]) as client:
        for reply in cases:
            waits = [0] if reply.get("data", {}).get("status") == "running" else [0, 1]
            for wait in waits:
                requests = []
                async def send(instance, command, params, **kwargs):
                    assert instance == "Selected@owned" and command == "get_test_job"
                    assert params == {"job_id": "owned-job", "includeDetails": True, "includeFailedTests": True}
                    requests.append(copy.deepcopy(params))
                    return copy.deepcopy(reply)
                PluginHub.send_command_for_instance = send
                result = await client.call_tool("get_test_job", {"job_id": "owned-job", "wait_timeout": wait, "include_details": True, "include_failed_tests": True})
                payload = json.loads(result.content[0].text)
                assert len(requests) == 1 and payload["success"] is reply["success"]
                if reply["success"]:
                    assert payload["data"]["status"] == reply["data"]["status"]
                    assert payload["data"]["error"] == reply["data"].get("error")
                    if reply["data"].get("result") is None:
                        assert payload["data"]["result"] is None
                    else:
                        assert payload["data"]["result"]["summary"] == reply["data"]["result"]["summary"]
                else:
                    assert payload["error"] == reply["error"] and payload["hint"] == "retry" and payload["data"] == reply["data"]
                count += 1
    print(json.dumps({"protocol": sys.argv[1], "calls": count}))
anyio.run(main)
"""
)


def _run(tmp_path, program, *arguments):
    env = {key: value for key, value in os.environ.items() if not key.startswith("UNITY_MCP_")}
    for key in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_DATA_HOME",
        "UNITY_MCP_LOG_DIR",
        "TEMP",
        "TMP",
    ):
        env[key] = str(tmp_path)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, *arguments],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_test_job_results_without_summary(tmp_path):
    _run(tmp_path, CLI_PROGRAM)


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
def test_sdk_test_job_results_without_summary(tmp_path, protocol):
    _run(tmp_path, SDK_PROGRAM, protocol)
