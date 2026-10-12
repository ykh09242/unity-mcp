"""Unknown changes and incomplete validation must never produce a reduced green gate."""

import json
from pathlib import Path
import subprocess

import pytest

from tools import ci_scope


@pytest.mark.parametrize("path", sorted(ci_scope.TOOLS_ONLY))
def test_allowlisted_modified_files_select_tools(path):
    assert ci_scope.classify(f"M\0{path}\0") == "tools-only"


@pytest.mark.parametrize(
    "changes",
    [
        "",
        "M\0tools/ci_test_performance.py",
        "M\0tools/ci_test_performance.py\0extra\0",
        "A\0tools/ci_test_performance.py\0",
        "D\0tools/ci_test_performance.py\0",
        "R100\0old\0tools/ci_test_performance.py\0",
        "M\0Server/src/file.py\0",
        "M\0MCPForUnity/Editor/File.cs\0",
        "M\0CustomTools/RoslynRuntimeCompilation/File.cs\0",
        "M\0website/docs/reference/tools/tool.md\0",
        "M\0tools/tests/fixtures/file.py\0",
        "M\0tools/ci_scope.py\0",
        "M\0tools/lint_python.py\0",
        "M\0.github/workflows/python-tests.yml\0",
        "M\0unknown\0",
        "M\0tools/ci_test_performance.py\0M\0Server/src/file.py\0",
        "M\0tools/ci_test_performance.py\0M\0tools/ci_test_performance.py\0",
    ],
)
def test_unknown_mixed_or_structural_changes_require_full_validation(changes):
    assert ci_scope.classify(changes) == "full"


@pytest.mark.parametrize(
    "profile,classification,python,unity,expected",
    [
        ("full", "success", "success", "success", True),
        ("tools-only", "success", "success", "skipped", True),
        ("full", "success", "success", "skipped", False),
        ("tools-only", "success", "skipped", "skipped", False),
        ("tools-only", "failure", "success", "skipped", False),
        ("tools-only", "cancelled", "success", "skipped", False),
        ("full", "success", "failure", "success", False),
        ("full", "success", "cancelled", "success", False),
        ("full", "success", "success", "cancelled", False),
        ("unknown", "success", "success", "success", False),
        ("", "success", "success", "skipped", False),
    ],
)
def test_gate_requires_every_selected_workflow(profile, classification, python, unity, expected):
    assert ci_scope.gate(profile, classification, python, unity) is expected


@pytest.mark.parametrize("prior", ["success", "failure", "cancelled", "in_progress", "missing"])
def test_real_git_range_and_missing_base_fail_closed(tmp_path, monkeypatch, prior):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()

    git("init")
    git("config", "user.name", "CI test")
    git("config", "user.email", "ci@example.invalid")
    path = tmp_path / "tools" / "ci_test_performance.py"
    path.parent.mkdir()
    path.write_text("before\n", encoding="utf-8")
    git("add", "tools/ci_test_performance.py")
    git("-c", "commit.gpgsign=false", "commit", "-m", "initial")
    before = git("rev-parse", "HEAD")
    path.write_text("after\n", encoding="utf-8")
    git("add", "tools/ci_test_performance.py")
    git("-c", "commit.gpgsign=false", "commit", "-m", "change")
    after = git("rev-parse", "HEAD")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": before, "after": after}), encoding="utf-8")
    run = {
        "head_sha": before,
        "head_branch": "beta",
        "event": "push",
        "status": "in_progress" if prior == "in_progress" else "completed",
        "conclusion": prior,
    }
    real_run = subprocess.run

    def process(command, **kwargs):
        if command[0] != "gh":
            return real_run(command, **kwargs)
        assert f"head_sha={before}" in command[-1]
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"workflow_runs": [] if prior == "missing" else [run]}).encode()
        )

    monkeypatch.setattr(ci_scope.subprocess, "run", process)
    assert ci_scope.select(event, tmp_path, "owner/repo")[0] == (
        "tools-only" if prior == "success" else "full"
    )
    for base, head in [("0" * 40, after), ("1" * 40, after), (before, before), (after, after)]:
        event.write_text(json.dumps({"before": base, "after": head}), encoding="utf-8")
        assert ci_scope.select(event, tmp_path, "owner/repo")[0] == "full"


@pytest.mark.parametrize(
    "field,value",
    [("head_sha", "b" * 40), ("head_branch", "other"), ("event", "workflow_dispatch")],
)
def test_other_validation_provenance_cannot_authorize_reduced_checks(monkeypatch, field, value):
    run = {
        "head_sha": "a" * 40,
        "head_branch": "beta",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        field: value,
    }
    monkeypatch.setattr(
        ci_scope.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps({"workflow_runs": [run]}).encode()
        ),
    )
    assert not ci_scope.validated_base("owner/repo", "a" * 40)


def test_unavailable_event_selects_full_and_emits_output(tmp_path, monkeypatch):
    output = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert ci_scope.main(["select", "--event", str(tmp_path / "missing")]) == 0
    assert output.read_text(encoding="utf-8") == "profile=full\n"
    assert ci_scope.select(None, Path.cwd())[0] == "full"


def test_check_cli_failure_is_nonzero():
    assert (
        ci_scope.main(
            [
                "check",
                "--profile",
                "full",
                "--classification",
                "success",
                "--python",
                "success",
                "--unity",
                "skipped",
            ]
        )
        == 1
    )
