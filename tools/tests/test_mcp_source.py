"""Offline regressions for the package source switcher's Git metadata handling."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
import mcp_source  # noqa: E402


@pytest.fixture
def git_env(monkeypatch):
    # Test repositories must not inherit user remotes, hooks or signing settings.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("PYTHONUTF8", "1")
    return dict(os.environ)


def git(repo, *args):
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull,
         "-c", "commit.gpgsign=false", "-c", "user.name=Test",
         "-c", "user.email=test@example.invalid", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path, git_env):
    path = tmp_path / "checkout"
    path.mkdir()
    git(path, "init", "--initial-branch=topic")
    git(path, "commit", "--allow-empty", "-m", "first")
    return path


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "dependencies": {mcp_source.PKG_NAME: "old", "com.example.other": "1.2.3"},
        "testables": ["com.example.other"],
    }), encoding="utf-8")
    return path


def run_switch(repo, manifest, choice=None, *, input=None):
    command = [sys.executable, "-B", "-W", "error", str(_REPO_ROOT / "mcp_source.py"),
               "--repo", str(repo), "--manifest", str(manifest)]
    if choice is not None:
        command.extend(["--choice", choice])
    return subprocess.run(command, input=input, capture_output=True,
                          text=True, encoding="utf-8")


def assert_source(manifest, source):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["dependencies"][mcp_source.PKG_NAME] == source
    assert data["dependencies"]["com.example.other"] == "1.2.3"
    assert data["testables"] == ["com.example.other"]


def test_local_origin_fallback_has_only_one_path_and_revision(tmp_path):
    options = mcp_source.build_options(tmp_path, "topic", "file:/local/upstream.git")
    assert options[2][1] == options[0][1]
    assert options[2][1].count("?path=") == 1
    assert options[2][1].count("#") == 1


def test_remote_branch_and_local_path_options_remain_distinct(tmp_path):
    options = mcp_source.build_options(tmp_path, "feature/test", "https://example.invalid/repo.git")
    assert options[2][1] == "https://example.invalid/repo.git?path=/MCPForUnity#feature/test"
    assert options[3][1] == f"file:{(tmp_path / 'MCPForUnity').as_posix()}"
    assert options[0][1].endswith("#main")
    assert options[1][1].endswith("#beta")


@pytest.mark.parametrize("choice", ["1", "2", "4"])
@pytest.mark.parametrize("git_state", ["remote", "committed", "unborn", "absent"])
def test_nonremote_choices_work_without_git_metadata(repo, manifest, choice, git_state):
    if git_state == "remote":
        git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    elif git_state == "unborn":
        repo = repo.parent / "unborn"
        repo.mkdir()
        git(repo, "init", "--initial-branch=topic")
    elif git_state == "absent":
        repo = repo.parent / "plain"
        repo.mkdir()
    result = run_switch(repo, manifest, choice)
    assert result.returncode == 0, result.stderr
    expected = {
        "1": "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main",
        "2": "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#beta",
        "4": f"file:{(repo / 'MCPForUnity').as_posix()}",
    }[choice]
    assert_source(manifest, expected)


def test_interactive_local_choice_works_without_origin(repo, manifest):
    result = run_switch(repo, manifest, input="4\n")
    assert result.returncode == 0, result.stderr
    assert_source(manifest, f"file:{(repo / 'MCPForUnity').as_posix()}")


def test_remote_choice_without_origin_leaves_manifest_unchanged(repo, manifest):
    original = manifest.read_bytes()
    result = run_switch(repo, manifest, "3")
    assert result.returncode == 1
    assert "Error:" in result.stderr
    assert manifest.read_bytes() == original


def test_remote_choice_without_revision_leaves_manifest_unchanged(tmp_path, manifest, git_env):
    repo = tmp_path / "unborn"
    repo.mkdir()
    git(repo, "init", "--initial-branch=topic")
    git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    original = manifest.read_bytes()
    result = run_switch(repo, manifest, "3")
    assert result.returncode == 1
    assert "Error:" in result.stderr
    assert manifest.read_bytes() == original


def test_remote_choice_uses_current_branch(repo, manifest):
    git(repo, "remote", "add", "origin", "git@github.com:example/repo.git")
    result = run_switch(repo, manifest, "3")
    assert result.returncode == 0, result.stderr
    assert_source(manifest, "https://github.com/example/repo.git?path=/MCPForUnity#topic")


def test_local_origin_remote_choice_uses_upstream_fallback(repo, manifest):
    git(repo, "remote", "add", "origin", "file:/local/upstream.git")
    result = run_switch(repo, manifest, "3")
    assert result.returncode == 0, result.stderr
    assert_source(manifest, "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main")


def test_detached_head_remote_choice_pins_checked_out_commit(repo, manifest):
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "--allow-empty", "-m", "second")
    git(repo, "checkout", "--detach", first)
    git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    result = run_switch(repo, manifest, "3")
    assert result.returncode == 0, result.stderr
    assert_source(manifest, f"https://example.invalid/repo.git?path=/MCPForUnity#{first}")


def test_invalid_interactive_choice_leaves_manifest_unchanged(repo, manifest):
    git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    original = manifest.read_bytes()
    result = run_switch(repo, manifest, input="5\n")
    assert result.returncode == 1
    assert "Invalid selection" in result.stderr
    assert manifest.read_bytes() == original


def test_missing_dependency_leaves_manifest_unchanged(repo, manifest):
    git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    manifest.write_text('{"dependencies":{"com.example.other":"1.2.3"}}', encoding="utf-8")
    original = manifest.read_bytes()
    result = run_switch(repo, manifest, "2")
    assert result.returncode == 1
    assert "not found in manifest dependencies" in result.stderr
    assert manifest.read_bytes() == original
