"""Real Git ranges and pinned Ruff establish CI selection and check-only behavior."""

from fnmatch import fnmatchcase
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from tools import check_changed_formatting as gate
from tools import format_scripts


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("entrypoint", ["script", "module"])
def test_cli_entrypoints_import_runner_and_reject_unverifiable_range(tmp_path, entrypoint):
    arguments = (
        [str(ROOT / "tools/check_changed_formatting.py")]
        if entrypoint == "script"
        else ["-m", "tools.check_changed_formatting"]
    )
    result = subprocess.run(
        [sys.executable, *arguments, "--base", "0" * 40, "--head", "a" * 40],
        cwd=tmp_path if entrypoint == "script" else ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 2
    assert "Formatting selection failed: Formatting requires explicit nonzero" in result.stderr
    assert "Traceback" not in result.stderr


def git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True, timeout=15
    ).stdout.strip()


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return path


def commit(root):
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Formatting test")
    git(tmp_path, "config", "user.email", "formatting@example.invalid")
    git(tmp_path, "config", "core.autocrlf", "false")
    for path, content in {
        "tools/unchanged.py": "value = 1\n",
        "tools/changed.py": "value = 1\n",
        "tools/removed.py": "value = 1\n",
        "tools/old name.py": "value = 1\n",
        "MCPForUnity/Editor/Tool.cs": "class Tool { }\n",
        "tools/fixture.ps1": "Write-Output 'manual'\n",
        "tools/vendor/external.py": "untouched   =   1\n",
        "tools/generated/output.cs": "class Output{}\n",
        "ruff.toml": (ROOT / "ruff.toml").read_text(encoding="utf-8"),
        ".editorconfig": (ROOT / ".editorconfig").read_text(encoding="utf-8"),
        ".config/dotnet-tools.json": (ROOT / ".config/dotnet-tools.json").read_text(
            encoding="utf-8"
        ),
    }.items():
        write(tmp_path, path, content)
    return tmp_path, commit(tmp_path)


def test_complete_range_includes_added_renamed_destinations_but_not_deleted_or_manual(repository):
    root, base = repository
    write(root, "tools/changed.py", "value = 2\n")
    commit(root)  # A beta push can contain multiple commits.
    write(root, "tools/added with spaces.py", "added = 1\n")
    (root / "tools/old name.py").rename(root / "tools/new name.py")
    (root / "tools/removed.py").unlink()
    write(root, "tools/fixture.ps1", "Write-Output 'manual change'\n")
    write(root, "tools/vendor/external.py", "external  = 2\n")
    head = commit(root)
    assert [path.relative_to(root).as_posix() for path in gate.select_files(root, base, head)] == [
        "tools/added with spaces.py",
        "tools/changed.py",
        "tools/new name.py",
    ]


def test_pr_range_excludes_base_only_edits_without_changing_push_selection(repository):
    root, ancestor = repository
    write(root, "tools/unchanged.py", "base_only = 2\n")
    base = commit(root)
    git(root, "checkout", "--detach", ancestor)
    write(root, "tools/changed.py", "branch_change = 2\n")
    head = commit(root)

    assert gate.select_files(root, base, head, merge_base=True) == (root / "tools/changed.py",)
    assert set(gate.select_files(root, base, head)) == {
        root / "tools/changed.py",
        root / "tools/unchanged.py",
    }


def test_pr_base_only_formatter_change_does_not_expand_branch_selection(repository):
    root, ancestor = repository
    settings = root / ".editorconfig"
    write(root, ".editorconfig", settings.read_text(encoding="utf-8") + "\n# Base-only edit\n")
    base = commit(root)
    git(root, "checkout", "--detach", ancestor)
    write(root, "tools/changed.py", "branch_change = 2\n")
    head = commit(root)

    assert gate.select_files(root, base, head, merge_base=True) == (root / "tools/changed.py",)
    assert root / "MCPForUnity/Editor/Tool.cs" in gate.select_files(root, base, head)


def test_pr_range_rejects_disconnected_history(repository):
    root, base = repository
    tree = git(root, "rev-parse", f"{base}^{{tree}}")
    head = git(root, "commit-tree", tree, "-m", "disconnected fixture")
    git(root, "checkout", "--detach", head)
    with pytest.raises(subprocess.CalledProcessError):
        gate.select_files(root, base, head, merge_base=True)


def test_pr_range_rejects_multiple_best_common_ancestors(repository):
    root, ancestor = repository
    tree = git(root, "rev-parse", f"{ancestor}^{{tree}}")
    left = git(root, "commit-tree", tree, "-p", ancestor, "-m", "left fixture")
    right = git(root, "commit-tree", tree, "-p", ancestor, "-m", "right fixture")
    base = git(root, "commit-tree", tree, "-p", left, "-p", right, "-m", "left merge")
    head = git(root, "commit-tree", tree, "-p", right, "-p", left, "-m", "right merge")
    git(root, "checkout", "--detach", head)
    with pytest.raises(ValueError, match="unique merge base"):
        gate.select_files(root, base, head, merge_base=True)


def test_cli_pr_mode_uses_merge_base(repository, monkeypatch, capsys):
    root, ancestor = repository
    write(root, "tools/unchanged.py", "base_only = 2\n")
    base = commit(root)
    git(root, "checkout", "--detach", ancestor)
    write(root, "tools/changed.py", "branch_change = 2\n")
    head = commit(root)
    monkeypatch.setattr(gate, "ROOT", root)

    assert gate.main(["--base", base, "--head", head, "--merge-base", "--list"]) == 0
    assert capsys.readouterr().out.splitlines() == ["tools/changed.py"]


@pytest.mark.parametrize(
    "config,suffixes",
    [
        ("ruff.toml", {".py"}),
        (".editorconfig", {".cs"}),
        (".config/dotnet-tools.json", {".cs"}),
        ("tools/format_scripts.py", {".py", ".cs"}),
        ("tools/check_changed_formatting.py", {".py", ".cs"}),
    ],
)
def test_configuration_changes_check_all_affected_supported_files(repository, config, suffixes):
    root, base = repository
    path = root / config
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    write(root, config, content + "\n")
    head = commit(root)
    selected = gate.select_files(root, base, head)
    assert {path.suffix for path in selected} == suffixes
    if ".py" in suffixes:
        assert root / "tools/unchanged.py" in selected
    if ".cs" in suffixes:
        assert root / "MCPForUnity/Editor/Tool.cs" in selected
    assert not any("vendor" in path.parts or "generated" in path.parts for path in selected)


@pytest.mark.parametrize("base,head", [("", "a" * 40), ("0" * 40, "a" * 40), ("HEAD~1", "HEAD")])
def test_ambiguous_ranges_fail_closed(repository, base, head):
    with pytest.raises(ValueError, match="explicit nonzero"):
        gate.select_files(repository[0], base, head)


def test_wrong_head_and_missing_base_fail_closed(repository):
    root, base = repository
    write(root, "tools/changed.py", "value = 2\n")
    head = commit(root)
    with pytest.raises(ValueError, match="checkout"):
        gate.select_files(root, head, base)
    with pytest.raises(subprocess.CalledProcessError):
        gate.select_files(root, "f" * 40, head)


def test_real_pinned_ruff_rejects_changed_file_without_writing_or_checking_unchanged(
    repository, monkeypatch
):
    root, base = repository
    command = format_scripts.ruff_command(ROOT)
    changed = write(root, "tools/changed.py", "value=  2\n")
    head = commit(root)
    # This uncommitted malformed file is outside the explicit CI range.
    unchanged = write(root, "tools/unchanged.py", "untouched=  7\n")
    before = {path: path.read_bytes() for path in (changed, unchanged)}
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(format_scripts, "ROOT", root)
    monkeypatch.setattr(format_scripts, "ruff_command", lambda _: command)
    assert gate.main(["--base", base, "--head", head]) == 1
    assert {path: path.read_bytes() for path in before} == before
    changed.write_bytes(b"value = 2\n")
    assert gate.main(["--base", base, "--head", head]) == 0
    assert unchanged.read_bytes() == before[unchanged]


def test_csharp_uses_existing_check_runner_and_preserves_failure(repository, monkeypatch):
    root, base = repository
    write(root, "MCPForUnity/Editor/Tool.cs", "class Tool{int value=2;}\n")
    head = commit(root)
    monkeypatch.setattr(gate, "ROOT", root)
    calls = []
    monkeypatch.setattr(format_scripts, "main", lambda args: calls.append(args) or 1)
    assert gate.main(["--base", base, "--head", head]) == 1
    assert calls == [["--check", "MCPForUnity/Editor/Tool.cs"]]


def test_config_only_change_detects_existing_affected_language_drift(repository, monkeypatch):
    root, _ = repository
    command = format_scripts.ruff_command(ROOT)
    drifted = write(root, "tools/unchanged.py", "untouched=  7\n")
    base = commit(root)
    config = root / "ruff.toml"
    write(root, "ruff.toml", config.read_text(encoding="utf-8") + "\n# Changed settings input\n")
    head = commit(root)
    before = drifted.read_bytes()
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(format_scripts, "ROOT", root)
    monkeypatch.setattr(format_scripts, "ruff_command", lambda _: command)
    assert gate.main(["--base", base, "--head", head]) == 1
    assert drifted.read_bytes() == before


def test_selection_failure_returns_setup_error_without_checking(repository, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", repository[0])
    monkeypatch.setattr(format_scripts, "main", lambda _: pytest.fail("unexpected check"))
    assert gate.main(["--base", "0" * 40, "--head", repository[1]]) == 2


def test_empty_or_manual_selection_never_falls_back_to_local_changes(repository, monkeypatch):
    root, base = repository
    write(root, "tools/fixture.ps1", "Write-Output 'new manual content'\n")
    head = commit(root)
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(format_scripts, "main", lambda _: pytest.fail("unexpected fallback"))
    assert gate.main(["--base", base, "--head", head]) == 0


def test_workflow_selects_configs_and_passes_explicit_pr_or_beta_range():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/changed-formatting.yml").read_text(encoding="utf-8")
    )
    triggers = workflow.get("on", workflow.get(True))
    for event in ("pull_request", "push"):
        patterns = triggers[event]["paths"]
        for path in [
            "tools/file.py",
            "mcp_source.py",
            "MCPForUnity/Editor/Tool.cs",
            "Root.cs",
            "Root.csx",
            *gate.PYTHON_CONFIG,
            *gate.CSHARP_CONFIG,
        ]:
            assert any(fnmatchcase(path, pattern) for pattern in patterns), (event, path)
    job = workflow["jobs"]["formatting"]
    checkout = job["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    step = next(step for step in job["steps"] if "--base" in step.get("run", ""))
    assert step["env"] == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha || github.event.before }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha || github.sha }}",
        "USE_MERGE_BASE": "${{ github.event_name == 'pull_request' }}",
    }
    assert '--base "$BASE_SHA" --head "$HEAD_SHA"' in step["run"]
    assert 'if [[ "$USE_MERGE_BASE" == "true" ]]' in step["run"]
    assert "args+=(--merge-base)" in step["run"]
    assert "--write" not in step["run"]
