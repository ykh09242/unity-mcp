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
# Shared corpus read by Server/tests/test_play_scenario_shared_contract.py.
SHARED_REPORT_FIXTURE = (
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/PlayScenarios/"
    "Fixtures/shared-report-contract.json"
)


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


@pytest.mark.parametrize("target", ["main", "beta"])
@pytest.mark.parametrize(
    "change,path",
    [
        ("C# addition without metadata", "MCPForUnity/Editor/Tools/NewTool.cs"),
        ("metadata removal", "MCPForUnity/Runtime/Component.cs.meta"),
        ("asset GUID change", "MCPForUnity/Editor/Icons/Icon.png.meta"),
    ],
)
def test_package_integrity_changes_select_python_pr_gate(target, change, path):
    # Given: an added script, removed metadata or modified GUID in the shipped package.
    # When: the real PR filters are evaluated (path filters apply to all change kinds).
    selected_gate = selected("python-tests.yml", "pull_request", target, path, "synchronize")
    # Then: the tools suite containing the shipped metadata integrity check must run.
    assert selected_gate, change
    runs = "\n".join(
        step.get("run", "") for step in workflow("python-tests.yml")["jobs"]["test"]["steps"]
    )
    assert 'python -m pytest "$GITHUB_WORKSPACE/tools/tests/"' in runs


@pytest.mark.parametrize(
    "name,event,branch,activity",
    [
        ("python-tests.yml", "pull_request", "main", "synchronize"),
        ("python-tests.yml", "pull_request", "beta", "synchronize"),
        ("fork-beta-tools.yml", "push", "beta", None),
    ],
)
@pytest.mark.parametrize(
    "path", [".dockerignore", "docker-compose.yml", "docker-compose.remote.yml"]
)
def test_docker_context_changes_select_distribution_contracts(name, event, branch, activity, path):
    assert selected(name, event, branch, path, activity)
    assert not selected(name, event, branch, "unrelated/example.txt", activity)


@pytest.mark.parametrize("target", ["main", "beta"])
@pytest.mark.parametrize("activity", ["opened", "synchronize", "reopened"])
def test_shared_report_fixture_only_pr_selects_python_contracts(target: str, activity: str) -> None:
    assert (ROOT / SHARED_REPORT_FIXTURE).is_file()
    assert selected("python-tests.yml", "pull_request", target, SHARED_REPORT_FIXTURE, activity)
    # Other Unity assets and fixture metadata do not become Python contract inputs.
    for path in (
        SHARED_REPORT_FIXTURE + ".meta",
        SHARED_REPORT_FIXTURE.replace("shared-report-contract.json", "unity-only.json"),
        "TestProjects/UnityMCPTests/Assets/Example.prefab",
    ):
        assert not selected("python-tests.yml", "pull_request", target, path, activity)


def test_shared_report_fixture_retains_beta_python_contract_validation() -> None:
    assert selected("fork-beta-tools.yml", "push", "beta", SHARED_REPORT_FIXTURE)
    jobs = workflow("fork-beta-tools.yml")["jobs"]
    assert jobs["python_tests"]["uses"] == "./.github/workflows/python-tests.yml"


@pytest.mark.parametrize("target", ["main", "beta"])
@pytest.mark.parametrize("activity", ["opened", "synchronize", "reopened"])
@pytest.mark.parametrize(
    "path",
    [
        "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Example.cs.meta",
        "TestProjects/UnityMCPTests/Assets/Example.prefab.meta",
    ],
)
def test_test_project_asset_pr_runs_only_the_focused_metadata_contract(target, activity, path):
    assert selected("test-project-metadata.yml", "pull_request", target, path, activity)
    assert not selected("python-tests.yml", "pull_request", target, path, activity)
    job = workflow("test-project-metadata.yml")["jobs"]["metadata"]
    assert "strategy" not in job
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert (
        "test_package_script_metadata.py::test_supplied_test_project_metadata_has_valid_unique_guids"
        in runs
    )
    assert not selected("test-project-metadata.yml", "pull_request", target, path, "closed")
    assert not selected(
        "test-project-metadata.yml",
        "pull_request",
        target,
        "TestProjects/UnityMCPTests/Assets/Example.cs",
        activity,
    )


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/release-qualification.yml",
        ".github/workflows/unity-player-tests.yml",
        "tools/run_play_scenario_player_e2e.py",
        "tools/check_play_scenario_player_evidence.py",
        "tools/player_e2e_profiles.py",
        "tools/unity-player-e2e.json",
        "tools/unity_windows_ci.py",
        "tools/tests/test_release_qualification_workflow.py",
        "tools/tests/test_ci_trigger_policy.py",
    ],
)
def test_player_qualification_dependencies_select_receiving_and_offline_validation(
    path: str,
) -> None:
    assert selected("release-qualification.yml", "push", "beta", path)
    assert selected("fork-beta-tools.yml", "push", "beta", path)
    for target in ("main", "beta"):
        assert selected("python-tests.yml", "pull_request", target, path, "synchronize")
        assert not selected(
            "release-qualification.yml", "pull_request", target, path, "synchronize"
        )
    assert not selected("release-qualification.yml", "push", "codex/player-work", path)


@pytest.mark.parametrize("target", ["main", "beta"])
def test_reference_only_pr_executes_resource_uri_contract(target: str) -> None:
    path = "website/docs/reference/tools/get_tests.md"
    assert selected("docs-generate.yml", "pull_request", target, path, "synchronize")
    steps = workflow("docs-generate.yml")["jobs"]["check"]["steps"]
    contract = next(
        step for step in steps if "test_resource_uri_references.py" in step.get("run", "")
    )
    assert "--extra dev" in contract["run"]
    assert "-W error" in contract["run"]


def test_authored_reference_resource_error_is_rejected(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "uri_reference_contract", ROOT / "Server/tests/test_resource_uri_references.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "unity-mcp-skill").mkdir()
    (tmp_path / "unity-mcp-skill/SKILL.md").write_text("# Fixture\n", encoding="utf-8")
    directory = tmp_path / "website/docs/reference/tools"
    directory.mkdir(parents=True)
    (directory / "example.md").write_text("Read the get_tests resource.\n", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="get_tests"):
        module.test_agent_facing_markdown_references_resources_by_uri(
            {"get_tests": "mcpforunity://tests"}
        )
