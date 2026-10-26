"""History preparation fetches only validated missing pins; the checker stays offline."""

import json
from pathlib import Path
import subprocess

import pytest

import check_source_pin_drift as drift
import prepare_source_pin_history as preparation
from check_release_evidence import EvidenceError
from tests.test_check_source_pin_drift import commit, history, set_pin
from tests.test_update_source_pins import git, put


def missing_history(history, tmp_path):
    source, pin, checked = history
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "fetch", "--depth=1", "--no-tags", source.as_uri(), checked)
    assert drift.inspect(repo, checked)["state"] == "unavailable"
    return repo, source, pin, checked


def test_available_pin_skips_network_and_ignores_uncommitted_metadata(history, monkeypatch):
    repo, pin, checked = history
    put(repo, "manifest.json", "{}")
    monkeypatch.setattr(preparation, "fetch_pin", lambda *_: pytest.fail("unexpected network"))
    assert preparation.prepare(repo) == preparation.PreparedHistory(checked, pin, False)


def test_missing_pin_restores_offline_comparison_without_changing_refs(
    history, tmp_path, monkeypatch
):
    repo, source, pin, checked = missing_history(history, tmp_path)
    refs = git(repo, "for-each-ref")
    fetch_head = (repo / ".git/FETCH_HEAD").read_bytes()
    fetched = []

    def local_fetch(target, selected):
        fetched.append(selected)
        git(
            target,
            "fetch",
            "--depth=1",
            "--no-tags",
            "--no-write-fetch-head",
            source.as_uri(),
            selected,
        )

    monkeypatch.setattr(preparation, "fetch_pin", local_fetch)
    assert preparation.prepare(repo, checked) == preparation.PreparedHistory(checked, pin, True)
    assert fetched == [pin]
    report = drift.inspect(repo, checked, checked)
    assert report["state"] == "current" and drift.exit_status(report) == 0
    assert git(repo, "for-each-ref") == refs
    assert (repo / ".git/FETCH_HEAD").read_bytes() == fetch_head
    assert sorted(p.name for p in repo.iterdir()) == [".git"]


@pytest.mark.parametrize(
    "path", ["MCPForUnity/package.json", "manifest.json", "release-source-inputs.json"]
)
def test_inconsistent_metadata_never_fetches(history, monkeypatch, path):
    repo, _, _ = history
    put(repo, path, "{}")
    commit(repo)
    monkeypatch.setattr(preparation, "fetch_pin", lambda *_: pytest.fail("unexpected network"))
    with pytest.raises(EvidenceError):
        preparation.prepare(repo)


@pytest.mark.parametrize("pin", ["beta", "0" * 40, "../credential-bearing-input"])
def test_noncanonical_pins_never_fetch(history, monkeypatch, pin):
    repo, _, _ = history
    set_pin(repo, pin)
    commit(repo)
    monkeypatch.setattr(preparation, "fetch_pin", lambda *_: pytest.fail("unexpected network"))
    with pytest.raises(EvidenceError):
        preparation.prepare(repo)


def test_existing_noncommit_pin_is_rejected_without_network(history, monkeypatch):
    repo, _, _ = history
    blob = git(repo, "rev-parse", "HEAD:Server/src/main.py")
    set_pin(repo, blob)
    commit(repo)
    monkeypatch.setattr(preparation, "fetch_pin", lambda *_: pytest.fail("unexpected network"))
    with pytest.raises(EvidenceError):
        preparation.prepare(repo)


@pytest.mark.parametrize("failure", ["failed", "timeout", "missing_after_fetch"])
def test_network_failure_stays_unavailable_with_sanitized_diagnostics(
    history, tmp_path, monkeypatch, capsys, failure
):
    repo, _, _, checked = missing_history(history, tmp_path)

    def fail_fetch(*_):
        if failure == "failed":
            raise EvidenceError("credential-bearing-private-output " + str(repo))
        if failure == "timeout":
            raise subprocess.TimeoutExpired("private-command", 60)

    monkeypatch.setattr(preparation, "fetch_pin", fail_fetch)
    assert preparation.main(["--repo", str(repo), "--revision", checked]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["state"] == "unavailable"
    assert str(repo) not in output and "private" not in output
    assert drift.inspect(repo, checked)["state"] == "unavailable"


def test_fetch_uses_fixed_fork_exact_sha_no_credentials_and_bounded_timeout(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(preparation.subprocess, "run", run)
    pin = "a" * 40
    preparation.fetch_pin(tmp_path, pin)
    command, kwargs = calls[0]
    assert command[-2:] == ["https://github.com/ykh09242/unity-mcp.git", pin]
    for flag in ("--depth=1", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules"):
        assert flag in command
    assert "credential.helper=" in command and "http.extraHeader=" in command
    assert kwargs["timeout"] == 60 and kwargs["capture_output"]
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GCM_INTERACTIVE"] == "Never"


def test_failed_git_fetch_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        preparation.subprocess,
        "run",
        lambda command, **_: subprocess.CompletedProcess(command, 1, stderr=b"private"),
    )
    with pytest.raises(EvidenceError, match="Pinned history fetch failed"):
        preparation.fetch_pin(tmp_path, "a" * 40)


def test_workflow_prepares_history_before_offline_comparison():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/source-pin-drift.yml"
    ).read_text()
    assert workflow.index("python3 tools/prepare_source_pin_history.py") < workflow.index(
        "python3 tools/check_source_pin_drift.py"
    )
    assert workflow.count("- tools/prepare_source_pin_history.py") == 2
    assert workflow.count("- tools/tests/test_prepare_source_pin_history.py") == 2
    assert "persist-credentials: false" in workflow
