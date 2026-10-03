"""Fork syncs run checks without invoking upstream publishing identities."""

from pathlib import Path
from fnmatch import fnmatchcase

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_ONLY = "github.repository == 'CoplayDev/unity-mcp'"


def workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("job", ["update_unity_beta_version", "publish_pypi_prerelease"])
def test_beta_release_side_effects_are_upstream_only(job: str) -> None:
    condition = workflow("beta-release.yml")["jobs"][job]["if"]
    assert condition == f"{UPSTREAM_ONLY} && github.actor != 'github-actions[bot]'"


def test_forks_keep_python_and_unity_validation() -> None:
    jobs = workflow("beta-release.yml")["jobs"]
    for name in ("python_tests", "unity_tests"):
        assert jobs[name]["if"] == "github.actor != 'github-actions[bot]'"
        assert jobs[name]["uses"].startswith("./.github/workflows/")


def test_pages_setup_upload_and_deployment_are_upstream_only() -> None:
    jobs = workflow("docs-deploy.yml")["jobs"]
    condition = f"{UPSTREAM_ONLY} && github.event_name == 'push' && github.ref == 'refs/heads/beta'"
    assert jobs["deploy"]["if"] == condition
    steps = {step["name"]: step for step in jobs["build"]["steps"]}
    for name in ("Setup Pages", "Upload Pages artifact"):
        assert steps[name]["if"] == condition
    assert "if" not in steps["Build"]


def push_selects(workflow_name: str, branch: str, path: str) -> bool:
    """Evaluate the simple positive path and branch filters used by these workflows."""
    config = workflow(workflow_name)
    # PyYAML's YAML 1.1 loader treats GitHub's unquoted `on` as a boolean key.
    push = config.get("on", config.get(True))["push"]
    if "branches" in push and not any(fnmatchcase(branch, pattern) for pattern in push["branches"]):
        return False
    if any(fnmatchcase(branch, pattern) for pattern in push.get("branches-ignore", [])):
        return False
    return any(fnmatchcase(path, pattern) for pattern in push["paths"])


@pytest.mark.parametrize("path", [
    "MCPForUnity/Editor/Tools/ManageBuild.cs",
    "tools/compile-check.sh",
    ".github/workflows/compile-check.yml",
])
def test_beta_pushes_select_license_free_compilation(path: str) -> None:
    # Given: a beta push touching C# or the compiler check itself.
    # When: GitHub evaluates the compile workflow's branch and path filters.
    selected = push_selects("compile-check.yml", "beta", path)
    # Then: the license-free compile check runs for that push.
    assert selected
    assert workflow("compile-check.yml")["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("path", [
    "tools/tests/test_generate_docs_reference.py",
    ".github/workflows/python-tests.yml",
    ".github/workflows/fork-beta-tools.yml",
])
def test_fork_beta_tools_pushes_select_reusable_python_validation(path: str) -> None:
    # Given: a fork beta push with tooling or its validation configuration changed.
    wrapper = ROOT / ".github" / "workflows" / "fork-beta-tools.yml"
    assert wrapper.is_file(), "Fork beta tooling pushes need a validation trigger"
    # When: branch and path filters are evaluated.
    selected = push_selects(wrapper.name, "beta", path)
    # Then: the selected wrapper delegates to the existing Python test workflow without secrets.
    assert selected
    config = workflow(wrapper.name)
    assert config["permissions"] == {"contents": "read"}
    assert list(config["jobs"]) == ["python_tests"]
    job = config["jobs"]["python_tests"]
    assert job["if"] == "github.repository != 'CoplayDev/unity-mcp'"
    assert job["uses"] == "./.github/workflows/python-tests.yml"
    assert "secrets" not in job


def test_reusable_python_validation_includes_hermetic_tool_tests() -> None:
    # Given: the Python workflow reused by the beta tooling check.
    config = workflow("python-tests.yml")
    triggers = config.get("on", config.get(True))
    assert "workflow_call" in triggers
    # When: its test job's commands are inspected.
    runs = "\n".join(step.get("run", "") for step in config["jobs"]["test"]["steps"])
    # Then: the real tools suite is invoked alongside the server tests.
    assert 'python -m pytest "$GITHUB_WORKSPACE/tools/tests/"' in runs
