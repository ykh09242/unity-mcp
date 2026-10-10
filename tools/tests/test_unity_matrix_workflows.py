"""All Unity CI channels share validated versions, images and package profiles."""

from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_SUITES = json.loads(
    (ROOT / "tools/unity-optional-tests.json").read_text(encoding="utf-8")
)["suites"]


def workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name,job_name",
    [
        ("compile-check.yml", "compile"),
        ("unity-tests.yml", "testAllModes"),
    ],
)
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
    assert (
        'python3 tools/unity_ci.py prepare "$UNITY_VERSION" --purpose tests' in preparation["run"]
    )
    runners = [
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("game-ci/unity-test-runner@")
    ]
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
    assert (
        'python3 tools/unity_compile_cache.py prepare "$UNITY_VERSION" --output "$SDK_PATH"'
        in preparation["run"]
    )
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
    diagnostic = next(
        step for step in steps if step.get("name") == "Diagnose restored compiler cache"
    )
    assert diagnostic["if"] == (
        "failure() && steps.sdk.outcome == 'failure' && steps.sdk_cache.outputs.cache-hit == 'true'"
    )
    assert diagnostic["env"] == {
        "UNITY_VERSION": "${{ matrix.unity.version }}",
        "SDK_PATH": "${{ steps.sdk_identity.outputs.cache_path }}",
    }
    assert (
        'python3 tools/unity_compile_cache_diagnostics.py "$UNITY_VERSION" --cache "$SDK_PATH"'
        in diagnostic["run"]
    )
    assert steps.index(diagnostic) > next(
        i for i, step in enumerate(steps) if step.get("id") == "sdk"
    )
    assert "continue-on-error" not in diagnostic


def test_compilation_uses_cached_data_with_small_pinned_runtime_image():
    steps = workflow("compile-check.yml")["jobs"]["compile"]["steps"]
    assert not any(step.get("id") == "editor" for step in steps)
    compile_step = next(step for step in steps if step.get("name") == "Compile")
    assert compile_step["env"]["UNITY_IMAGE"] == "${{ steps.sdk.outputs.runtime_image }}"
    assert compile_step["env"]["UNITY_DATA"] == "${{ steps.sdk.outputs.unity_data }}"
    assert "--entrypoint /bin/bash" in compile_step["run"]
    assert '-e UNITY_DATA="/repo/$UNITY_DATA"' in compile_step["run"]
    assert '"$UNITY_IMAGE" /repo/tools/compile-check.sh' in compile_step["run"]


@pytest.mark.parametrize(
    "path", ["tools/unity_compile_cache.py", "tools/unity_compile_cache_diagnostics.py"]
)
def test_sdk_extractor_changes_trigger_compilation(path):
    config = workflow("compile-check.yml")
    triggers = config.get("on", config.get(True))
    for event in ("push", "pull_request"):
        assert any(fnmatchcase(path, pattern) for pattern in triggers[event]["paths"])


@pytest.mark.parametrize("name", ["compile-check.yml", "unity-tests.yml"])
@pytest.mark.parametrize(
    "path",
    [
        "tools/unity-versions.json",
        "tools/unity_ci.py",
        "tools/unity-ci/Dockerfile",
        "tools/unity_ci_packages.py",
        "tools/unity-ci-packages.json",
        "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/UnityReflectTests.cs",
    ],
)
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
        capture_output=True,
        text=True,
        check=True,
    )
    matrix = json.loads(result.stdout)
    assert matrix == [
        {"version": row["id"], "channel": row["channel"]} for row in manifest["versions"]
    ]
    assert {row["channel"] for row in matrix} == {"lts", "supported", "beta", "alpha"}
    assert any(row["version"].startswith("2021.3.") for row in matrix)
    assert any(row["version"].startswith("2022.3.") for row in matrix)
    assert any(row["version"].startswith("6000.0.") for row in matrix)
    default = next(row for row in manifest["versions"] if row["id"] == manifest["defaultVersion"])
    assert default["channel"] == "lts"


@pytest.mark.parametrize(
    "name,job_name",
    [
        ("compile-check.yml", "compile"),
        ("unity-tests.yml", "testAllModes"),
    ],
)
def test_compilation_and_editor_tests_share_isolated_package_preparation(name, job_name):
    job = workflow(name)["jobs"][job_name]
    packages = next(step for step in job["steps"] if step.get("id") == "packages")
    assert packages["env"]["UNITY_VERSION"] == "${{ matrix.unity.version }}"
    assert "python3 tools/unity_ci_packages.py prepare" in packages["run"]
    assert '--output ".unity-ci/$UNITY_VERSION"' in packages["run"]
    if name == "unity-tests.yml":
        assert packages["env"]["UNITY_IMAGE"] == "${{ steps.editor.outputs.image }}"
        runners = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("game-ci/unity-test-runner@")
        ]
        assert all(
            step["with"]["projectPath"] == "${{ steps.packages.outputs.project_path }}"
            for step in runners
        )
    else:
        assert packages["env"]["UNITY_DATA"] == "${{ steps.sdk.outputs.unity_data }}"
        assert '--unity-data "$UNITY_DATA"' in packages["run"]
        assert "--image" not in packages["run"]
        compile_step = next(step for step in job["steps"] if step.get("name") == "Compile")
        assert compile_step["env"]["EXTRA_REFS"] == "${{ steps.packages.outputs.refs }}"
        assert (
            compile_step["env"]["TEST_FRAMEWORK_SOURCE"]
            == "${{ steps.packages.outputs.test_framework_source }}"
        )
        assert (
            compile_step["env"]["EDITOR_COROUTINES_SOURCE"]
            == "${{ steps.packages.outputs.editor_coroutines_source }}"
        )
        assert compile_step["env"]["TEST_PROJECT"] == "${{ steps.packages.outputs.project_path }}"
        assert '-e TEST_PROJECT="/repo/$TEST_PROJECT"' in compile_step["run"]
        assert (
            '-e EDITOR_COROUTINES_SOURCE="/repo/$EDITOR_COROUTINES_SOURCE"' in compile_step["run"]
        )
        artifact = next(
            step for step in job["steps"] if step.get("name") == "Upload package resolution"
        )
        assert artifact["with"]["path"] == "${{ steps.packages.outputs.resolution_report }}"
        assert artifact["with"]["if-no-files-found"] == "error"
        assert artifact["with"]["include-hidden-files"] is True


def bash_command():
    bash = shutil.which("bash")
    if bash is None and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(candidate) if candidate.is_file() else None
    if bash is None:
        pytest.skip("Bash is required to execute the workflow shell regression")
    return [bash, "--noprofile", "--norc", "-c"]


def named_step(job_name, name):
    return next(
        step
        for step in workflow("unity-tests.yml")["jobs"][job_name]["steps"]
        if step.get("name") == name
    )


def test_optional_preparation_uses_complete_editor_image_without_license_or_slim_cache():
    job = workflow("unity-tests.yml")["jobs"]["optionalPackageInputs"]
    assert job["needs"] == ["matrix"]
    assert "if" not in job
    assert job["strategy"]["fail-fast"] is False
    assert (
        job["strategy"]["matrix"]["unity"]
        == "${{ fromJson(needs.matrix.outputs.optional_versions) }}"
    )
    preparation = named_step("optionalPackageInputs", "Prepare optional integration packages")
    assert preparation["env"]["UNITY_IMAGE"] == "${{ steps.editor.outputs.image }}"
    assert '--image "$UNITY_IMAGE"' in preparation["run"]
    assert '--output ".unity-ci/optional-$UNITY_VERSION" --include-optional' in preparation["run"]
    assert "--unity-data" not in preparation["run"]
    for step in job["steps"]:
        assert "UNITY_LICENSE" not in str(step)
        assert "unity_compile_cache" not in str(step)
        assert not step.get("uses", "").startswith("game-ci/unity-test-runner@")
    editor = named_step("optionalPackageInputs", "Prepare Unity Editor image")
    assert 'prepare "$UNITY_VERSION" --purpose tests' in editor["run"]


def test_optional_project_transfer_requires_the_same_opt_in_as_license_detection():
    # No native consumer can run without the explicit policy opt-in.
    detect = named_step("license", "Detect Unity license secrets")
    for name in ("Archive prepared optional project", "Upload prepared optional project"):
        transfer = named_step("optionalPackageInputs", name)
        assert transfer.get("if") == detect["if"]
    preparation = named_step("optionalPackageInputs", "Prepare optional integration packages")
    assert "if" not in preparation


def test_optional_preparation_retains_only_resolution_metadata_without_native_execution():
    steps = workflow("unity-tests.yml")["jobs"]["optionalPackageInputs"]["steps"]
    reports = [step for step in steps if step.get("name") == "Upload optional package resolution"]
    assert len(reports) == 1, "License-free validation must retain its resolution evidence"
    report = reports[0]
    assert report["uses"].startswith("actions/upload-artifact@")
    assert report["if"] == "always() && steps.packages.outcome == 'success'"
    assert report["with"]["name"] == "optional-package-resolution-${{ matrix.unity.version }}"
    assert report["with"]["path"] == "${{ steps.packages.outputs.resolution_report }}"
    assert report["with"]["if-no-files-found"] == "error"
    assert report["with"]["include-hidden-files"] is True
    assert report["with"]["retention-days"] == 7
    assert not report.get("continue-on-error", False)


def test_optional_native_jobs_are_gated_isolated_and_use_suite_filter_and_required_results():
    jobs = workflow("unity-tests.yml")["jobs"]
    job = jobs["optionalIntegrations"]
    assert job["needs"] == ["matrix", "license", "optionalPackageInputs"]
    assert job["if"] == "needs.license.outputs.unity_ok == 'true'"
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["max-parallel"] == 1
    assert job["strategy"]["matrix"] == {
        "unity": "${{ fromJson(needs.matrix.outputs.optional_versions) }}",
        "suite": "${{ fromJson(needs.matrix.outputs.optional_suites) }}",
    }
    cache = next(
        step["with"] for step in job["steps"] if step.get("uses", "").startswith("actions/cache@")
    )
    assert cache["path"] == ".unity-ci/optional-${{ matrix.unity.version }}/project/Library"
    assert cache["key"].startswith("Library-optional-${{ matrix.unity.version }}-")
    assert "restore-keys" not in cache
    baseline_cache = next(
        step["with"]
        for step in jobs["testAllModes"]["steps"]
        if step.get("uses", "").startswith("actions/cache@")
    )
    assert cache["key"] != baseline_cache["key"]
    runner = named_step("optionalIntegrations", "Run optional integration tests")
    assert runner["uses"].startswith("game-ci/unity-test-runner@")
    assert runner["with"]["customImage"] == "${{ steps.editor.outputs.image }}"
    assert runner["with"]["projectPath"] == ".unity-ci/optional-${{ matrix.unity.version }}/project"
    assert runner["with"]["testMode"] == "editmode"
    assert runner["with"]["customParameters"] == "-testFilter ${{ matrix.suite.filter }}"
    assert runner["with"]["githubToken"] == ""
    gate = named_step("optionalIntegrations", "Require optional tests to execute and pass")
    assert "if" not in gate and not gate.get("continue-on-error", False)
    assert gate["env"]["TEST_RUN_OUTCOME"] == "${{ steps.tests.outcome }}"
    assert gate["env"]["REQUIRED_TESTS"] == "${{ toJson(matrix.suite.requiredTests) }}"
    assert job["steps"].index(gate) > job["steps"].index(runner)
    artifact = named_step("optionalIntegrations", "Upload optional integration evidence")
    assert artifact["if"] == "always()"
    assert "${{ matrix.suite.id }}" in artifact["with"]["name"]


def test_optional_matrix_filters_existing_manifest_versions_and_channels():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/unity_ci_packages.py"), "optional-matrix"],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = json.loads(result.stdout)
    manifest = json.loads((ROOT / "tools/unity-versions.json").read_text(encoding="utf-8"))
    profiles = json.loads((ROOT / "tools/unity-ci-packages.json").read_text(encoding="utf-8"))
    families = {
        family
        for profile in profiles["optionalProfiles"].values()
        for family in profile["unityFamilies"]
    }
    assert rows == [
        {"version": row["id"], "channel": row["channel"]}
        for row in manifest["versions"]
        if ".".join(row["id"].split(".")[:2]) in families
    ]
    assert rows


def test_optional_matrix_outputs_include_reviewed_suite_manifest():
    job = workflow("unity-tests.yml")["jobs"]["matrix"]
    selection = next(step for step in job["steps"] if step.get("id") == "set")
    assert "python3 tools/unity_ci_packages.py optional-matrix" in selection["run"]
    assert "tools/unity-optional-tests.json" in selection["run"]
    assert job["outputs"]["optional_versions"] == "${{ steps.set.outputs.optional_versions }}"
    assert job["outputs"]["optional_suites"] == "${{ steps.set.outputs.optional_suites }}"
    manifest = json.loads((ROOT / "tools/unity-optional-tests.json").read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert {
        "cinemachine-configuration",
        "cinemachine-creation",
        "probuilder",
        "input-system",
        "input-pause",
        "vfx-graph",
        "volumes",
    } <= {suite["id"] for suite in OPTIONAL_SUITES}
    assert len(OPTIONAL_SUITES) == len({suite["id"] for suite in OPTIONAL_SUITES})


def test_optional_source_archive_preserves_relative_package_links_and_executable_modes(tmp_path):
    version = "6000.0.75f1"
    producer = tmp_path / "producer"
    source = producer / ".unity-ci" / ("optional-" + version)
    for relative in ("package/Editor", "packages/com.example.optional", "project/Packages"):
        (source / relative).mkdir(parents=True)
    helper = source / "package/Editor/helper.sh"
    helper.write_text("#!/bin/sh\necho package-helper\n", encoding="utf-8")
    helper.chmod(0o755)
    (source / "packages/com.example.optional/package.json").write_text(
        '{"name":"com.example.optional"}', encoding="utf-8"
    )
    (source / "project/Packages/manifest.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "com.example.optional": "file:../../packages/com.example.optional",
                    "com.example.product": "file:../../package",
                }
            }
        ),
        encoding="utf-8",
    )
    (source / "resolved-packages.json").write_text("{}", encoding="utf-8")
    archive = named_step("optionalPackageInputs", "Archive prepared optional project")
    result = subprocess.run(
        [*bash_command(), archive["run"]],
        cwd=producer,
        capture_output=True,
        text=True,
        env={**os.environ, "UNITY_VERSION": version},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    artifact = producer / "optional-project.tar.gz"
    with tarfile.open(artifact) as tar:
        names = tar.getnames()
        assert {name.split("/")[0] for name in names} == {
            "package",
            "packages",
            "project",
            "resolved-packages.json",
        }
        assert all(not name.startswith("/") and ".." not in name.split("/") for name in names)
        assert all("Library" not in name.split("/") for name in names)
        assert tar.getmember("package/Editor/helper.sh").mode & 0o111
    consumer = tmp_path / "consumer"
    download = consumer / ".unity-ci/download"
    download.mkdir(parents=True)
    shutil.copyfile(artifact, download / artifact.name)
    restoration = named_step("optionalIntegrations", "Restore prepared optional project")
    result = subprocess.run(
        [*bash_command(), restoration["run"]],
        cwd=consumer,
        capture_output=True,
        text=True,
        env={**os.environ, "UNITY_VERSION": version},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    restored = consumer / ".unity-ci" / ("optional-" + version)
    manifest = json.loads((restored / "project/Packages/manifest.json").read_text(encoding="utf-8"))
    for package, reference in manifest["dependencies"].items():
        target = (restored / "project/Packages" / reference.removeprefix("file:")).resolve()
        assert target.is_relative_to(restored.resolve())
        assert target.is_dir(), package
    restored_helper = restored / "package/Editor/helper.sh"
    assert restored_helper.read_bytes() == helper.read_bytes()
    mode = subprocess.run(
        [*bash_command(), 'test -x "$HELPER_PATH"'],
        env={**os.environ, "HELPER_PATH": str(restored_helper)},
        capture_output=True,
        text=True,
    )
    assert mode.returncode == 0, mode.stdout + mode.stderr
    upload = named_step("optionalPackageInputs", "Upload prepared optional project")
    assert upload["with"]["path"] == artifact.name
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["compression-level"] == 0


@pytest.mark.parametrize("suite", OPTIONAL_SUITES, ids=lambda suite: suite["id"])
def test_optional_required_tests_exist_in_the_filtered_native_fixture(suite):
    assert suite["requiredTests"]
    assert len(suite["requiredTests"]) == len(set(suite["requiredTests"]))
    windows_only = suite.get("requiredTestsWindows", [])
    assert len(windows_only) == len(set(windows_only))
    assert not set(suite["requiredTests"]) & set(windows_only)
    namespace, fixture = suite["filter"].rsplit(".", 1)
    candidates = list((ROOT / "TestProjects/UnityMCPTests/Assets/Tests").rglob(fixture + ".cs"))
    assert len(candidates) == 1, suite["filter"]
    source = candidates[0].read_text(encoding="utf-8")
    assert re.search(r"\bnamespace\s+" + re.escape(namespace) + r"\b", source)
    assert re.search(r"\bclass\s+" + re.escape(fixture) + r"\b", source)
    for required in [*suite["requiredTests"], *windows_only]:
        assert required.startswith(suite["filter"] + "."), required
        method = required.removeprefix(suite["filter"] + ".")
        assert method.isidentifier(), required
        declaration = re.search(
            r"\bpublic\s+(?:async\s+)?[\w.<>\[\],]+\s+" + re.escape(method) + r"\s*\(", source
        )
        assert declaration, required
        attributes = source[: declaration.start()].rsplit("\n\n", 1)[-1]
        platform_win = bool(re.search(r"\[\s*Platform\s*\([^\]]*\bWin\b", attributes))
        assert platform_win == (required in windows_only), required


@pytest.mark.parametrize("suite", OPTIONAL_SUITES, ids=lambda suite: suite["id"])
@pytest.mark.parametrize("state", ["passed", "omitted", "skipped"])
def test_optional_workflow_shell_enforces_every_required_native_test(tmp_path, suite, state):
    required = suite["requiredTests"]
    root = ET.Element("test-run", result="Skipped:Ignored")
    ET.SubElement(root, "test-case", fullname="Unrelated.Fixture.Passing", result="Passed")
    for index, name in enumerate(required):
        if index == 0 and state == "omitted":
            continue
        result = "Skipped" if index == 0 and state == "skipped" else "Passed"
        ET.SubElement(root, "test-case", fullname=name, result=result)
    cases = list(root)
    passed = sum(case.get("result") == "Passed" for case in cases)
    root.attrib.update(
        total=str(len(cases)),
        passed=str(passed),
        failed="0",
        inconclusive="0",
        skipped=str(len(cases) - passed),
    )
    xml = tmp_path / "optional-results.xml"
    ET.ElementTree(root).write(xml, encoding="utf-8")
    gate = named_step("optionalIntegrations", "Require optional tests to execute and pass")
    script = gate["run"]
    assert script.startswith("python3 - <<'PY'\n")
    # Select the already-running interpreter; execute the workflow's actual shell and Python body.
    script = script.replace("python3 -", shlex.quote(sys.executable.replace("\\", "/")) + " -", 1)
    result = subprocess.run(
        [*bash_command(), script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "RESULTS_XML": str(xml),
            "TEST_RUN_OUTCOME": "success",
            "REQUIRED_TESTS": json.dumps(required),
            "PYTHONIOENCODING": "utf-8",
        },
    )
    assert result.returncode == (0 if state == "passed" else 1), result.stdout + result.stderr
    if state != "passed":
        assert required[0] in result.stdout


@pytest.mark.parametrize("present", range(16))
def test_license_shell_requires_credentials_and_either_activation_method(tmp_path, present):
    step = named_step("license", "Detect Unity license secrets")
    keys = ("UNITY_EMAIL", "UNITY_PASSWORD", "UNITY_LICENSE", "UNITY_SERIAL")
    values = {key: "dummy" if present & (1 << index) else "" for index, key in enumerate(keys)}
    output = tmp_path / "license-output.txt"
    result = subprocess.run(
        [*bash_command(), step["run"]],
        capture_output=True,
        text=True,
        env={**os.environ, **values, "GITHUB_OUTPUT": str(output)},
    )
    expected = bool(
        values["UNITY_EMAIL"]
        and values["UNITY_PASSWORD"]
        and (values["UNITY_LICENSE"] or values["UNITY_SERIAL"])
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text(encoding="utf-8").strip() == f"unity_ok={str(expected).lower()}"
