"""Exercise immutable skill pin checks only against owned synthetic Git repositories."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]


def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_skill_pin", ROOT / "tools/check_skill_pin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(repo),
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init")
    for relative in (".claude/skills/unity-mcp-skill", "unity-mcp-skill"):
        skill = repo / relative
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Fixture\n", encoding="utf-8")
        (skill / "references").mkdir()
        (skill / "references/guide.md").write_text("Reference\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "verified skill content")
    (repo / "MCPForUnity").mkdir()
    (repo / "MCPForUnity/package.json").write_text(
        json.dumps({"mcpSkillRevision": git(repo, "rev-parse", "HEAD")}), encoding="utf-8"
    )
    return repo


def test_synthetic_preceding_commit_verifies_both_mirrors(checkout: Path) -> None:
    assert checker().check_pin(checkout, verify_git=True) == []


@pytest.mark.parametrize("mirror", [".claude/skills/unity-mcp-skill", "unity-mcp-skill"])
def test_changed_or_missing_content_requires_new_verified_pin(checkout: Path, mirror: str) -> None:
    (checkout / mirror / "references/guide.md").write_text("Changed\n", encoding="utf-8")
    issues = checker().check_pin(checkout, verify_git=True)
    assert issues
    assert "references/guide.md" in "\n".join(issues)


def test_content_newlines_follow_existing_mirror_contract(checkout: Path) -> None:
    (checkout / "unity-mcp-skill/SKILL.md").write_bytes(b"# Fixture\r\n")
    assert checker().check_pin(checkout, verify_git=True) == []


@pytest.mark.parametrize("revision", [None, True, "beta", "0" * 40, "a" * 39])
def test_invalid_revision_is_reported_without_git(
    checkout: Path, revision: str | bool | None
) -> None:
    (checkout / "MCPForUnity/package.json").write_text(
        json.dumps({"mcpSkillRevision": revision}), encoding="utf-8"
    )
    assert "mcpSkillRevision" in "\n".join(checker().check_pin(checkout))


def test_actual_git_object_lookup_requires_opt_in(checkout: Path) -> None:
    (checkout / "MCPForUnity/package.json").write_text(
        json.dumps({"mcpSkillRevision": "a" * 40}), encoding="utf-8"
    )
    assert checker().check_pin(checkout) == []
    assert checker().check_pin(checkout, verify_git=True)


def test_tree_object_is_not_a_commit_revision(checkout: Path) -> None:
    (checkout / "MCPForUnity/package.json").write_text(
        json.dumps({"mcpSkillRevision": git(checkout, "rev-parse", "HEAD^{tree}")}),
        encoding="utf-8",
    )
    assert "commit" in "\n".join(checker().check_pin(checkout, verify_git=True))
