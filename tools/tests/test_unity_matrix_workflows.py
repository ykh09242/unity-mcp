"""All Unity CI channels share validated versions, images and package profiles."""

from fnmatch import fnmatchcase
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,job_name", [
    ("compile-check.yml", "compile"),
    ("unity-tests.yml", "testAllModes"),
])
def test_every_unity_job_uses_the_full_validated_manifest(name, job_name):
    jobs = workflow(name)["jobs"]
    matrix = jobs["matrix"]
    command = next(step["run"] for step in matrix["steps"] if step.get("id") == "set")
    assert "python3 tools/unity_ci.py matrix" in command
    assert "defaultVersion" not in command
    assert "FULL_MATRIX_LABEL" not in command
    assert matrix["outputs"]["versions"] == "${{ steps.set.outputs.versions }}"
    job = jobs[job_name]
    assert "matrix" in job["needs"]
    assert job["strategy"]["matrix"]["unity"] == "${{ fromJson(needs.matrix.outputs.versions) }}"
    assert job["strategy"]["fail-fast"] is False
    assert job.get("continue-on-error", False) is False
    assert "${{ matrix.unity.version }}" in job["name"]
    assert "${{ matrix.unity.channel }}" in job["name"]


def test_licensed_tests_prepare_the_selected_immutable_editor_image():
    job = workflow("unity-tests.yml")["jobs"]["testAllModes"]
    preparation = next(step for step in job["steps"] if step.get("id") == "editor")
    assert preparation["env"]["UNITY_VERSION"] == "${{ matrix.unity.version }}"
    assert 'python3 tools/unity_ci.py prepare "$UNITY_VERSION" --purpose tests' in preparation["run"]
    runners = [step for step in job["steps"] if step.get("uses", "").startswith("game-ci/unity-test-runner@")]
    assert len(runners) == 2
    for runner in runners:
        assert runner["with"]["customImage"] == "${{ steps.editor.outputs.image }}"
        assert runner["with"]["unityVersion"] == "${{ matrix.unity.version }}"


def test_compilation_restores_exact_sdk_cache_and_validates_it_on_every_run():
    config = workflow("compile-check.yml")
    assert config["permissions"] == {"contents": "read"}
    job = config["jobs"]["compile"]
    assert "permissions" not in job
    steps = job["steps"]
    identity = next(step for step in steps if step.get("id") == "sdk_identity")
    assert identity["env"]["UNITY_VERSION"] == "${{ matrix.unity.version }}"
    assert 'python3 tools/unity_compile_cache.py identity "$UNITY_VERSION"' in identity["run"]
    restore = next(step for step in steps if step.get("id") == "sdk_cache")
    assert restore["uses"] == "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    assert restore["env"] == {"TAR_OPTIONS": "--same-permissions"}
    assert restore["with"] == {
        "path": "${{ steps.sdk_identity.outputs.cache_path }}",
        "key": "${{ steps.sdk_identity.outputs.cache_key }}",
    }
    preparation = next(step for step in steps if step.get("id") == "sdk")
    assert "if" not in preparation
    assert preparation["env"]["UNITY_VERSION"] == "${{ matrix.unity.version }}"
    assert preparation["env"]["SDK_PATH"] == "${{ steps.sdk_identity.outputs.cache_path }}"
    assert 'python3 tools/unity_compile_cache.py prepare "$UNITY_VERSION" --output "$SDK_PATH"' in preparation["run"]
    assert steps.index(identity) < steps.index(restore) < steps.index(preparation)


def test_compilation_saves_new_sdk_before_package_or_compiler_failures():
    steps = workflow("compile-check.yml")["jobs"]["compile"]["steps"]
    save = next(step for step in steps if step.get("uses", "").startswith("actions/cache/save@"))
    assert save["uses"] == "actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    assert save["if"] == (
        "github.event_name != 'pull_request' && steps.sdk_cache.outputs.cache-hit != 'true' "
        "&& steps.sdk.outputs.populated == 'true'"
    )
    assert save["with"] == {
        "path": "${{ steps.sdk_identity.outputs.cache_path }}",
        "key": "${{ steps.sdk_identity.outputs.cache_key }}",
    }
    prepare_index = next(i for i, step in enumerate(steps) if step.get("id") == "sdk")
    package_index = next(i for i, step in enumerate(steps) if step.get("id") == "packages")
    compile_index = next(i for i, step in enumerate(steps) if step.get("name") == "Compile")
    assert prepare_index < steps.index(save) < package_index < compile_index


def test_failed_restored_sdk_has_bounded_read_only_diagnostics():
    steps = workflow("compile-check.yml")["jobs"]["compile"]["steps"]
    diagnostic = next(step for step in steps if step.get("name") == "Diagnose restored compiler cache")
    assert diagnostic["if"] == (
        "failure() && steps.sdk.outcome == 'failure' && steps.sdk_cache.outputs.cache-hit == 'true'"
    )
    assert diagnostic["env"] == {
        "UNITY_VERSION": "${{ matrix.unity.version }}",
        "SDK_PATH": "${{ steps.sdk_identity.outputs.cache_path }}",
    }
    assert 'python3 tools/unity_compile_cache_diagnostics.py "$UNITY_VERSION" --cache "$SDK_PATH"' in diagnostic["run"]
    assert steps.index(diagnostic) > next(i for i, step in enumerate(steps) if step.get("id") == "sdk")
    assert "continue-on-error" not in diagnostic


def test_compilation_uses_cached_data_with_small_pinned_runtime_image():
    steps = workflow("compile-check.yml")["jobs"]["compile"]["steps"]
    assert not any(step.get("id") == "editor" for step in steps)
    compile_step = next(step for step in steps if step.get("name") == "Compile")
    assert compile_step["env"]["UNITY_IMAGE"] == "${{ steps.sdk.outputs.runtime_image }}"
    assert compile_step["env"]["UNITY_DATA"] == "${{ steps.sdk.outputs.unity_data }}"
    assert '--entrypoint /bin/bash' in compile_step["run"]
    assert '-e UNITY_DATA="/repo/$UNITY_DATA"' in compile_step["run"]
    assert '"$UNITY_IMAGE" /repo/tools/compile-check.sh' in compile_step["run"]


@pytest.mark.parametrize("path", ["tools/unity_compile_cache.py", "tools/unity_compile_cache_diagnostics.py"])
def test_sdk_extractor_changes_trigger_compilation(path):
    config = workflow("compile-check.yml")
    triggers = config.get("on", config.get(True))
    for event in ("push", "pull_request"):
        assert any(fnmatchcase(path, pattern) for pattern in triggers[event]["paths"])


@pytest.mark.parametrize("name", ["compile-check.yml", "unity-tests.yml"])
@pytest.mark.parametrize("path", [
    "tools/unity-versions.json",
    "tools/unity_ci.py",
    "tools/unity-ci/Dockerfile",
    "tools/unity_ci_packages.py",
    "tools/unity-ci-packages.json",
    "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/UnityReflectTests.cs",
])
def test_matrix_and_package_policy_changes_trigger_unity_validation(name, path):
    config = workflow(name)
    triggers = config.get("on", config.get(True))
    assert "workflow_dispatch" in triggers
    for event in ("push", "pull_request"):
        assert any(fnmatchcase(path, pattern) for pattern in triggers[event]["paths"])


def test_licensed_jobs_keep_license_gate_and_isolate_version_caches():
    job = workflow("unity-tests.yml")["jobs"]["testAllModes"]
    assert job["if"] == "needs.license.outputs.unity_ok == 'true'"
    assert job["strategy"]["max-parallel"] == 1
    cache = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/cache@"))
    assert "${{ matrix.unity.version }}" in cache["with"]["key"]
    for prefix in cache["with"].get("restore-keys", "").splitlines():
        assert "${{ matrix.unity.version }}" in prefix
    assert cache["with"]["path"] == "${{ steps.packages.outputs.project_path }}/Library"
    assert "tools/unity-versions.json" in cache["with"]["key"]


def test_real_matrix_covers_the_support_floor_and_all_release_channels():
    manifest = json.loads((ROOT / "tools" / "unity-versions.json").read_text(encoding="utf-8"))
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "unity_ci.py"), "matrix"],
        capture_output=True, text=True, check=True,
    )
    matrix = json.loads(result.stdout)
    assert matrix == [{"version": row["id"], "channel": row["channel"]} for row in manifest["versions"]]
    assert {row["channel"] for row in matrix} == {"lts", "supported", "beta", "alpha"}
    assert any(row["version"].startswith("2021.3.") for row in matrix)
    assert any(row["version"].startswith("2022.3.") for row in matrix)
    assert any(row["version"].startswith("6000.0.") for row in matrix)
    default = next(row for row in manifest["versions"] if row["id"] == manifest["defaultVersion"])
    assert default["channel"] == "lts"


@pytest.mark.parametrize("name,job_name", [
    ("compile-check.yml", "compile"),
    ("unity-tests.yml", "testAllModes"),
])
def test_compilation_and_editor_tests_share_isolated_package_preparation(name, job_name):
    job = workflow(name)["jobs"][job_name]
    packages = next(step for step in job["steps"] if step.get("id") == "packages")
    assert packages["env"]["UNITY_VERSION"] == "${{ matrix.unity.version }}"
    assert "python3 tools/unity_ci_packages.py prepare" in packages["run"]
    assert '--output ".unity-ci/$UNITY_VERSION"' in packages["run"]
    if name == "unity-tests.yml":
        assert packages["env"]["UNITY_IMAGE"] == "${{ steps.editor.outputs.image }}"
        runners = [step for step in job["steps"] if step.get("uses", "").startswith("game-ci/unity-test-runner@")]
        assert all(step["with"]["projectPath"] == "${{ steps.packages.outputs.project_path }}" for step in runners)
    else:
        assert packages["env"]["UNITY_DATA"] == "${{ steps.sdk.outputs.unity_data }}"
        assert '--unity-data "$UNITY_DATA"' in packages["run"]
        assert "--image" not in packages["run"]
        compile_step = next(step for step in job["steps"] if step.get("name") == "Compile")
        assert compile_step["env"]["EXTRA_REFS"] == "${{ steps.packages.outputs.refs }}"
        assert compile_step["env"]["TEST_FRAMEWORK_SOURCE"] == "${{ steps.packages.outputs.test_framework_source }}"
        assert compile_step["env"]["EDITOR_COROUTINES_SOURCE"] == "${{ steps.packages.outputs.editor_coroutines_source }}"
        assert compile_step["env"]["TEST_PROJECT"] == "${{ steps.packages.outputs.project_path }}"
        assert '-e TEST_PROJECT="/repo/$TEST_PROJECT"' in compile_step["run"]
        assert '-e EDITOR_COROUTINES_SOURCE="/repo/$EDITOR_COROUTINES_SOURCE"' in compile_step["run"]
        artifact = next(step for step in job["steps"] if step.get("name") == "Upload package resolution")
        assert artifact["with"]["path"] == "${{ steps.packages.outputs.resolution_report }}"
        assert artifact["with"]["if-no-files-found"] == "error"
        assert artifact["with"]["include-hidden-files"] is True
