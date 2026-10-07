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


@pytest.mark.parametrize(
    "name,job",
    [
        ("release.yml", "bump"),
        ("release.yml", "sync_beta"),
        ("release.yml", "publish_docker"),
        ("release.yml", "publish_pypi"),
        ("release.yml", "publish_mcpb"),
        ("sync-releases.yml", "sync"),
        ("stats.yml", "stats"),
    ],
)
def test_legacy_publication_and_adoption_jobs_exclude_forks(name: str, job: str) -> None:
    # Given: inherited automation owns upstream release identities and metrics.
    definition = workflow(name)["jobs"][job]
    # When: its GitHub job-level authorization is evaluated.
    condition = definition.get("if")
    # Then: a fork cannot publish, rewrite release history or claim upstream adoption.
    assert condition == UPSTREAM_ONLY


def test_forks_keep_python_and_unity_validation() -> None:
    # Given: one fork beta entry point owns both kinds of validation.
    unity = workflow("fork-beta-tools.yml")["jobs"]["unity_tests"]
    python = workflow("fork-beta-tools.yml")["jobs"]["python_tests"]
    # When: each entry point's reusable workflow and repository condition are read.
    definitions = (unity, python)
    # Then: forks retain both kinds of validation without passing new secrets.
    assert unity["if"] == "github.repository != 'CoplayDev/unity-mcp'"
    assert python["if"] == "github.repository != 'CoplayDev/unity-mcp'"
    assert all(job["uses"].startswith("./.github/workflows/") for job in definitions)
    assert "secrets" not in python


def test_beta_python_release_gate_excludes_duplicate_fork_runs() -> None:
    # Given: a mixed server/tool push selects both beta workflow entry points.
    release = workflow("beta-release.yml")["jobs"]["python_tests"]
    fork = workflow("fork-beta-tools.yml")["jobs"]["python_tests"]
    # When: the repository dispatch conditions are evaluated by GitHub.
    conditions = (release["if"], fork["if"])
    # Then: exactly one caller owns Python validation for each repository kind.
    assert conditions == (
        f"{UPSTREAM_ONLY} && github.actor != 'github-actions[bot]'",
        "github.repository != 'CoplayDev/unity-mcp'",
    )
    assert release["uses"] == fork["uses"] == "./.github/workflows/python-tests.yml"


@pytest.mark.parametrize(
    "path",
    [
        "Server/src/main.py",
        "Server/uv.lock",
        "MCPForUnity/Editor/Tools/ReadConsole.cs",
        "MCPForUnity/package.json",
    ],
)
def test_fork_beta_product_changes_select_python_validation(path: str) -> None:
    # Given: a product-only beta push, with no tooling change to trigger the old wrapper.
    # When: the fork Python entry point's real branch and path filters are applied.
    selected = push_selects("fork-beta-tools.yml", "beta", path)
    # Then: moving the release caller's ownership cannot leave product pushes untested.
    assert selected
    assert not push_selects("fork-beta-tools.yml", "main", path)


def test_pages_setup_upload_and_deployment_require_an_owned_beta_branch() -> None:
    jobs = workflow("docs-deploy.yml")["jobs"]
    condition = (
        f"({UPSTREAM_ONLY} || github.repository == 'ykh09242/unity-mcp') && "
        "(github.event_name == 'push' || github.event_name == 'workflow_dispatch') && "
        "github.ref == 'refs/heads/beta'"
    )
    assert jobs["deploy"]["if"] == condition
    steps = {step["name"]: step for step in jobs["build"]["steps"]}
    for name in ("Setup Pages", "Upload Pages artifact"):
        assert steps[name]["if"] == condition
    assert "if" not in steps["Build"]


def test_pages_build_uses_the_configured_deployment_origin() -> None:
    # Given: the repository's Pages setting is resolved only for an allowed deployment.
    config = workflow("docs-deploy.yml")
    steps = {step["name"]: step for step in config["jobs"]["build"]["steps"]}
    # When: the production build reads its canonical origin.
    origin = steps["Build"]["env"].get("WEBSITE_URL")
    # Then: it uses Pages metadata, not localhost or an inherited upstream URL.
    assert steps["Setup Pages"]["id"] == "pages"
    assert origin == "${{ steps.pages.outputs.origin }}"
    assert "workflow_dispatch" in config.get("on", config.get(True))


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


@pytest.mark.parametrize(
    "path",
    [
        "MCPForUnity/Editor/Tools/ManageBuild.cs",
        "tools/compile-check.sh",
        ".github/workflows/compile-check.yml",
    ],
)
def test_beta_pushes_select_license_free_compilation(path: str) -> None:
    # Given: a beta push touching C# or the compiler check itself.
    # When: GitHub evaluates the compile workflow's branch and path filters.
    selected = push_selects("compile-check.yml", "beta", path)
    # Then: the license-free compile check runs for that push.
    assert selected
    assert workflow("compile-check.yml")["permissions"] == {"contents": "read"}


@pytest.mark.parametrize(
    "path",
    [
        "tools/tests/test_generate_docs_reference.py",
        "mcp_source.py",
        ".github/scripts/mark_skipped.py",
        ".github/workflows/python-tests.yml",
        ".github/workflows/fork-beta-tools.yml",
        ".github/workflows/docs-deploy.yml",
        ".github/actions/publish-docker/action.yml",
        ".github/actionlint.yaml",
    ],
)
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
    assert list(config["jobs"]) == ["python_tests", "unity_tests"]
    job = config["jobs"]["python_tests"]
    assert job["if"] == "github.repository != 'CoplayDev/unity-mcp'"
    assert job["uses"] == "./.github/workflows/python-tests.yml"
    assert "secrets" not in job


@pytest.mark.parametrize(
    "path",
    [
        "tools/unity-ci-packages.json",
        "tools/unity-optional-tests.json",
        "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/CameraConfigurationIntegrityTests.cs",
        "MCPForUnity/Editor/Tools/ManageCamera.cs",
        "Server/src/main.py",
    ],
)
def test_fork_beta_profile_and_test_changes_select_exactly_one_unity_caller(path: str) -> None:
    fork = workflow("fork-beta-tools.yml")["jobs"]["unity_tests"]
    upstream = workflow("beta-release.yml")["jobs"]["unity_tests"]
    assert fork["uses"] == upstream["uses"] == "./.github/workflows/unity-tests.yml"
    assert fork["if"] == "github.repository != 'CoplayDev/unity-mcp'"
    assert upstream["if"] == f"{UPSTREAM_ONLY} && github.actor != 'github-actions[bot]'"
    selected = []
    for name, job in (("fork-beta-tools.yml", fork), ("beta-release.yml", upstream)):
        if push_selects(name, "beta", path) and job["if"] == fork["if"]:
            selected.append(name)
    # The standalone push trigger must not duplicate the reusable fork caller.
    if push_selects("unity-tests.yml", "beta", path):
        selected.append("unity-tests.yml")
    assert selected == ["fork-beta-tools.yml"]


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/release.yml",
        ".github/actions/publish-pypi/action.yml",
        ".github/actionlint.yaml",
        ".github/scripts/mark_skipped.py",
        "mcp_source.py",
    ],
)
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


def test_server_bootstrap_covers_default_windows_paths_and_offline_cache() -> None:
    job = workflow("python-tests.yml")["jobs"]["server_bootstrap"]
    assert set(job["strategy"]["matrix"]["os"]) == {
        "windows-latest",
        "ubuntu-latest",
        "macos-latest",
    }
    assert job["timeout-minutes"] <= 10
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Checkout source metadata"]["with"]["persist-credentials"] is False
    assert (
        "tools/check_server_startup.py"
        in steps["Checkout source metadata"]["with"]["sparse-checkout"]
    )
    assert steps["Install uv"]["with"]["python-version"] == "3.11"
    probe = steps["Verify cold and offline server bootstrap"]
    assert probe["shell"] == "pwsh"
    assert probe["env"]["GIT_CONFIG_KEY_0"] == "core.longpaths"
    assert probe["env"]["GIT_CONFIG_VALUE_0"] == "false"
    assert "mcpServerSource" in probe["run"]
    assert "--offline" in probe["run"]
    assert probe["run"].count("mcp-for-unity --help") == 2
    assert probe["run"].count("python tools/check_server_startup.py") == 2
    assert probe["run"].count("$LASTEXITCODE -ne 0") == 4
    assert "--python 3.10" not in probe["run"]
    assert not probe.get("continue-on-error", False)


def test_python_lint_is_a_blocking_step_with_locked_dependencies() -> None:
    # Given: reusable Python validation gates both ordinary PRs and release callers.
    steps = workflow("python-tests.yml")["jobs"]["test"]["steps"]
    # When: the step invoking the real lint entry point is selected.
    lint = next(step for step in steps if "tools/lint_python.py" in step.get("run", ""))
    # Then: diagnostics cannot be ignored and CI uses the committed dependency lock.
    assert not lint.get("continue-on-error", False)
    assert "--locked --extra dev" in lint["run"]
    assert lint["if"] == "matrix.suite == 'tools' && matrix.python-version == '3.14'"


@pytest.mark.parametrize(
    "step_name",
    [
        "Run tests with coverage",
        "Run server tests",
        "Run local harness unit tests (hermetic, no Unity)",
    ],
)
def test_python_validation_treats_warnings_as_errors(step_name: str) -> None:
    steps = workflow("python-tests.yml")["jobs"]["test"]["steps"]
    command = next(step["run"] for step in steps if step["name"] == step_name)
    assert "-W error" in command


def test_forks_keep_coverage_artifacts_without_external_uploads() -> None:
    steps = {step["name"]: step for step in workflow("python-tests.yml")["jobs"]["test"]["steps"]}
    assert steps["Upload coverage reports"]["if"] == (
        "always() && matrix.suite == 'server' && matrix.python-version == '3.14' "
        f"&& {UPSTREAM_ONLY}"
    )
    artifact = steps["Upload test results"]
    assert artifact["if"] == "always()"
    assert artifact["uses"].startswith("actions/upload-artifact@")
    assert {"Server/test-results.xml", "Server/coverage.xml", "Server/htmlcov/"} <= set(
        artifact["with"]["path"].splitlines()
    )


def test_python_validation_runs_both_suites_on_every_supported_minor() -> None:
    # Given: each declared Python minor needs the complete server and tool suites.
    job = workflow("python-tests.yml")["jobs"]["test"]
    # When: the matrix and commands are inspected together.
    matrix = job["strategy"]["matrix"]
    steps = {step["name"]: step for step in job["steps"]}
    # Then: every environment is explicitly selected and uploads cannot collide.
    assert job["strategy"]["fail-fast"] is False
    assert matrix["python-version"] == ["3.11", "3.12", "3.13", "3.14"]
    assert matrix["suite"] == ["server", "tools"]
    assert "exclude" not in matrix
    assert "needs" not in job
    assert "${{ matrix.python-version }}" in job["name"]
    assert "${{ matrix.suite }}" in job["name"]
    assert 'uv python install "${{ matrix.python-version }}"' in steps["Set up Python"]["run"]
    for name in (
        "Install dependencies",
        "Run tests with coverage",
        "Run server tests",
        "Run local harness unit tests (hermetic, no Unity)",
    ):
        assert '--python "${{ matrix.python-version }}"' in steps[name]["run"]
    assert "${{ matrix.python-version }}" in steps["Upload test results"]["with"]["name"]
    assert "${{ matrix.suite }}" in steps["Upload test results"]["with"]["name"]
    assert "${{ matrix.python-version }}" in steps["Upload coverage reports"]["with"]["name"]
    candidate = steps["Verify unlocked candidate startup"]
    assert candidate["if"] == "matrix.suite == 'server'"
    assert '--python "${{ matrix.python-version }}" --from ./Server' in candidate["run"]
    assert "python tools/check_server_startup.py" in candidate["run"]
    assert "--locked" not in candidate["run"]
    assert not candidate.get("continue-on-error", False)


def test_python_suites_are_disjoint_and_keep_structured_results() -> None:
    # Given: parallel jobs must not duplicate tests or omit coverage on the selected version.
    steps = {step["name"]: step for step in workflow("python-tests.yml")["jobs"]["test"]["steps"]}
    # When: the three mutually exclusive test invocations are selected.
    covered = steps["Run tests with coverage"]
    plain = steps["Run server tests"]
    tools = steps["Run local harness unit tests (hermetic, no Unity)"]
    # Then: each matrix cell runs one full suite, and every outcome has a JUnit artifact.
    assert covered["if"] == "matrix.suite == 'server' && matrix.python-version == '3.14'"
    assert plain["if"] == "matrix.suite == 'server' && matrix.python-version != '3.14'"
    assert tools["if"] == "matrix.suite == 'tools'"
    for step in (covered, plain, tools):
        assert "--junitxml=test-results.xml" in step["run"]
        assert "--durations=20" in step["run"]
        assert not step.get("continue-on-error", False)
    assert "pytest tests/" in covered["run"]
    assert "pytest tests/" in plain["run"]
    assert "--cov --cov-report=xml --cov-report=html --cov-report=term" in covered["run"]
    assert "--cov" not in plain["run"]
    assert '"$GITHUB_WORKSPACE/tools/tests/"' in tools["run"]


def test_python_dependency_cache_does_not_mask_cold_bootstrap() -> None:
    # Given: unit jobs may reuse dependency wheels, but bootstrap proves fresh/offline startup.
    jobs = workflow("python-tests.yml")["jobs"]
    # When: the setup action cache policy is read for both job families.
    installs = {
        name: next(step["with"] for step in job["steps"] if step["name"] == "Install uv")
        for name, job in jobs.items()
    }
    # Then: only unit jobs cache pinned dependencies, separated by Python minor.
    assert installs["server_bootstrap"]["enable-cache"] is False
    cache = installs["test"]
    assert cache["enable-cache"] is True
    assert cache["cache-suffix"] == "python-${{ matrix.python-version }}"
    assert set(cache["cache-dependency-glob"].splitlines()) == {
        "Server/pyproject.toml",
        "Server/uv.lock",
    }
    assert cache["save-cache"] == "${{ matrix.suite == 'tools' }}"


@pytest.mark.parametrize(
    "path", sorted((ROOT / ".github" / "workflows").glob("*.yml")), ids=lambda path: path.name
)
def test_external_workflow_actions_use_immutable_commits(path: Path) -> None:
    # Given: workflow actions are validated as parsed YAML, not by text substitution.
    config = workflow(path.name)
    # When: every action invocation is examined, including reusable jobs.
    references = [job["uses"] for job in config["jobs"].values() if "uses" in job]
    references += [
        step["uses"]
        for job in config["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    ]
    # Then: external actions cannot silently follow mutable tags or release branches.
    for reference in references:
        if reference.startswith("./"):
            continue
        action, ref = reference.rsplit("@", 1)
        assert action and re.fullmatch(r"[0-9a-f]{40}", ref), reference
        text = path.read_text(encoding="utf-8")
        assert re.search(
            re.escape(reference) + r"\s+# (?:v\d+\.\d+\.\d+|RELEASE \(\d{4}-\d{2}-\d{2}\))", text
        ), reference


@pytest.mark.parametrize(
    "path",
    sorted((ROOT / ".github" / "actions").rglob("action.y*ml")),
    ids=lambda path: path.parent.name,
)
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


@pytest.mark.parametrize(
    "name,enabled",
    [
        ("beta-release.yml", True),
        ("release.yml", True),
        ("docs-generate.yml", False),
        ("e2e-bridge.yml", False),
        ("claude-nl-suite.yml", False),
    ],
)
def test_uv_action_upgrades_preserve_explicit_cache_policy(name: str, enabled: bool) -> None:
    # Given: action major upgrades can change cache defaults.
    steps = [step for job in workflow(name)["jobs"].values() for step in job.get("steps", [])]
    # When: the uv installation's cache input is inspected.
    installs = [step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@")]
    # Then: existing opt-ins remain opt-ins and previously uncached jobs stay uncached.
    assert installs
    assert all(step["with"]["enable-cache"] is enabled for step in installs)


@pytest.mark.parametrize(
    "name,job", [("claude-nl-suite.yml", "nl-suite"), ("e2e-bridge.yml", "e2e-bridge")]
)
def test_latest_manual_runner_preserves_unity_compatibility_fixture(name: str, job: str) -> None:
    # Given: the manual Unity harnesses use a compatibility image independent of the host.
    config = workflow(name)
    # When: the host runner is upgraded to the current generally available Ubuntu release.
    assert config["jobs"][job]["runs-on"] == "ubuntu-26.04"
    # Then: the minimum Unity fixture is preserved, including disk-backed cache mounts.
    assert config["env"]["UNITY_IMAGE"] == "unityci/editor:ubuntu-2021.3.45f2-linux-il2cpp-3"
    commands = "\n".join(step.get("run", "") for step in config["jobs"][job]["steps"])
    assert "$RUNNER_TEMP/unity-config:/root/.config/unity3d" in commands
    assert "$RUNNER_TEMP/unity-cache:/root/.cache/unity3d" in commands


def test_actionlint_accepts_only_the_verified_new_runner_label() -> None:
    # Given: Ubuntu 26.04 is GA but newer than actionlint's bundled label catalog.
    config_path = ROOT / ".github" / "actionlint.yaml"
    assert config_path.is_file()
    # When: the local linter configuration is parsed.
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    # Then: only that known label is added, without disabling any lint rules.
    assert config == {"self-hosted-runner": {"labels": ["ubuntu-26.04"]}}
