"""The Unity test workflow must not float on game-ci's v4 tag or its CLI's latest release."""

import json
from pathlib import Path
import re

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "unity-tests.yml"


def runner_steps():
    text = WORKFLOW.read_text(encoding="utf-8")
    starts = [match.start() for match in re.finditer(r"^      - ", text, re.M)] + [len(text)]
    steps = [text[begin:end] for begin, end in zip(starts, starts[1:])]
    return [step for step in steps if "game-ci/unity-test-runner@" in step]


def test_every_runner_step_pins_the_action_commit_and_cli_release():
    steps = runner_steps()
    assert len(steps) == 3  # Domain reload, base EditMode and optional integration runner.
    for step in steps:
        # Both failures flow into their XML gate, which checks the raw outcome.
        assert re.search(r"^        continue-on-error: true$", step, re.M), step
        ref = re.search(r"uses: game-ci/unity-test-runner@(\S+)", step).group(1)
        assert re.fullmatch(r"[0-9a-f]{40}", ref), ref
        assert re.search(r"^          cliVersion: v\d+\.\d+\.\d+$", step, re.M), step
        # The read-only job cannot create a check run; the local gate reads the XML instead.
        assert re.search(r'^          githubToken: ""$', step, re.M), step


def test_unity_test_project_uses_the_packages_test_framework():
    root = WORKFLOW.parents[2]
    package = json.loads((root / "MCPForUnity" / "package.json").read_text(encoding="utf-8"))
    project = json.loads(
        (root / "TestProjects" / "UnityMCPTests" / "Packages" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        project["dependencies"]["com.unity.test-framework"]
        == package["dependencies"]["com.unity.test-framework"]
    )


@pytest.mark.parametrize("filename", ["unity-tests.yml", "e2e-bridge.yml"])
def test_native_gate_requires_opt_in_before_reading_license_secrets(filename):
    config = yaml.safe_load(WORKFLOW.with_name(filename).read_text(encoding="utf-8"))
    gate = config["jobs"]["license"]
    # Existing credentials must not turn an opted-out workflow into an activation attempt.
    assert gate["outputs"]["unity_ok"] == "${{ steps.detect.outputs.unity_ok || 'false' }}"
    steps = gate["steps"]
    detect = next(step for step in steps if step.get("id") == "detect")
    assert detect["if"] == "vars.UNITY_RUN_LICENSED_TESTS == 'true'"
    for step in steps:
        if "secrets." in json.dumps(step):
            assert step["if"] == "vars.UNITY_RUN_LICENSED_TESTS == 'true'"
    report = next(step for step in steps if step.get("id") == "license-free")
    assert report["if"] == "vars.UNITY_RUN_LICENSED_TESTS != 'true'"
    assert "secrets." not in json.dumps(report)
    assert "SKIPPED" in report["run"]
    assert "GITHUB_STEP_SUMMARY" in report["run"]
    assert "::warning::" not in report["run"]
    native_jobs = (
        ["testAllModes", "optionalIntegrations"]
        if filename == "unity-tests.yml"
        else ["e2e-bridge"]
    )
    for name in native_jobs:
        assert "license" in config["jobs"][name]["needs"]
        assert config["jobs"][name]["if"] == "needs.license.outputs.unity_ok == 'true'"


def test_manual_live_suite_cannot_bypass_license_free_policy():
    config = yaml.safe_load(WORKFLOW.with_name("claude-nl-suite.yml").read_text(encoding="utf-8"))
    assert config["jobs"]["nl-suite"]["if"] == "vars.UNITY_RUN_LICENSED_TESTS == 'true'"
    report = config["jobs"]["license-free"]
    assert report["if"] == "vars.UNITY_RUN_LICENSED_TESTS != 'true'"
    assert "secrets." not in json.dumps(report)
    assert "SKIPPED" in json.dumps(report)


def test_optional_package_preparation_remains_independent_of_native_policy():
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    prepare = config["jobs"]["optionalPackageInputs"]
    assert prepare["needs"] == ["matrix"]
    assert "if" not in prepare
    assert "secrets." not in json.dumps(prepare)
    assert "game-ci/unity-test-runner" not in json.dumps(prepare)


def test_strict_native_mode_is_opt_in_and_checks_fresh_evidence_after_execution():
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = config.get("on", config.get(True))
    for trigger in ("workflow_dispatch", "workflow_call"):
        field = triggers[trigger]["inputs"]["require_native_e2e"]
        assert field["type"] == "boolean" and field["default"] is False
    steps = config["jobs"]["testAllModes"]["steps"]
    cache_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/cache@")
    )
    initialize = next(step for step in steps if step.get("id") == "native-session")
    execute = next(step for step in steps if step.get("id") == "tests")
    gate = next(
        step
        for step in steps
        if step.get("name") == "Require native scenario bodies and persisted evidence"
    )
    assert cache_index < steps.index(initialize) < steps.index(execute) < steps.index(gate)
    assert "--initialize" in initialize["run"]
    assert gate["if"] == "always() && inputs.require_native_e2e"
    assert gate["env"]["NATIVE_SESSION_ID"] == "${{ steps.native-session.outputs.session_id }}"
    assert "--session-id" in gate["run"] and "--runner-outcome" in gate["run"]
    assert not gate.get("continue-on-error", False)
    artifact = next(
        step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert artifact["if"] == "always()"
    assert "PlayScenarioIntegrationEvidence" in artifact["with"]["path"]
    assert "resolved-packages.json" in artifact["with"]["path"]
    assert artifact["with"]["include-hidden-files"] is True


@pytest.mark.parametrize(
    "policy,license_ok,expected",
    [("true", "true", 0), ("false", "true", 1), ("true", "false", 1), ("", "", 1)],
)
def test_required_mode_rejects_missing_prerequisites(policy, license_ok, expected):
    import os
    import shutil
    import subprocess

    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        step
        for step in config["jobs"]["license"]["steps"]
        if step.get("name") == "Require licensed native E2E prerequisites"
    )
    assert step["if"] == "inputs.require_native_e2e"
    shell = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    if not Path(shell).exists():
        pytest.skip("Bash is unavailable")
    result = subprocess.run(
        [shell, "-c", step["run"]],
        env={**os.environ, "LICENSED_POLICY": policy, "UNITY_OK": license_ok},
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == expected


@pytest.mark.parametrize(
    "license_result,unity_ok,native_result,expected",
    [
        ("success", "true", "success", 0),
        ("failure", "true", "success", 1),
        ("skipped", "", "skipped", 1),
        ("success", "true", "skipped", 1),
        ("success", "true", "failure", 1),
        ("success", "true", "cancelled", 1),
        ("success", "false", "success", 1),
    ],
)
def test_always_run_required_gate_fails_skipped_or_unsuccessful_dependencies(
    license_result, unity_ok, native_result, expected
):
    import os
    import shutil
    import subprocess

    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = config["jobs"]["requiredNativeE2E"]
    assert job["if"] == "always() && inputs.require_native_e2e"
    assert job["needs"] == ["license", "testAllModes"]
    shell = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    if not Path(shell).exists():
        pytest.skip("Bash is unavailable")
    result = subprocess.run(
        [shell, "-c", job["steps"][0]["run"]],
        env={
            **os.environ,
            "LICENSE_RESULT": license_result,
            "UNITY_OK": unity_ok,
            "NATIVE_RESULT": native_result,
        },
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == expected
