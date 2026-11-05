"""Execute delivery hooks only in synthetic repositories; never launch Unity."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
ZERO = "0" * 40
SOURCE = "MCPForUnity/Editor/Example.cs"


def git(repo, *args):
    return (
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True, timeout=20)
        .stdout.decode()
        .strip()
    )


def shell(repo, script, *args, stdin=""):
    result = subprocess.run(
        [BASH, script, *args],
        cwd=repo,
        input=stdin.encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=20,
    )
    return subprocess.CompletedProcess(
        result.args, result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8")
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    root = tmp_path / "repository with spaces"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    for relative in ("tools/hooks/pre-push", "tools/install-hooks.sh"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
        target.chmod(0o755)
    checker = root / "tools/check-unity-versions.sh"
    checker.write_text(
        "#!/usr/bin/env bash\ngit rev-parse HEAD >> .checked-shas\n"
        "if [[ -f .mutate ]]; then echo changed >> MCPForUnity/Editor/Example.cs; fi\n"
        "if [[ -f .create-lock ]]; then\n"
        "  mkdir -p TestProjects/UnityMCPTests/Packages\n"
        "  printf '{\"dependencies\":{}}\\n' > TestProjects/UnityMCPTests/Packages/packages-lock.json\n"
        "fi\n",
        encoding="utf-8",
        newline="\n",
    )
    checker.chmod(0o755)
    (root / SOURCE).parent.mkdir(parents=True)
    (root / SOURCE).write_text("initial\n", encoding="utf-8")
    (root / ".gitignore").write_text(
        "TestProjects/UnityMCPTests/Packages/packages-lock.json\n", encoding="utf-8"
    )
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "initial")
    return root


def push(repo, *shas, base=ZERO):
    return shell(
        repo,
        "tools/hooks/pre-push",
        stdin="".join(
            f"refs/heads/local{index} {sha} refs/heads/remote{index} {base}\n"
            for index, sha in enumerate(shas)
        ),
    )


def test_clean_head_reports_actual_checked_sha(repo):
    sha = git(repo, "rev-parse", "HEAD")
    result = push(repo, sha, sha)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sha in result.stdout
    assert (repo / ".checked-shas").read_text().splitlines() == [sha]


@pytest.mark.parametrize("creation", ["already-present", "checker-creates"])
def test_generated_project_lock_is_allowed_and_preserved(repo, creation):
    lock = repo / "TestProjects/UnityMCPTests/Packages/packages-lock.json"
    expected = '{"dependencies":{}}\n'
    if creation == "already-present":
        lock.parent.mkdir(parents=True)
        lock.write_text(expected, encoding="utf-8")
        git(repo, "check-ignore", lock.relative_to(repo).as_posix())
    else:
        (repo / ".create-lock").touch()
    sha = git(repo, "rev-parse", "HEAD")
    index = (repo / ".git/index").read_bytes()
    result = push(repo, sha)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / ".checked-shas").read_text().splitlines() == [sha]
    assert lock.read_text() == expected
    assert (repo / ".git/index").read_bytes() == index


@pytest.mark.parametrize("order", ["old-first", "head-first"])
def test_different_branch_and_multi_ref_push_refuse_before_checker(repo, order):
    old = git(repo, "rev-parse", "HEAD")
    (repo / SOURCE).write_text("different commit\n", encoding="utf-8")
    git(repo, "add", SOURCE)
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "second")
    head = git(repo, "rev-parse", "HEAD")
    shas = (old, head) if order == "old-first" else (head, old)
    result = push(repo, *shas)
    assert result.returncode != 0
    assert "HEAD" in result.stderr
    assert not (repo / ".checked-shas").exists()


@pytest.mark.parametrize(
    "state", ["unstaged", "staged", "untracked", "checker", "assume-unchanged", "skip-worktree"]
)
def test_dirty_relevant_inputs_refuse_without_changing_index_or_source(repo, state):
    sha = git(repo, "rev-parse", "HEAD")
    target = repo / ("tools/check-unity-versions.sh" if state == "checker" else SOURCE)
    if state == "untracked":
        target = target.with_name("Untracked.cs")
    target.write_text("local fix\n", encoding="utf-8")
    if state == "staged":
        git(repo, "add", SOURCE)
    if state in {"assume-unchanged", "skip-worktree"}:
        git(repo, "update-index", f"--{state}", SOURCE)
    index = (repo / ".git/index").read_bytes()
    result = push(repo, sha)
    assert result.returncode != 0
    assert "dirty" in result.stderr.lower()
    assert target.read_text() == "local fix\n"
    assert (repo / ".git/index").read_bytes() == index
    assert not (repo / ".checked-shas").exists()


def test_unknown_remote_base_fails_closed(repo):
    result = push(repo, git(repo, "rev-parse", "HEAD"), base="1" * 40)
    assert result.returncode != 0
    assert not (repo / ".checked-shas").exists()


@pytest.mark.parametrize("ignore", ["project-temp", "custom-exclude"])
def test_ignored_imported_source_refuses_without_mutating_git_or_local_fix(repo, ignore):
    relative = (
        "TestProjects/UnityMCPTests/Assets/Temp/Extra.cs"
        if ignore == "project-temp"
        else "MCPForUnity/Editor/CustomIgnored.cs"
    )
    if ignore == "project-temp":
        (repo / ".gitignore").write_text("**/Assets/Temp/\n", encoding="utf-8")
    else:
        (repo / ".git/info/exclude").write_text("CustomIgnored.cs\n", encoding="utf-8")
    source = repo / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("ignored local compilation input\n", encoding="utf-8")
    git(repo, "check-ignore", relative)
    index = (repo / ".git/index").read_bytes()
    result = push(repo, git(repo, "rev-parse", "HEAD"))
    assert result.returncode != 0
    assert "ignored" in result.stderr.lower()
    assert not (repo / ".checked-shas").exists()
    assert source.read_text() == "ignored local compilation input\n"
    assert (repo / ".git/index").read_bytes() == index


def test_checker_mutation_cannot_certify_original_commit(repo):
    (repo / ".mutate").touch()
    result = push(repo, git(repo, "rev-parse", "HEAD"))
    assert result.returncode != 0
    assert "dirty" in result.stderr.lower()


def test_irrelevant_commit_and_deletion_skip_checker(repo):
    before = git(repo, "rev-parse", "HEAD")
    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
    git(repo, "add", "notes.txt")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "notes")
    assert push(repo, git(repo, "rev-parse", "HEAD"), ZERO, base=before).returncode == 0
    assert not (repo / ".checked-shas").exists()


@pytest.mark.parametrize("location", ["default", "relative", "absolute", "linked"])
def test_install_resolves_effective_path_and_uninstalls_only_owned_hook(repo, tmp_path, location):
    root = repo
    if location == "linked":
        root = tmp_path / "linked tree"
        git(repo, "worktree", "add", "--detach", str(root))
    if location in {"relative", "absolute"}:
        path = "custom hooks" if location == "relative" else str(tmp_path / "absolute hooks")
        git(root, "config", "core.hooksPath", path)
    effective = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    result = shell(root, "tools/install-hooks.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    hook = effective / "pre-push"
    assert hook.read_bytes() == (root / "tools/hooks/pre-push").read_bytes()
    assert shell(root, "tools/install-hooks.sh").returncode == 0
    hook.write_text("foreign hook\n", encoding="utf-8")
    assert shell(root, "tools/install-hooks.sh").returncode == 0
    assert shell(root, "tools/install-hooks.sh", "--uninstall").returncode == 0
    assert hook.read_text() == "foreign hook\n"
    assert shell(root, "tools/install-hooks.sh", "--force").returncode == 0
    assert shell(root, "tools/install-hooks.sh", "--uninstall").returncode == 0
    assert not hook.exists()
