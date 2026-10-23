"""Committed synthetic history exercises advisory drift and strict pin refreshes."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

import check_source_pin_drift as drift
from tests.test_update_source_pins import git, put


def commit(repo, message="fixture"):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def set_pin(repo, pin):
    source = drift.BASE + "/archive/" + pin + ".zip#subdirectory=Server"
    put(
        repo,
        "MCPForUnity/package.json",
        json.dumps(
            {
                "name": "com.ykh09242.unity-mcp",
                "version": "1",
                "mcpServerSource": source,
                "mcpSkillRevision": "a" * 40,
            }
        ),
    )
    put(
        repo,
        "manifest.json",
        json.dumps({"version": "1", "server": {"mcp_config": {"args": ["--from", source]}}}),
    )
    put(
        repo,
        "release-source-inputs.json",
        json.dumps(
            {
                "schema_version": 1,
                "repository": drift.BASE,
                "server_commit": pin,
                "skill_commit": "a" * 40,
                "server_source": source,
            }
        ),
    )
    put(repo, "Server/README.md", source)


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    put(repo, "Server/src/main.py", "# initial\n")
    put(repo, "Server/pyproject.toml", '[project]\nname="ykh09242-unity-mcp-server"\n')
    pin = commit(repo)
    set_pin(repo, pin)
    base = commit(repo)
    return repo, pin, base


def test_metadata_only_refresh_is_current_and_working_tree_is_ignored(history):
    repo, pin, base = history
    put(repo, "Server/src/main.py", "# uncommitted change\n")
    report = drift.inspect(repo, baseline=base, strict=True)
    assert (report["state"], report["server_commit"], report["checked_commit"]) == (
        "current",
        pin,
        base,
    )
    assert drift.exit_status(report) == 0


@pytest.mark.parametrize(
    "path",
    ["Server/src/main.py", "Server/uv.lock", "Server/Dockerfile", "Server/new-build-input.cfg"],
)
def test_runtime_and_new_packaging_inputs_are_stale(history, path):
    repo, _, base = history
    put(repo, path, "changed\n")
    commit(repo)
    report = drift.inspect(repo, baseline=base)
    assert report["state"] == "stale"
    assert report["changed_paths"] == [path]
    assert drift.exit_status(report) == 0
    assert drift.exit_status(drift.inspect(repo, strict=True)) == 1


def test_docs_tests_and_exact_revert_do_not_force_pin_churn(history):
    repo, _, _ = history
    put(repo, "Server/src/main.py", "changed\n")
    commit(repo)
    put(repo, "Server/src/main.py", "# initial\n")
    put(repo, "Server/tests/test_fixture.py", "# test\n")
    put(repo, "Server/README.md", "docs only\n")
    put(repo, "docs/a.md", "docs\n")
    commit(repo)
    assert drift.inspect(repo, strict=True)["state"] == "current"


def test_changed_pin_is_strict_and_does_not_create_self_reference(history):
    repo, _, base = history
    put(repo, "Server/src/main.py", "# new runtime\n")
    validated = commit(repo)
    set_pin(repo, validated)
    refreshed = commit(repo)
    report = drift.inspect(repo, baseline=base)
    assert report["pin_changed"] and report["requires_current"]
    assert report["state"] == "current" and drift.exit_status(report) == 0
    assert report["server_commit"] == validated != refreshed
    put(repo, "Server/src/main.py", "# additional runtime\n")
    commit(repo)
    report = drift.inspect(repo, baseline=base)
    assert report["state"] == "stale" and drift.exit_status(report) == 1


@pytest.mark.parametrize("pin", ["beta", "b" * 40, "0" * 40])
def test_moving_missing_and_zero_pin_fail_with_safe_action(history, pin):
    repo, _, _ = history
    set_pin(repo, pin)
    commit(repo)
    report = drift.inspect(repo)
    assert report["state"] == "unavailable" and drift.exit_status(report) == 2
    assert str(repo) not in json.dumps(report)
    assert "full_local_history" in report["next_action"]


@pytest.mark.parametrize("path", ["manifest.json", "release-source-inputs.json"])
def test_inconsistent_metadata_is_not_reported_current(history, path):
    repo, _, _ = history
    put(repo, path, "{}")
    commit(repo)
    assert drift.exit_status(drift.inspect(repo)) == 2


def test_missing_baseline_and_invalid_revision_are_unavailable(history):
    repo, _, _ = history
    assert drift.exit_status(drift.inspect(repo, baseline="b" * 40)) == 2
    assert drift.exit_status(drift.inspect(repo, "--help")) == 2


def test_truncation_limits_display_only_and_cli_reports_json(history):
    repo, _, _ = history
    for number in range(105):
        put(repo, f"Server/src/added-{number}.py", "# fixture\n")
    commit(repo)
    result = subprocess.run(
        [sys.executable, str(Path(drift.__file__)), "--repo", str(repo), "--require-current"],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["changed_path_count"] == 105
    assert len(report["changed_paths"]) == 100 and report["paths_truncated"]
