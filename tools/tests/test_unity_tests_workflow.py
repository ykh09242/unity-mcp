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
