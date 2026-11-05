"""Candidate image validation contracts; no daemon or publication needed."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "check_container", ROOT / "tools/check_container.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path",
    [
        "Server/Dockerfile",
        "Server/uv.lock",
        "Server/pyproject.toml",
        "Server/src/main.py",
        "Server/README.md",
        "Server/LICENSE",
        "MCPForUnity/package.json",
        ".dockerignore",
        "docker-compose.yml",
        "docker-compose.remote.yml",
        "tools/check_server_startup.py",
        "tools/check_container.py",
        ".github/workflows/python-tests.yml",
    ],
)
def test_actual_build_context_and_smoke_inputs_require_candidate(path):
    assert load_helper().relevant(path)


@pytest.mark.parametrize("path", ["website/docs/index.md", "Server/tests/test_example.py"])
def test_excluded_inputs_do_not_build_candidate(path):
    assert not load_helper().relevant(path)


@pytest.mark.parametrize("failure", [None, "build", "cli", "registration", "timeout"])
def test_build_and_installed_probes_are_bounded_fail_closed_and_nonpublishing(monkeypatch, failure):
    helper = load_helper()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert 0 < kwargs["timeout"] <= 600
        phase = (
            "build"
            if command[1] == "build"
            else ("registration" if "--entrypoint" in command else "cli")
        )
        if command[1] != "rm" and (phase == failure or failure == "timeout" and phase == "cli"):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="Unity MCP (ykh09242)\n")

    monkeypatch.setattr(helper.subprocess, "run", run)
    if failure is None:
        helper.validate(ROOT)
    else:
        with pytest.raises((subprocess.CalledProcessError, subprocess.TimeoutExpired)):
            helper.validate(ROOT)
    commands = [command for command, _ in calls]
    assert commands[0][1] == "build"
    assert "--push" not in sum(commands, [])
    assert not any(command[1] in {"push", "login"} for command in commands)
    for command in commands:
        if command[1] == "run":
            assert command[command.index("--network") + 1] == "none"
            name = command[command.index("--name") + 1]
            assert ["docker", "rm", "--force", name] in commands
    if failure in {None, "registration"}:
        registration = next(command for command in commands if "--entrypoint" in command)
        assert "/app/Server/.venv/bin/python" in registration
        assert "-I" in registration
        assert "/tmp/check_server_startup.py" in registration
        assert any("check_server_startup.py" in arg and "readonly" in arg for arg in registration)


def test_python_workflow_runs_candidate_with_no_credentials_or_publication():
    workflow = yaml.safe_load((ROOT / ".github/workflows/python-tests.yml").read_text())
    job = workflow["jobs"]["container"]
    assert job["permissions"] == {"contents": "read"}
    checkout = job["steps"][0]
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["ref"] == "${{ inputs.ref || github.sha }}"
    text = str(job)
    assert "tools/check_container.py scope" in text
    assert "tools/check_container.py validate" in text
    assert "secrets." not in text
    assert "upload-artifact" not in text
    assert "login-action" not in text


@pytest.mark.parametrize(
    "path,expected",
    [
        ("Server/src/main.py", True),
        ("docker-compose.yml", True),
        ("Server/tests/test_example.py", False),
    ],
)
def test_scope_inspects_complete_git_event_range(tmp_path, path, expected):
    helper = load_helper()

    def git(*args):
        return (
            subprocess.run(
                ["git", *args], cwd=tmp_path, capture_output=True, check=True, timeout=20
            )
            .stdout.decode()
            .strip()
        )

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "base")
    base = git("rev-parse", "HEAD")
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("fixture\n", encoding="utf-8")
    git("add", path)
    git("-c", "commit.gpgsign=false", "commit", "-qm", "input")
    # A later unrelated commit must not hide earlier relevant inputs in one push.
    git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "latest")
    event = tmp_path / "event.json"
    for payload in ({"before": base}, {"pull_request": {"base": {"sha": base}}}):
        event.write_text(json.dumps(payload), encoding="utf-8")
        assert helper.selected(event, tmp_path) is expected
    event.write_text('{"before": "' + "1" * 40 + '"}', encoding="utf-8")
    assert helper.selected(event, tmp_path)


def test_negative_controls_require_actual_rejection(tmp_path, monkeypatch):
    helper = load_helper()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0 if command[1] == "rm" else 1)

    monkeypatch.setattr(helper.subprocess, "run", run)
    helper.negative_controls(tmp_path, "candidate:fixture")
    assert calls[0][1] == "build"
    assert "--entrypoint" in calls[1]
    assert calls[2][:3] == ["docker", "rm", "--force"]
    monkeypatch.setattr(
        helper.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0)
    )
    with pytest.raises(RuntimeError, match="Invalid COPY"):
        helper.negative_controls(tmp_path, "candidate:fixture")
