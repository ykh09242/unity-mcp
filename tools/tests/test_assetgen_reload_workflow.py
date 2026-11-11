"""The manual reload job selects real probes with exact disposable ownership."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/assetgen-reload-tests.yml"


@pytest.fixture
def config():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_real_reload_requires_explicit_dispatch_or_reusable_opt_in(config):
    triggers = config.get("on", config.get(True))
    assert set(triggers) == {"workflow_dispatch", "workflow_call"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["run_assetgen_reload"]["type"] == "boolean"
    assert inputs["run_assetgen_reload"]["default"] is False
    assert triggers["workflow_call"]["inputs"]["run_assetgen_reload"]["default"] is False
    job = config["jobs"]["reload"]
    assert job["if"] == "inputs.run_assetgen_reload"
    assert job["strategy"]["max-parallel"] == 1
    steps = {step.get("id", step.get("name")): step for step in job["steps"]}
    assert (
        steps["Checkout exact dispatched commit"]["with"]["ref"]
        == "${{ inputs.ref || github.sha }}"
    )
    source = steps["source"]["run"]
    assert "checked == os.environ['EXPECTED_SHA'] == os.environ['GITHUB_SHA']" in source
    assert "load_manifest(Path('tools/unity-versions.json'))" in source
    prerequisites = steps["Require licensed execution prerequisites"]
    assert prerequisites["env"]["LICENSED_POLICY"] == "${{ vars.UNITY_RUN_LICENSED_TESTS }}"
    assert "exit 1" in prerequisites["run"]
    assert not any(step.get("uses", "").startswith("actions/cache") for step in job["steps"])


def test_each_probe_selects_an_existing_explicit_actual_reload_method(config):
    job = config["jobs"]["reload"]
    rows = job["strategy"]["matrix"]["probe"]
    assert {row["id"] for row in rows} == {"queued", "recorded"}
    sources = {
        "queued": "AssetGenQueuedReloadTests.cs",
        "recorded": "AssetGenJobReloadTests.cs",
    }
    for row in rows:
        source = (
            ROOT / "TestProjects/UnityMCPTests/Assets/Tests/EditMode/AssetGen" / sources[row["id"]]
        ).read_text(encoding="utf-8")
        namespace, fixture, method = row["method"].rsplit(".", 2)
        assert f"namespace {namespace}" in source
        assert f"class {fixture}" in source
        assert re.search(
            r"\[UnityTest, Explicit\([^\n]+\)\]\s+public IEnumerator " + method, source
        )
        assert f'Environment.GetEnvironmentVariable("{row["owner"]}")' in source
    runner = next(step for step in job["steps"] if step.get("id") == "tests")
    assert runner["with"]["customParameters"] == "-testFilter ${{ matrix.probe.method }}"
    assert runner["with"]["cliVersion"] == "v0.1.69"
    assert runner["with"]["customImage"] == "${{ steps.owned-image.outputs.image }}"
    assert runner["with"]["githubToken"] == ""


@pytest.mark.parametrize("checkout_matches", [True, False])
def test_workflow_source_selector_checks_sha_before_creating_evidence(
    tmp_path, monkeypatch, checkout_matches
):
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(step for step in config["jobs"]["reload"]["steps"] if step.get("id") == "source")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "unity-versions.json").write_bytes((ROOT / "tools/unity-versions.json").read_bytes())
    monkeypatch.setenv("PROBE_ID", "queued")
    monkeypatch.setenv(
        "PROBE_METHOD", config["jobs"]["reload"]["strategy"]["matrix"]["probe"][0]["method"]
    )
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("EXPECTED_SHA", "a" * 40)
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    checked = ("a" if checkout_matches else "b") * 40
    program = compile(step["run"], "assetgen-workflow-source-selector", "exec")
    with patch("subprocess.check_output", return_value=checked + "\n"):
        if checkout_matches:
            exec(program, {})
        else:
            with pytest.raises(AssertionError, match="exact event SHA"):
                exec(program, {})
    receipt = tmp_path / ".tmp/assetgen-reload-queued/source.json"
    assert receipt.is_file() is checkout_matches
    assert output.is_file() is checkout_matches


def test_ownership_image_binds_only_the_matching_disposable_container_project(config):
    steps = {step.get("id", step.get("name")): step for step in config["jobs"]["reload"]["steps"]}
    prepare = steps["packages"]["run"]
    assert "tools/unity_ci_packages.py prepare" in prepare
    assert '--image "$UNITY_IMAGE"' in prepare
    assert '--output ".unity-ci/assetgen-reload/$PROBE_ID"' in prepare
    program = steps["owned-image"]["run"]
    assert 'test "$PROJECT_PATH" = ".unity-ci/assetgen-reload/$PROBE_ID/project"' in program
    command = shlex.split(next(line for line in program.splitlines() if line.startswith("printf ")))
    template = command[1].replace("\\n", "\n")
    image = "unityci/editor:ubuntu-6000.3.25f1-linux-il2cpp-3@sha256:" + "a" * 64
    for row in config["jobs"]["reload"]["strategy"]["matrix"]["probe"]:
        project = f".unity-ci/assetgen-reload/{row['id']}/project"
        recipe = template % (image, row["owner"], project)
        assert recipe.splitlines() == [
            f"FROM {image}",
            f"ENV {row['owner']}=/github/workspace/{project}",
        ]
    assert "docker build --network=none" in program
    assert "docker push" not in program


@pytest.mark.parametrize(
    "probe_case",
    [(index, state) for index in (0, 1) for state in ("Passed", "Skipped", "Explicit", "missing")],
)
def test_actual_workflow_gate_rejects_missing_or_skipped_selected_case(
    config, tmp_path, probe_case
):
    probe_index, case_result = probe_case
    job = config["jobs"]["reload"]
    row = job["strategy"]["matrix"]["probe"][probe_index]
    gate = next(
        step
        for step in job["steps"]
        if step.get("name") == "Require the selected real reload case to pass"
    )
    assert gate["if"] == "always() && steps.native-output.outcome == 'success'"
    assert gate["env"]["REQUIRED_TEST"] == "${{ matrix.probe.method }}"
    cases = [("Unrelated.Fixture.Pass", "Passed")]
    if case_result != "missing":
        cases.append((row["method"], case_result))
    passed = sum(result == "Passed" for _, result in cases)
    root = ET.Element(
        "test-run",
        result="Skipped:Ignored",
        total=str(len(cases)),
        passed=str(passed),
        failed="0",
        inconclusive="0",
        skipped=str(len(cases) - passed),
    )
    for name, result in cases:
        ET.SubElement(root, "test-case", fullname=name, result=result)
    results = tmp_path / "results.xml"
    results.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    values = {
        "$RESULTS_XML": str(results),
        "$TEST_RUN_OUTCOME": "success",
        "$REQUIRED_TEST": row["method"],
    }
    command = [values.get(part, part) for part in shlex.split(gate["run"])]
    command[0] = sys.executable
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == (0 if case_result == "Passed" else 1), result.stdout + result.stderr


def test_reload_evidence_is_retained_after_failure_without_project_or_license_contents(config):
    job = config["jobs"]["reload"]
    upload = next(
        step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    paths = upload["with"]["path"].splitlines()
    assert paths == [
        ".tmp/assetgen-reload-${{ matrix.probe.id }}/source.json",
        ".unity-ci/assetgen-reload/${{ matrix.probe.id }}/resolved-packages.json",
        ".unity-ci/assetgen-reload/${{ matrix.probe.id }}/Dockerfile.reload",
        ".release-evidence/",
    ]
    assert upload["with"]["retention-days"] == 30


@pytest.mark.parametrize("probe_index", [0, 1])
def test_workflow_reservation_binds_the_real_job_and_runner_step(config, tmp_path, probe_index):
    job = config["jobs"]["reload"]
    probe = job["strategy"]["matrix"]["probe"][probe_index]
    reserve = next(step for step in job["steps"] if step.get("id") == "native-output")
    runner = next(step for step in job["steps"] if step.get("id") == "tests")
    command = [part.replace("$PROBE_ID", probe["id"]) for part in shlex.split(reserve["run"])]
    command[:2] = [sys.executable, str(ROOT / command[1])]
    output = tmp_path / "step-output"
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_WORKSPACE": str(ROOT),
            "GITHUB_REPOSITORY": "fixture/unity-mcp",
            "GITHUB_JOB": "reload",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout.removeprefix("UNITY_MCP_EXECUTION "))
    assert record["job_key"] == "reload"
    assert record["step_name"] == runner["name"].replace("${{ matrix.probe.id }}", probe["id"])
    assert record["emitter_step_name"] == reserve["name"]
    assert record["observer_step_name"] == next(
        step["name"]
        for step in job["steps"]
        if step.get("name") == "Observe AssetGen reload outputs"
    )
    paths = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    reservation = json.loads((tmp_path / paths["reservation"]).read_text(encoding="utf-8"))
    assert reservation == record
    assert paths["results"] == record["expected_outputs"]["results"]
    assert paths["log"] == record["expected_outputs"]["execution-log"]
