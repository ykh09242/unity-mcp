"""Execute the opt-in hook against isolated Git indexes and working trees."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[2]
SOURCE: Final = "Server/src/services/tools/example.py"
REFERENCE: Final = "website/docs/reference/tools/core/example.md"


@pytest.fixture
def hook_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # A local generator fixture isolates the hook's Git contract from registry dependencies.
    repo = tmp_path / "repository with spaces"
    repo.mkdir()
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    files = {
        SOURCE: "initial\n",
        REFERENCE: "generated:initial\n",
        "website/docs/reference/tools/core/handbook.md": "authored example\n",
        "website/docs/reference/resources/index.md": "resources\n",
        "website/docs/reference/cli-commands.md": "cli\n",
        "tools/cli_example_validation.py": "VALUE = 'initial'\n",
        "tools/cli_reference.py": "VALUE = 'initial'\n",
        "mcp_source.py": "VALUE = 'initial'\n",
        "tools/unrelated.py": "VALUE = 'initial'\n",
        "tools/hooks/pre-commit": (ROOT / "tools/hooks/pre-commit").read_text(encoding="utf-8"),
        "tools/generate_docs_reference.py": (
            "from pathlib import Path\n"
            "root = Path(__file__).resolve().parents[1]\n"
            f"source = root / {SOURCE!r}\n"
            f"output = root / {REFERENCE!r}\n"
            "(root / '.generator-ran').write_text('ran', encoding='utf-8')\n"
            "if source.exists():\n"
            "    output.write_text('generated:' + source.read_text(encoding='utf-8'), "
            "encoding='utf-8', newline='\\n')\n"
            "    (output.parent / 'new.md').write_text('new page\\n', encoding='utf-8', newline='\\n')\n"
            "else:\n"
            "    output.unlink()\n"
        ),
        "test-bin/python3": '#!/usr/bin/env bash\nexec "$HOOK_TEST_PYTHON" "$@"\n',
    }
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    (repo / "test-bin/python3").chmod(0o755)
    monkeypatch.setenv("HOOK_TEST_PYTHON", sys.executable)
    git(repo, "init", "-q")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "core.hooksPath", ".unused-hooks")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return repo


def git(repo: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments], cwd=repo, check=True, capture_output=True, timeout=20
    ).stdout


def run_hook(repo: Path) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    return subprocess.run(
        [bash, "-c", 'export PATH="$PWD/test-bin:$PATH"; exec bash tools/hooks/pre-commit'],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )


@pytest.mark.parametrize(
    "dirty_path",
    [
        SOURCE,
        "Server/src/services/resources/unstaged.py",
        REFERENCE,
        "website/docs/reference/tools/core/handbook.md",
        "website/docs/reference/new-authored.md",
        "tools/cli_example_validation.py",
        "tools/cli_reference.py",
        "tools/generate_docs_reference.py",
        "mcp_source.py",
    ],
)
def test_hook_preserves_index_and_worktree_when_inputs_have_unstaged_changes(
    hook_repo: Path, dirty_path: str
) -> None:
    # Given: staged source plus an unstaged/partially staged input or authored example.
    (hook_repo / SOURCE).write_text("staged\n", encoding="utf-8", newline="\n")
    git(hook_repo, "add", SOURCE)
    dirty = hook_repo / dirty_path
    dirty.parent.mkdir(parents=True, exist_ok=True)
    dirty.write_text("unstaged author content\n", encoding="utf-8")
    before_index = (hook_repo / ".git/index").read_bytes()
    before_files = {
        path.relative_to(hook_repo): path.read_bytes()
        for path in hook_repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(hook_repo).parts
    }

    # When: the real hook considers regenerating from the working tree.
    result = run_hook(hook_repo)

    # Then: rejection happens before generation, preserving both Git states byte for byte.
    assert result.returncode != 0, result.stdout + result.stderr
    assert (hook_repo / ".git/index").read_bytes() == before_index
    assert {
        path.relative_to(hook_repo): path.read_bytes()
        for path in hook_repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(hook_repo).parts
    } == before_files


@pytest.mark.parametrize("deleted", [False, True])
def test_hook_stages_generated_additions_updates_and_deletions_when_inputs_are_clean(
    hook_repo: Path, deleted: bool
) -> None:
    # Given: a fully staged tool edit or deletion, with unrelated author work outside references.
    source = hook_repo / SOURCE
    if deleted:
        source.unlink()
    else:
        source.write_text("staged\n", encoding="utf-8", newline="\n")
    git(hook_repo, "add", "-A", SOURCE)
    unrelated = hook_repo / "tools/unrelated.py"
    unrelated.write_text("keep unstaged\n", encoding="utf-8", newline="\n")

    # When: the real hook regenerates and stages its own changes.
    result = run_hook(hook_repo)

    # Then: generated changes join the commit, including removed and newly created pages.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (hook_repo / ".generator-ran").exists()
    assert git(hook_repo, "diff", "--", "website/docs/reference") == b""
    changed = git(hook_repo, "diff", "--cached", "--name-only").decode().splitlines()
    assert set(changed) == (
        {SOURCE, REFERENCE}
        if deleted
        else {SOURCE, REFERENCE, "website/docs/reference/tools/core/new.md"}
    )
    if not deleted:
        assert git(hook_repo, "show", f":{REFERENCE}") == b"generated:staged\n"
    assert unrelated.read_bytes() == b"keep unstaged\n"


def test_hook_leaves_author_work_alone_when_no_tool_change_is_staged(hook_repo: Path) -> None:
    # Given: reference work with no staged module change.
    reference = hook_repo / REFERENCE
    reference.write_text("authored change\n", encoding="utf-8", newline="\n")
    # When: the hook runs for an unrelated commit.
    result = run_hook(hook_repo)
    # Then: no generator or staging operation occurs.
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (hook_repo / ".generator-ran").exists()
    assert git(hook_repo, "diff", "--cached") == b""
    assert reference.read_bytes() == b"authored change\n"


def test_hook_stages_new_pages_when_existing_pages_are_unchanged(hook_repo: Path) -> None:
    # Given: a new staged module and a generator run that adds only a new page.
    module = "Server/src/services/tools/new.py"
    (hook_repo / module).write_text("new module\n", encoding="utf-8", newline="\n")
    git(hook_repo, "add", module)
    # When: generation has no tracked reference modification to trigger staging.
    result = run_hook(hook_repo)
    # Then: the new generated page is still included in the commit.
    assert result.returncode == 0, result.stdout + result.stderr
    assert set(git(hook_repo, "diff", "--cached", "--name-only").decode().splitlines()) == {
        module,
        "website/docs/reference/tools/core/new.md",
    }
