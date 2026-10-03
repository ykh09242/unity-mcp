"""Fork syncs run checks without invoking upstream publishing identities."""

from pathlib import Path
from fnmatch import fnmatchcase
import re

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
    ".github/workflows/docs-deploy.yml",
    ".github/actions/publish-docker/action.yml",
    ".github/actionlint.yaml",
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


@pytest.mark.parametrize("path", [".github/workflows/release.yml", ".github/actions/publish-pypi/action.yml", ".github/actionlint.yaml"])
def test_ci_policy_changes_select_python_push_and_pull_request_validation(path: str) -> None:
    assert push_selects("python-tests.yml", "feature-policy", path)
    config = workflow("python-tests.yml")
    triggers = config.get("on", config.get(True))
    assert any(fnmatchcase(path, pattern) for pattern in triggers["pull_request"]["paths"])


def test_reusable_python_validation_includes_hermetic_tool_tests() -> None:
    # Given: the Python workflow reused by the beta tooling check.
    config = workflow("python-tests.yml")
    triggers = config.get("on", config.get(True))
    assert "workflow_call" in triggers
    # When: its test job's commands are inspected.
    runs = "\n".join(step.get("run", "") for step in config["jobs"]["test"]["steps"])
    # Then: the real tools suite is invoked alongside the server tests.
    assert 'python -m pytest "$GITHUB_WORKSPACE/tools/tests/"' in runs


def test_forks_keep_coverage_artifacts_without_external_uploads() -> None:
    steps = {step["name"]: step for step in workflow("python-tests.yml")["jobs"]["test"]["steps"]}
    assert steps["Upload coverage reports"]["if"] == f"always() && {UPSTREAM_ONLY}"
    artifact = steps["Upload test results"]
    assert artifact["if"] == "always()"
    assert artifact["uses"].startswith("actions/upload-artifact@")
    assert {"Server/coverage.xml", "Server/htmlcov/"} <= set(artifact["with"]["path"].splitlines())


def test_python_validation_selects_minimum_and_current_interpreters() -> None:
    # Given: reusable validation must cover both support-floor and current Python.
    job = workflow("python-tests.yml")["jobs"]["test"]
    # When: the matrix and commands are inspected together.
    versions = job["strategy"]["matrix"]["python-version"]
    steps = {step["name"]: step for step in job["steps"]}
    # Then: every environment is explicitly selected and uploads cannot collide.
    assert job["strategy"]["fail-fast"] is False
    assert "3.10" in versions
    assert any(version.startswith("3.14.") for version in versions)
    assert "${{ matrix.python-version }}" in job["name"]
    assert 'uv python install "${{ matrix.python-version }}"' in steps["Set up Python"]["run"]
    for name in ("Install dependencies", "Run tests with coverage", "Run local harness unit tests (hermetic, no Unity)"):
        assert '--python "${{ matrix.python-version }}"' in steps[name]["run"]
    assert "${{ matrix.python-version }}" in steps["Upload test results"]["with"]["name"]
    assert "${{ matrix.python-version }}" in steps["Upload coverage reports"]["with"]["name"]


@pytest.mark.parametrize("path", sorted((ROOT / ".github" / "workflows").glob("*.yml")), ids=lambda path: path.name)
def test_external_workflow_actions_use_immutable_commits(path: Path) -> None:
    # Given: workflow actions are validated as parsed YAML, not by text substitution.
    config = workflow(path.name)
    # When: every action invocation is examined, including reusable jobs.
    references = [job["uses"] for job in config["jobs"].values() if "uses" in job]
    references += [step["uses"] for job in config["jobs"].values() for step in job.get("steps", []) if "uses" in step]
    # Then: external actions cannot silently follow mutable tags or release branches.
    for reference in references:
        if reference.startswith("./"):
            continue
        action, ref = reference.rsplit("@", 1)
        assert action and re.fullmatch(r"[0-9a-f]{40}", ref), reference
        text = path.read_text(encoding="utf-8")
        assert re.search(re.escape(reference) + r"\s+# (?:v\d+\.\d+\.\d+|RELEASE \(\d{4}-\d{2}-\d{2}\))", text), reference


@pytest.mark.parametrize("path", sorted((ROOT / ".github" / "actions").rglob("action.y*ml")), ids=lambda path: path.parent.name)
def test_local_action_dependencies_use_immutable_commits(path: Path) -> None:
    # Given: all local action definitions, including currently unused composites.
    text = path.read_text(encoding="utf-8")
    action = yaml.safe_load(text)
    # When: each action's external dependencies are examined.
    for step in action["runs"].get("steps", []):
        reference = step.get("uses", "")
        # Then: local references stay local and external actions are immutable and auditable.
        if reference and not reference.startswith("./"):
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), reference
            assert re.search(re.escape(reference) + r"\s+# v\d+\.\d+\.\d+", text), reference


@pytest.mark.parametrize("name,enabled", [
    ("beta-release.yml", True),
    ("release.yml", True),
    ("python-tests.yml", False),
    ("docs-generate.yml", False),
    ("e2e-bridge.yml", False),
    ("claude-nl-suite.yml", False),
])
def test_uv_action_upgrades_preserve_explicit_cache_policy(name: str, enabled: bool) -> None:
    # Given: action major upgrades can change cache defaults.
    steps = [step for job in workflow(name)["jobs"].values() for step in job.get("steps", [])]
    # When: the uv installation's cache input is inspected.
    installs = [step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@")]
    # Then: existing opt-ins remain opt-ins and previously uncached jobs stay uncached.
    assert installs
    assert all(step["with"]["enable-cache"] is enabled for step in installs)


@pytest.mark.parametrize("name,job", [("claude-nl-suite.yml", "nl-suite"), ("e2e-bridge.yml", "e2e-bridge")])
def test_latest_manual_runner_preserves_unity_compatibility_fixture(name: str, job: str) -> None:
    # Given: the manual Unity harnesses use a compatibility image independent of the host.
    config = workflow(name)
    # When: the host runner is upgraded to the current generally available Ubuntu release.
    assert config["jobs"][job]["runs-on"] == "ubuntu-26.04"
    # Then: the minimum Unity fixture is preserved, including disk-backed cache mounts.
    assert config["env"]["UNITY_IMAGE"] == "unityci/editor:ubuntu-2021.3.45f2-linux-il2cpp-3"
    commands = "\n".join(step.get("run", "") for step in config["jobs"][job]["steps"])
    assert '$RUNNER_TEMP/unity-config:/root/.config/unity3d' in commands
    assert '$RUNNER_TEMP/unity-cache:/root/.cache/unity3d' in commands


def test_actionlint_accepts_only_the_verified_new_runner_label() -> None:
    # Given: Ubuntu 26.04 is GA but newer than actionlint's bundled label catalog.
    config_path = ROOT / ".github" / "actionlint.yaml"
    assert config_path.is_file()
    # When: the local linter configuration is parsed.
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    # Then: only that known label is added, without disabling any lint rules.
    assert config == {"self-hosted-runner": {"labels": ["ubuntu-26.04"]}}
