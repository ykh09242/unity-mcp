"""Job hints retain the effective local target through shell parsing and dispatch."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection, followup
from cli.utils.config import CLIConfig, get_config


@pytest.fixture
def wire(monkeypatch, tmp_path):
    for name in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv("UNITY_MCP_" + name, raising=False)
    monkeypatch.chdir(tmp_path)
    requests = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append((str(request.url), json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "job_id": "owned-job",
                    "status": "succeeded",
                    "native_control": {"released": False},
                },
            },
        )

    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    return requests


def project(root):
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.0")
    return root


@pytest.mark.parametrize(
    "command",
    [
        ["packages", "add", "com.fixture"],
        ["packages", "remove", "com.fixture"],
        ["packages", "embed", "com.fixture"],
        ["packages", "resolve"],
        ["build", "run", "--profile", "Assets/Build Profiles/Owned.asset"],
        ["build", "batch", "--targets", "windows64,linux64"],
        ["editor", "tests", "--async"],
        ["editor", "poll-test", "owned-job", "--wait", "0"],
        ["asset-gen", "generate-image", "--prompt", "fixture"],
    ],
)
def test_printed_followup_keeps_endpoint_target_guard_and_timeout(wire, tmp_path, command):
    root = project(tmp_path / "Owned Project")
    start = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--host",
            "127.0.0.1",
            "--port",
            "18080",
            "--timeout",
            "41",
            "--instance",
            "Owned Project@aaaaaaaaaaaaaaaa",
            "--expect-project",
            str(root),
            *command,
        ],
    )
    assert start.exit_code == 0, (start.output, start.exception)
    hint = next(
        line.split("with: ", 1)[1] for line in start.output.splitlines() if "with: " in line
    )
    polled = CliRunner().invoke(cli, shlex.split(hint)[1:])
    assert polled.exit_code == 0, (polled.output, polled.exception)
    assert len(wire) == 2
    assert wire[1][0] == wire[0][0] == "http://127.0.0.1:18080/api/context-command"
    for key in ("unity_instance", "expected_project"):
        assert wire[1][1][key] == wire[0][1][key]
    assert wire[1][1]["params"].get("job_id", wire[1][1]["params"].get("jobId")) == "owned-job"
    assert get_config().timeout == 41


def test_context_defaults_are_frozen_before_context_file_changes(wire, tmp_path):
    root = project(tmp_path / "Profile Project")
    context = root / ".unity-mcp.json"
    context.write_text(
        json.dumps(
            {
                "schema_version": "unity-mcp/cli-context@1",
                "port": 18081,
                "instance": "aaaaaaaaaaaaaaaa",
                "timeout": 43,
                "format": "table",
            }
        )
    )
    start = CliRunner().invoke(cli, ["--project-context", str(context), "packages", "resolve"])
    assert start.exit_code == 0, start.output
    hint = start.output.split("Observe with: ", 1)[1].splitlines()[0]
    context.write_text("invalid context")
    polled = CliRunner().invoke(cli, shlex.split(hint)[1:])
    assert polled.exit_code == 0, polled.output
    assert wire[1][0] == wire[0][0]
    assert wire[1][1]["unity_instance"] == wire[0][1]["unity_instance"]
    assert wire[1][1]["expected_project"] == {"root": str(root.resolve())}
    assert get_config().timeout == 43
    assert get_config().format == "table"


def test_platform_shell_preserves_apostrophes_metacharacters_and_spaced_arguments(wire, tmp_path):
    root = project(tmp_path / "Owner's Project $HOME; & safe")
    instance = "Owner's Project $(throw 'injected')@aaaaaaaaaaaaaaaa"
    config = CLIConfig(
        port=18082, timeout=47, unity_instance=instance, expected_project_root=str(root)
    )
    hint = followup.followup_command(config, ["packages", "status", "job 'quoted'; $HOME"])
    capture = tmp_path / "capture arguments.py"
    capture.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        assert shell, "Windows shell regression requires PowerShell"
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        script = (
            f"function unity-mcp {{ & {quote(sys.executable)} {quote(capture)} @args }}\n{hint}"
        )
        invocation = [shell, "-NoProfile", "-NonInteractive", "-Command", script]
    else:
        script = f'unity-mcp() {{ {shlex.quote(sys.executable)} {shlex.quote(str(capture))} "$@"; }}\n{hint}'
        invocation = ["/bin/sh", "-c", script]
    parsed = subprocess.run(invocation, capture_output=True, text=True, check=True, timeout=20)
    polled = CliRunner().invoke(cli, json.loads(parsed.stdout))
    assert polled.exit_code == 0, polled.output
    assert wire[0] == (
        "http://127.0.0.1:18082/api/context-command",
        {
            "type": "manage_packages",
            "params": {"action": "status", "job_id": "job 'quoted'; $HOME"},
            "unity_instance": instance,
            "expected_project": {"root": str(root.resolve())},
        },
    )


def test_posix_rendering_round_trips_all_arguments(monkeypatch):
    monkeypatch.setattr(followup, "os", SimpleNamespace(name="posix"))
    config = CLIConfig(unity_instance="Owner's $Project", verbose=True)
    args = shlex.split(followup.followup_command(config, ["packages", "status", "job;&' spaced"]))
    assert args[-3:] == ["packages", "status", "job;&' spaced"]
    assert args[args.index("--instance") + 1] == config.unity_instance
    assert "--verbose" in args
