"""Skill provenance CI checks exact content after bounded shallow-history preparation."""

import json
from pathlib import Path
import subprocess

import pytest
import yaml

import check_skill_pin as checker
import prepare_source_pin_history as preparation
from check_release_evidence import EvidenceError
from tests.test_check_skill_pin import checkout, git


@pytest.fixture
def skill_history(checkout):
    metadata = checkout / "MCPForUnity/package.json"
    package = json.loads(metadata.read_text(encoding="utf-8"))
    package["name"] = "com.ykh09242.unity-mcp"
    metadata.write_text(json.dumps(package), encoding="utf-8")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "select skill pin")
    return checkout, package["mcpSkillRevision"], git(checkout, "rev-parse", "HEAD")


def test_available_skill_pin_skips_network_and_uses_committed_metadata(skill_history, monkeypatch):
    repo, pin, checked = skill_history
    (repo / "MCPForUnity/package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(preparation, "fetch_pin", lambda *_: pytest.fail("unexpected network"))
    result = preparation.prepare_skill(repo, checked)
    assert (result.checked_commit, result.skill_commit, result.fetched) == (checked, pin, False)


def test_shallow_skill_history_restores_verification_without_changing_refs(
    skill_history, tmp_path, monkeypatch
):
    source, pin, checked = skill_history
    repo = tmp_path / "shallow"
    git(tmp_path, "clone", "--depth=1", "--no-tags", source.as_uri(), str(repo))
    assert checker.check_pin(repo, verify_git=True)
    refs = git(repo, "for-each-ref")
    head = git(repo, "rev-parse", "HEAD")
    fetch_head = repo / ".git/FETCH_HEAD"
    before = fetch_head.read_bytes() if fetch_head.exists() else None
    calls = []
    real_run = subprocess.run

    def local_fetch(command, **kwargs):
        if "fetch" in command:
            assert command[-2:] == [preparation.FORK_GIT_URL, pin]
            calls.append(pin)
            command = [*command[:-2], source.as_uri(), pin]
        return real_run(command, **kwargs)

    monkeypatch.setattr(preparation.subprocess, "run", local_fetch)
    result = preparation.prepare_skill(repo, checked)
    assert result.fetched and calls == [pin]
    assert checker.check_pin(repo, verify_git=True) == []
    assert git(repo, "for-each-ref") == refs and git(repo, "rev-parse", "HEAD") == head
    assert (fetch_head.read_bytes() if fetch_head.exists() else None) == before
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("mirror", [*checker.SKILLS, "both"])
def test_available_stale_skill_pin_fails_content_verification(skill_history, monkeypatch, mirror):
    repo, _, _ = skill_history
    for folder in checker.SKILLS if mirror == "both" else (mirror,):
        (repo / folder / "references/guide.md").write_text("changed\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "change skill without refreshing pin")
    monkeypatch.setattr(preparation, "fetch_pin", lambda *_: pytest.fail("unexpected network"))
    assert not preparation.prepare_skill(repo).fetched
    assert checker.check_pin(repo) == []
    assert "Skill pin does not match intended content" in "\n".join(
        checker.check_pin(repo, verify_git=True)
    )


def test_skill_history_cli_reports_independent_pin(skill_history, capsys):
    repo, pin, checked = skill_history
    assert preparation.main(["--repo", str(repo), "--component", "skill"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "state": "available",
        "checked_commit": checked,
        "skill_commit": pin,
        "fetched": False,
    }


def test_wrong_package_identity_never_fetches(skill_history, monkeypatch):
    repo, pin, _ = skill_history
    (repo / "MCPForUnity/package.json").write_text(
        json.dumps({"name": "other-package", "mcpSkillRevision": pin}), encoding="utf-8"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "wrong package identity")
    monkeypatch.setattr(preparation, "fetch_pin", lambda *_: pytest.fail("unexpected network"))
    with pytest.raises(EvidenceError, match="Wrong Unity package identity"):
        preparation.prepare_skill(repo)


@pytest.mark.parametrize("pin", [None, True, "beta", "0" * 40, "../untrusted"])
def test_invalid_skill_pin_never_fetches(skill_history, monkeypatch, pin):
    repo, _, _ = skill_history
    metadata = repo / "MCPForUnity/package.json"
    metadata.write_text(
        json.dumps({"name": "com.ykh09242.unity-mcp", "mcpSkillRevision": pin}), encoding="utf-8"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "invalid skill pin")
    monkeypatch.setattr(preparation, "fetch_pin", lambda *_: pytest.fail("unexpected network"))
    with pytest.raises(EvidenceError):
        preparation.prepare_skill(repo)


@pytest.mark.parametrize("failure", ["nonexistent", "timeout", "wrong_object", "still_missing"])
def test_unavailable_skill_pin_fails_with_sanitized_diagnostics(
    skill_history, monkeypatch, capsys, failure
):
    repo, _, _ = skill_history
    pin = "a" * 40 if failure != "wrong_object" else git(repo, "rev-parse", "HEAD^{tree}")
    metadata = repo / "MCPForUnity/package.json"
    metadata.write_text(
        json.dumps({"name": "com.ykh09242.unity-mcp", "mcpSkillRevision": pin}), encoding="utf-8"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "unavailable skill pin")
    calls = []

    def unavailable(*_):
        calls.append(pin)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("private-command", 60)
        if failure != "still_missing":
            raise EvidenceError("private-fetch-diagnostics")

    monkeypatch.setattr(preparation, "fetch_pin", unavailable)
    assert preparation.main(["--repo", str(repo), "--component", "skill"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["state"] == "unavailable"
    assert "private" not in output and str(repo) not in output
    assert calls == ([] if failure == "wrong_object" else [pin])
    assert checker.check_pin(repo, verify_git=True)


def test_workflow_always_verifies_skill_git_content_and_covers_all_inputs():
    path = Path(__file__).resolve().parents[2] / ".github/workflows/source-pin-drift.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    expected = {
        ".claude/skills/unity-mcp-skill/**",
        "unity-mcp-skill/**",
        "Server/**",
        "MCPForUnity/package.json",
        "manifest.json",
        "release-source-inputs.json",
        "tools/check_skill_pin.py",
        "tools/tests/test_check_skill_pin.py",
        "tools/tests/test_prepare_skill_pin_history.py",
    }
    for event in ("push", "pull_request"):
        assert expected <= set(triggers[event]["paths"])
    steps = workflow["jobs"]["source_pin"]["steps"]
    history = next(i for i, step in enumerate(steps) if "--component skill" in step.get("run", ""))
    verify = next(
        i
        for i, step in enumerate(steps)
        if "check_skill_pin.py --verify-git" in step.get("run", "")
    )
    assert history < verify
    assert "always()" in steps[history]["if"]
    assert "always()" in steps[verify]["if"]
    assert not steps[verify].get("continue-on-error", False)
    assert "check_source_pin_drift.py" in "\n".join(step.get("run", "") for step in steps)
