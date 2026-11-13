"""Exact receiving commits require executed native evidence before qualification."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from tests.test_ci_trigger_policy import selected


ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "path",
    [
        "MCPForUnity/package.json",
        "manifest.json",
        "release-source-inputs.json",
        "MCPForUnity/Editor/Tools/AssetGen/AssetGenPaths.cs",
        "MCPForUnity/Runtime/Example.cs",
        "Server/src/transport/plugin_hub.py",
        "TestProjects/UnityMCPTests/Assets/Tests/EditMode/AssetGen/AssetGenJobReloadTests.cs",
        "tools/run_windows_vfx_tests.py",
        "tools/local_harness.py",
        "tools/tests/test_assetgen_reload_workflow.py",
        ".github/workflows/e2e-bridge.yml",
    ],
)
def test_receiving_beta_inputs_select_qualification_without_pr_secrets(path):
    assert selected("release-qualification.yml", "push", "beta", path)
    assert not selected("release-qualification.yml", "push", "codex/example", path)
    assert not selected("release-qualification.yml", "pull_request", "beta", path)
    assert not selected("release-qualification.yml", "push", "beta", "docs/unrelated.md")


def test_qualification_reuses_exact_sha_and_selected_pair_with_full_native_scope():
    config = workflow("release-qualification.yml")
    jobs = config["jobs"]
    assert config["permissions"] == {"contents": "read"}
    for name, file in {
        "bridge": "e2e-bridge.yml",
        "reload": "assetgen-reload-tests.yml",
        "windows_vfx": "unity-windows-vfx.yml",
        "player": "unity-player-tests.yml",
        "distribution": "distribution-canary.yml",
    }.items():
        job = jobs[name]
        assert job["uses"] == f"./.github/workflows/{file}"
        assert job["with"]["ref"] == "${{ github.sha }}"
        assert "identity" in job["needs"]
        assert "vars.UNITY_RUN_LICENSED_TESTS == 'true'" in job["if"]
        assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert jobs["bridge"]["with"]["pair_mode"] == "selected"
    assert jobs["bridge"]["with"]["require_native"] is True
    assert jobs["reload"]["with"]["run_assetgen_reload"] is True
    assert jobs["player"]["with"] == {
        "ref": "${{ github.sha }}",
        "backend": "mono",
        "player_profile": "standard",
        "require_player_e2e": True,
    }
    assert jobs["player"]["needs"] == ["identity", "reload", "windows_vfx"]
    assert jobs["player"]["secrets"] == {
        "UNITY_" + key: "${{ secrets.UNITY_" + key + " }}"
        for key in ("EMAIL", "PASSWORD", "SERIAL")
    }
    assert jobs["windows_vfx"]["secrets"] == {
        "WINDOWS_VFX_UNITY_" + key: "${{ secrets.WINDOWS_VFX_UNITY_" + key + " }}"
        for key in ("EMAIL", "PASSWORD", "SERIAL")
    }
    assert "secrets" not in jobs["distribution"]
    for name in ("bridge", "reload"):
        assert set(jobs[name]["secrets"]) == {
            "UNITY_LICENSE",
            "UNITY_EMAIL",
            "UNITY_PASSWORD",
            "UNITY_SERIAL",
        }
    gate = jobs["qualification"]
    assert set(gate["needs"]) == {
        "identity",
        "bridge",
        "reload",
        "windows_vfx",
        "player",
        "distribution",
    }
    assert "always()" in gate["if"]
    assert "vars.UNITY_RUN_LICENSED_TESTS == 'true'" in gate["if"]
    assert "github.event_name == 'workflow_dispatch'" in gate["if"]
    assert "not release qualification" in str(jobs["license_free"])


@pytest.mark.parametrize(
    "job", ["identity", "bridge", "reload", "windows_vfx", "player", "distribution"]
)
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", "missing", "", "success"])
def test_qualification_gate_requires_every_actual_success(monkeypatch, tmp_path, job, outcome):
    gate = workflow("release-qualification.yml")["jobs"]["qualification"]
    program = next(step["run"] for step in gate["steps"] if step.get("id") == "require")
    results = {name: {"result": "success"} for name in gate["needs"]}
    if outcome == "missing":
        del results[job]
    else:
        results[job]["result"] = outcome
    monkeypatch.setenv("JOB_RESULTS", json.dumps(results))
    summary = tmp_path / "summary.md"
    revision = "a" * 40
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("QUALIFICATION_SHA", revision)
    if outcome == "success":
        exec(compile(program, "qualification-gate", "exec"), {})
        text = summary.read_text(encoding="utf-8")
        assert revision in text
        assert "Windows standalone Player (Mono, standard)" in text
        assert "No publication was performed" in text
    else:
        with pytest.raises(SystemExit):
            exec(compile(program, "qualification-gate", "exec"), {})
        assert not summary.exists()


def test_bridge_required_gate_cannot_inherit_success_from_a_skipped_matrix(monkeypatch):
    jobs = workflow("e2e-bridge.yml")["jobs"]
    gate = jobs["required"]
    assert "always()" in gate["if"] and "inputs.require_native" in gate["if"]
    program = gate["steps"][0]["run"]
    monkeypatch.setenv(
        "JOB_RESULTS",
        json.dumps(
            {
                "license": {"result": "success"},
                "pairs": {"result": "success"},
                "e2e-bridge": {"result": "skipped"},
            }
        ),
    )
    with pytest.raises(SystemExit):
        exec(compile(program, "required-bridge", "exec"), {})


@pytest.mark.parametrize("require_native", [False, True])
@pytest.mark.parametrize("playmode_failed", [False, True])
def test_actual_bridge_command_makes_failed_playmode_block_qualification(
    require_native, playmode_failed
):
    import local_harness

    # Run the real workflow shell, replacing only its process-launch boundary.
    # No Editor, Docker process, server or license operation is started.
    bash = shutil.which("bash")
    if not bash and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(candidate) if candidate.is_file() else None
    if not bash:
        pytest.skip("Bash is required to exercise the hosted bridge command")
    step = next(
        step
        for step in workflow("e2e-bridge.yml")["jobs"]["e2e-bridge"]["steps"]
        if step.get("id") == "harness"
    )
    script = "python3() { printf '%s\\0' \"$@\"; }\n" + step["run"]
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script],
        env={
            **os.environ,
            "REQUIRE_NATIVE": str(require_native).lower(),
            "UNITY_PROJECT_ROOT": "qualification-project",
        },
        capture_output=True,
        check=True,
        timeout=15,
    )
    command = result.stdout.decode().split("\0")[:-1]
    assert command[0] == "tools/local_harness.py"
    args = local_harness.build_arg_parser().parse_args(command[1:])
    assert args.legs == "smoke,editmode,playmode"
    outcomes = [
        local_harness.LegOutcome("smoke", "pass", blocking=True),
        local_harness.LegOutcome("editmode", "pass", blocking=True),
        local_harness.LegOutcome(
            "playmode", "fail" if playmode_failed else "pass", blocking=args.strict_playmode
        ),
    ]
    assert local_harness.aggregate_exit(outcomes) == int(require_native and playmode_failed)
    assert args.strict_playmode is require_native
    assert step["env"]["REQUIRE_NATIVE"] == "${{ inputs.require_native }}"


@pytest.mark.parametrize(
    "file",
    [
        "e2e-bridge.yml",
        "assetgen-reload-tests.yml",
        "unity-windows-vfx.yml",
        "unity-player-tests.yml",
        "distribution-canary.yml",
    ],
)
def test_reusable_qualification_contracts_declare_and_pin_the_ref(file):
    config = workflow(file)
    triggers = config.get("on", config.get(True))
    assert triggers["workflow_call"]["inputs"]["ref"]["type"] == "string"
    for job in config["jobs"].values():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["ref"] == "${{ inputs.ref || github.sha }}"
                assert step["with"]["persist-credentials"] is False
    assert "pull_request_target" not in triggers
    if file == "unity-windows-vfx.yml":
        job = config["jobs"]["windowsVfx"]
        assert job["environment"] == "windows-vfx"
        assert job["if"] == "github.ref == 'refs/heads/beta'"
        verify = next(step for step in job["steps"] if step.get("id") == "verify")
        assert "$env:EXPECTED_SHA -cne $env:GITHUB_SHA" in verify["run"]


def test_qualified_player_keeps_existing_license_ownership_and_required_execution():
    jobs = workflow("unity-player-tests.yml")["jobs"]
    player = jobs["player"]
    vfx = workflow("unity-windows-vfx.yml")["jobs"]["windowsVfx"]
    assert (
        player["concurrency"]
        == vfx["concurrency"]
        == {
            "group": "unity-windows-player-license-${{ github.repository }}",
            "cancel-in-progress": False,
        }
    )
    required = jobs["requiredPlayerE2E"]
    assert required["needs"] == ["license", "player"]
    assert required["if"] == "always() && inputs.require_player_e2e"
    assert player["if"] == "needs.license.outputs.unity_ok == 'true'"
    prerequisites = jobs["license"]["steps"][-1]
    assert prerequisites["if"] == "inputs.require_player_e2e"
    assert all(not step.get("continue-on-error") for step in player["steps"])
    evidence = next(step for step in player["steps"] if step.get("id") == "evidence")
    assert evidence["if"] == "always()"
    assert "check_play_scenario_player_evidence.py" in evidence["run"]
    assert "if ($env:RUNNER_OUTCOME -ne 'success')" in evidence["run"]
