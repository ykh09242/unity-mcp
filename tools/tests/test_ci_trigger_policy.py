"""Work branches validate through PRs; beta retains integration validation routes."""

from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
VALIDATIONS = [
    ("python-tests.yml", "tools/lint_python.py"),
    ("unity-tests.yml", "MCPForUnity/Editor/Tools/ManageBuild.cs"),
    ("compile-check.yml", "MCPForUnity/Editor/Tools/ManageBuild.cs"),
    ("workflow-lint.yml", ".github/workflows/python-tests.yml"),
]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def selected(name, event, branch, path, activity=None):
    """Evaluate the positive branch/path filters used by these validation workflows."""
    config = workflow(name)
    triggers = config.get("on", config.get(True))
    if event not in triggers:
        return False
    filters = triggers[event] or {}
    if event == "pull_request" and activity not in filters.get(
        "types", ["opened", "synchronize", "reopened"]
    ):
        return False
    if "branches" in filters and not any(
        fnmatchcase(branch, pattern) for pattern in filters["branches"]
    ):
        return False
    if any(fnmatchcase(branch, pattern) for pattern in filters.get("branches-ignore", [])):
        return False
    return "paths" not in filters or any(fnmatchcase(path, pattern) for pattern in filters["paths"])


@pytest.mark.parametrize("name,path", VALIDATIONS)
def test_work_branch_push_does_not_duplicate_pr_validation(name, path):
    assert not selected(name, "push", "codex/server-improvement", path)


@pytest.mark.parametrize("name,path", VALIDATIONS)
@pytest.mark.parametrize("activity", ["opened", "synchronize", "reopened"])
@pytest.mark.parametrize("target", ["main", "beta"])
def test_pr_creation_and_updates_select_relevant_validation(name, path, activity, target):
    assert selected(name, "pull_request", target, path, activity)
    assert not selected(name, "pull_request", target, "unrelated/example.txt", activity)
    assert not selected(name, "pull_request", target, path, "closed")


@pytest.mark.parametrize("name", ["workflow-lint.yml"])
def test_existing_unrestricted_pr_target_scope_is_preserved(name):
    path = dict(VALIDATIONS)[name]
    assert selected(name, "pull_request", "release/maintenance", path, "synchronize")
    config = workflow(name)
    assert "branches" not in config.get("on", config.get(True))["pull_request"]


@pytest.mark.parametrize("name", ["compile-check.yml", "workflow-lint.yml"])
def test_direct_integration_validation_is_beta_only(name):
    path = dict(VALIDATIONS)[name]
    assert selected(name, "push", "beta", path)
    assert not selected(name, "push", "main", path)
    assert not selected(name, "push", "beta", "unrelated/example.txt")
    config = workflow(name)
    assert config.get("on", config.get(True))["push"]["branches"] == ["beta"]


@pytest.mark.parametrize("path", ["Server/src/main.py", "MCPForUnity/Editor/Tools/ManageBuild.cs"])
def test_beta_python_and_unity_use_preserved_reusable_callers(path):
    for name in ("python-tests.yml", "unity-tests.yml"):
        assert not selected(name, "push", "beta", path)
        config = workflow(name)
        assert "push" not in config.get("on", config.get(True))
    assert selected("fork-beta-tools.yml", "push", "beta", path)
    assert selected("beta-release.yml", "push", "beta", path)
    for caller in ("fork-beta-tools.yml", "beta-release.yml", "release.yml"):
        jobs = workflow(caller)["jobs"]
        assert jobs["python_tests"]["uses"] == "./.github/workflows/python-tests.yml"
        assert jobs["unity_tests"]["uses"] == "./.github/workflows/unity-tests.yml"


@pytest.mark.parametrize("name,path", VALIDATIONS)
def test_manual_validation_remains_available_on_reviewed_refs(name, path):
    assert selected(name, "workflow_dispatch", "reviewed-trusted-ref", path)


def test_reusable_python_and_unity_inputs_and_license_boundaries_remain_available():
    python = workflow("python-tests.yml")
    python_call = python.get("on", python.get(True))["workflow_call"]
    assert python_call["inputs"]["validation_profile"]["default"] == "full"
    assert python_call["inputs"]["ref"]["default"] == ""
    assert python_call["inputs"]["defer_performance_report"]["default"] is False
    unity = workflow("unity-tests.yml")
    unity_call = unity.get("on", unity.get(True))["workflow_call"]
    assert unity_call["inputs"]["ref"]["default"] == ""
    assert unity_call["inputs"]["player_profile"]["default"] == "standard"
    assert unity_call["inputs"]["require_native_e2e"]["default"] is False
    assert unity_call["inputs"]["require_player_e2e"]["default"] is False
    assert set(unity_call["secrets"]) == {
        "UNITY_LICENSE",
        "UNITY_EMAIL",
        "UNITY_PASSWORD",
        "UNITY_SERIAL",
    }
    assert unity["jobs"]["testAllModes"]["if"] == "needs.license.outputs.unity_ok == 'true'"
    for name in ("python-tests.yml", "unity-tests.yml"):
        config = workflow(name)
        assert "pull_request_target" not in config.get("on", config.get(True))
