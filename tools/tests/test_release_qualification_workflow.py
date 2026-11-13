"""Exact receiving commits require executed native evidence before qualification."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.test_check_source_pin_drift import commit, put, set_pin
from tests.test_ci_trigger_policy import selected

ROOT = Path(__file__).resolve().parents[2]
pytest_plugins = ("tests.test_check_source_pin_drift",)


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "outcome", ["failure", "cancelled", "skipped", "missing", "wrong_sha", "missing_sha"]
)
def test_qualification_rejects_absent_or_invalid_upm_evidence(monkeypatch, tmp_path, outcome):
    # Given successful curated checks and invalid untouched installation evidence.
    gate = workflow("release-qualification.yml")["jobs"]["qualification"]
    results = {name: {"result": "success"} for name in gate["needs"]}
    results["upm"] = {"result": outcome, "outputs": {"checked_sha": "a" * 40}}
    if outcome == "missing":
        del results["upm"]
    if outcome == "wrong_sha":
        results["upm"] = {"result": "success", "outputs": {"checked_sha": "b" * 40}}
    if outcome == "missing_sha":
        results["upm"] = {"result": "success", "outputs": {}}
    monkeypatch.setenv("JOB_RESULTS", json.dumps(results))
    monkeypatch.setenv("QUALIFICATION_SHA", "a" * 40)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    program = next(step["run"] for step in gate["steps"] if step.get("id") == "require")
    # When qualification evaluates the actual workflow predicate.
    with pytest.raises(SystemExit):
        exec(compile(program, "qualification-gate", "exec"), {})
    # Then no release qualification summary is emitted.
    assert not summary.exists()


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
        "upm": "unity-upm-install.yml",
    }.items():
        job = jobs[name]
        assert job["uses"] == f"./.github/workflows/{file}"
        assert job["with"]["ref"] == "${{ github.sha }}"
        assert "identity" in job["needs"]
        assert "vars.UNITY_RUN_LICENSED_TESTS == 'true'" in job["if"]
        assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert jobs["bridge"]["with"]["pair_mode"] == "selected"
    assert jobs["bridge"]["with"]["require_native"] is True
    assert jobs["upm"]["with"]["require_upm_install"] is True
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
    for name in ("bridge", "reload", "upm"):
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
        "upm",
    }
    assert "always()" in gate["if"]
    assert "vars.UNITY_RUN_LICENSED_TESTS == 'true'" in gate["if"]
    assert "github.event_name == 'workflow_dispatch'" in gate["if"]
    assert "not release qualification" in str(jobs["license_free"])


def test_manual_qualification_requires_current_server_history(history):
    repo, _, base = history
    steps = workflow("release-qualification.yml")["jobs"]["identity"]["steps"]
    prepare = next(step for step in steps if "prepare_source_pin_history.py" in step.get("run", ""))
    strict = next(step for step in steps if "check_source_pin_drift.py" in step.get("run", ""))
    assert steps.index(prepare) < steps.index(strict)
    for step in (prepare, strict):
        assert step["if"] == "github.event_name == 'workflow_dispatch'"
        assert step["env"]["CHECK_REVISION"] == "${{ github.sha }}"
        assert not step.get("continue-on-error")

    put(repo, "Server/src/main.py", "# changed server input\n")
    candidate = commit(repo)
    args = shlex.split(strict["run"])
    assert args[:2] == ["python3", "tools/check_source_pin_drift.py"]
    args = [sys.executable, str(ROOT / args[1]), *args[2:]]
    args = [candidate if arg == "$CHECK_REVISION" else arg for arg in args]
    args.extend(["--repo", str(repo)])
    advisory = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/check_source_pin_drift.py"),
            "--repo",
            str(repo),
            "--revision",
            candidate,
            "--baseline",
            base,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert advisory.returncode == 0 and json.loads(advisory.stdout)["state"] == "stale"
    failed = subprocess.run(args, capture_output=True, text=True, check=False)
    assert failed.returncode == 1 and json.loads(failed.stdout)["state"] == "stale"
    set_pin(repo, candidate)
    refreshed = commit(repo)
    args[args.index(candidate)] = refreshed
    passed = subprocess.run(args, capture_output=True, text=True, check=False)
    assert passed.returncode == 0 and json.loads(passed.stdout)["state"] == "current"


def test_fork_selection_matches_qualification_trigger():
    from tools import ci_scope

    fork = workflow("fork-beta-tools.yml")
    qualification = workflow("release-qualification.yml")
    fork_trigger = fork.get("on", fork.get(True))["push"]
    qualification_trigger = qualification.get("on", qualification.get(True))["push"]
    assert fork_trigger["branches"] == qualification_trigger["branches"] == ["beta"]
    assert set(qualification_trigger["paths"]) == {
        *(prefix + "**" for prefix in ci_scope.QUALIFICATION_PREFIXES),
        *ci_scope.QUALIFICATION_FILES,
    }
    call = fork["jobs"]["unity_tests"]
    assert call["with"]["ref"] == "${{ github.sha }}"
    assert (
        call["with"]["qualification_owns_player"]
        == "${{ vars.UNITY_RUN_LICENSED_TESTS == 'true' && needs.changes.outputs.qualification_selected == 'true' }}"
    )
    assert call["with"]["qualification_owns_upm"] == call["with"]["qualification_owns_player"]


@pytest.mark.parametrize(
    "delegate,event,ref,repository,licensed,backend,profile,expected",
    [
        (True, "push", "refs/heads/beta", "fork/repo", "true", "mono", "standard", False),
        (False, "push", "refs/heads/beta", "fork/repo", "true", "mono", "standard", True),
        (True, "push", "refs/heads/beta", "fork/repo", "false", "mono", "standard", True),
        (True, "push", "refs/heads/main", "fork/repo", "true", "mono", "standard", True),
        (True, "push", "refs/heads/beta", "CoplayDev/unity-mcp", "true", "mono", "standard", True),
        (True, "pull_request", "refs/heads/beta", "fork/repo", "true", "mono", "standard", True),
        (
            True,
            "workflow_dispatch",
            "refs/heads/beta",
            "fork/repo",
            "true",
            "mono",
            "standard",
            True,
        ),
        (True, "push", "refs/heads/beta", "fork/repo", "true", "il2cpp", "standard", True),
        (True, "push", "refs/heads/beta", "fork/repo", "true", "mono", "long", True),
    ],
)
def test_player_route_only_delegates_licensed_fork_beta_push(
    delegate, event, ref, repository, licensed, backend, profile, expected
):
    unity = workflow("unity-tests.yml")
    trigger = unity.get("on", unity.get(True))
    assert trigger["workflow_call"]["inputs"]["qualification_owns_player"]["default"] is False
    expression = unity["jobs"]["playerAssurance"]["if"]
    for name, value in {
        "inputs.qualification_owns_player": delegate,
        "github.event_name": event,
        "github.ref": ref,
        "github.repository": repository,
        "vars.UNITY_RUN_LICENSED_TESTS": licensed,
        "inputs.player_backend": backend,
        "inputs.player_profile": profile,
        "inputs.ref": "a" * 40,
        "github.sha": "a" * 40,
    }.items():
        expression = expression.replace(name, repr(value))
    expression = expression.replace("&&", " and ").replace("!", " not ").replace(" not =", " !=")
    assert eval(expression, {"__builtins__": {}}, {}) is expected
    player = unity["jobs"]["playerAssurance"]
    assert (
        "github.event_name == 'pull_request' && 'quick' || 'standard'"
        in player["with"]["player_profile"]
    )
    assert player["with"]["require_player_e2e"] == "${{ inputs.require_player_e2e || false }}"


def test_player_delegation_requires_the_qualification_commit():
    expression = workflow("unity-tests.yml")["jobs"]["playerAssurance"]["if"]
    assert "inputs.ref == github.sha" in expression
    assert (
        workflow("fork-beta-tools.yml")["jobs"]["unity_tests"]["with"]["ref"] == "${{ github.sha }}"
    )


@pytest.mark.parametrize(
    "job", ["identity", "bridge", "reload", "windows_vfx", "player", "distribution", "upm"]
)
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", "missing", "", "success"])
def test_qualification_gate_requires_every_actual_success(monkeypatch, tmp_path, job, outcome):
    gate = workflow("release-qualification.yml")["jobs"]["qualification"]
    program = next(step["run"] for step in gate["steps"] if step.get("id") == "require")
    results = {name: {"result": "success"} for name in gate["needs"]}
    results["upm"]["outputs"] = {"checked_sha": "a" * 40}
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
