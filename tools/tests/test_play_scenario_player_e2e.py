"""Regression coverage for real Player evidence admission and owned process bounds."""

import hashlib
import json
from pathlib import Path
import sys
import shutil
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_play_scenario_player_evidence as checker
from player_e2e_artifacts import contained, inventory, verify_bundle, write_json
from player_e2e_process import Invocation, execute
from player_e2e_reports import check_progress, check_report
from run_play_scenario_player_e2e import initialize


@pytest.fixture
def native(tmp_path):
    bundle = {
        "build_id": "b" * 32,
        "build_source_revision": "trusted-build",
        "payload_hash": "c" * 64,
        "definition_hash": "d" * 64,
        "definition": {"name": "fixture"},
    }
    request = {
        **{
            key: bundle[key]
            for key in ("build_id", "build_source_revision", "payload_hash", "definition_hash")
        },
        "schema_version": 2,
        "job_id": "a" * 32,
        "repeat_count": 1,
        "timeout_seconds": 30,
    }
    process = {
        "process_id": 123,
        "actual_exit_code": 1,
        "process_ended": True,
        "forced_termination": False,
        "started_unix_ms": 1000,
        "finished_unix_ms": 2000,
    }
    report = {
        "reproduction": {**bundle, "payload_verification": "launcher_admission"},
        "scenario": bundle["definition"],
        "job_id": request["job_id"],
        "repeat_count": 1,
        "timeout_seconds": 30,
        "execution_environment": "player",
        "player_schema_version": 2,
        "finalization_state": "completed",
        "runner_resources_released": True,
        "report_error": None,
        "status": "timed_out",
        "exit_code": 1,
        "started_unix_ms": 1100,
        "finished_unix_ms": 1900,
        "steps": [{"stage": "cleanup", "status": "passed"}],
    }
    write_json(tmp_path / "request.json", request)
    write_json(tmp_path / "run.json", report)
    return tmp_path, bundle, request, process, report


def test_valid_negative_native_outcome_is_truthful(native):
    directory, bundle, _, process, _ = native
    assert check_report(directory, bundle, process)["status"] == "timed_out"


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", "e" * 32),
        ("status", "succeeded"),
        ("finalization_state", "running"),
        ("report_error", "persist-failed"),
        ("runner_resources_released", False),
        ("finished_unix_ms", 900),
    ],
)
def test_wrong_identity_exit_or_unfinished_report_fails(native, field, value):
    directory, bundle, _, process, report = native
    report[field] = value
    (directory / "run.json").write_text(json.dumps(report))
    with pytest.raises(ValueError):
        check_report(directory, bundle, process)


def test_missing_final_report_cannot_be_success(native):
    directory, bundle, _, process, _ = native
    (directory / "run.json").unlink()
    with pytest.raises(ValueError):
        check_report(directory, bundle, process)


def test_progress_requires_update_not_initial_publication(native):
    _, _, request, process, _ = native
    initial = {
        **request,
        "schema_version": 1,
        "process_id": process["process_id"],
        "sequence": 1,
        "main_loop_sequence": 0,
        "heartbeat_unix_ms": 0,
        "elapsed_ms": 0,
    }
    with pytest.raises(ValueError, match="Initial snapshot"):
        check_progress([initial], request, 123)
    observed = {
        **initial,
        "sequence": 2,
        "main_loop_sequence": 1,
        "heartbeat_unix_ms": 1200,
        "elapsed_ms": 100,
    }
    check_progress([initial, observed], request, 123)
    for mutation in ({"sequence": 1}, {"elapsed_ms": -1}, {"job_id": "f" * 32}):
        with pytest.raises(ValueError):
            check_progress([initial, {**observed, **mutation}], request, 123)


def test_full_payload_hash_detects_changed_non_executable_file(tmp_path):
    (tmp_path / "MCPScenarioPlayer.exe").write_bytes(b"exe")
    (tmp_path / "UnityPlayer.dll").write_bytes(b"runtime")
    files = inventory(tmp_path)
    canonical = json.dumps(files, separators=(",", ":"))
    bundle = {
        "schema_version": 2,
        "build_id": "a" * 32,
        "build_source_revision": "trusted",
        "payload_inventory": files,
        "payload_inventory_json": canonical,
        "payload_hash": hashlib.sha256(canonical.encode()).hexdigest(),
    }
    write_json(tmp_path / "scenario-bundle.json", bundle)
    verify_bundle(tmp_path, "trusted")
    with pytest.raises(ValueError, match="trusted"):
        verify_bundle(tmp_path, "caller-label")
    (tmp_path / "UnityPlayer.dll").write_bytes(b"changed")
    with pytest.raises(ValueError, match="payload files"):
        verify_bundle(tmp_path, "trusted")


@pytest.mark.parametrize(
    "relative", ["../run.json", "a/../../run.json", "C:/run.json", "a\\run.json", "/run.json"]
)
def test_artifact_paths_reject_traversal(tmp_path, relative):
    with pytest.raises(ValueError):
        contained(tmp_path, relative)


def test_fresh_initializer_refuses_cached_root(tmp_path):
    output = tmp_path / "fresh"
    assert len(initialize(output)) == 32
    with pytest.raises(FileExistsError):
        initialize(output)


def test_actual_owned_timeout_reaps_process(tmp_path):
    result = execute(
        Invocation([sys.executable, "-c", "import time;time.sleep(10)"], tmp_path, timeout=0.2)
    )
    assert result["forced_termination"] and result["process_ended"]
    assert result["actual_exit_code"] != 0


def test_required_workflow_cannot_pass_skipped_execution():
    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load(
        (root / ".github/workflows/unity-player-tests.yml").read_text(encoding="utf-8-sig")
    )
    player = config["jobs"]["player"]
    assert player["runs-on"] == "windows-2022"
    assert player["if"] == "needs.license.outputs.unity_ok == 'true'"
    assert all(not step.get("continue-on-error") for step in player["steps"])
    final = config["jobs"]["requiredPlayerE2E"]
    assert final["needs"] == ["license", "player"]
    assert final["if"] == "always() && inputs.require_player_e2e"
    script = final["steps"][0]["run"]
    assert "\"$PLAYER_RESULT\" != 'success'" in script
    detect = config["jobs"]["license"]["steps"][0]
    assert detect["if"] == "vars.UNITY_RUN_LICENSED_TESTS == 'true'"
    assert '"$UNITY_SERIAL"' in detect["run"]


def test_missing_receipt_is_required_failure(tmp_path):
    write_json(
        tmp_path / ".session.json", {"session_id": "a" * 32, "build_source_revision": "trusted"}
    )
    with pytest.raises(ValueError):
        checker.check(tmp_path, "b" * 32, (tmp_path, tmp_path))


@pytest.mark.parametrize(
    "relative",
    ["NUL.txt", "com1.dll", "a/trailing.", "a/trailing ", "a/\\u0085name", "a/.env.fixture"],
)
def test_windows_ambiguous_payload_paths_are_rejected(tmp_path, relative):
    with pytest.raises(ValueError):
        contained(tmp_path, relative)


def test_nested_bundle_basename_is_part_of_payload(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "scenario-bundle.json").write_bytes(b"payload")
    assert inventory(tmp_path)[0]["path"] == "nested/scenario-bundle.json"


def test_discovery_directory_cap_precedes_unbounded_scan(tmp_path):
    for index in range(4096):
        (tmp_path / str(index)).mkdir()
    with pytest.raises(ValueError, match="directory count"):
        inventory(tmp_path)


def test_protected_exact_subtree_is_rejected_before_read(tmp_path):
    protected = tmp_path / "Assets/Resources/GameData"
    protected.mkdir(parents=True)
    with pytest.raises(ValueError, match="Protected"):
        inventory(tmp_path)
    # A plain synthetic directory with that basename is not the protected chain.
    ordinary = tmp_path / "ordinary"
    (ordinary / "GameData").mkdir(parents=True)
    (ordinary / "GameData/test.txt").write_bytes(b"fixture")
    assert inventory(ordinary)[0]["path"] == "GameData/test.txt"


@pytest.mark.parametrize(
    "policy,available,expected", [("false", "false", 1), ("true", "false", 1), ("true", "true", 0)]
)
def test_required_prerequisite_script_executes_fail_closed(tmp_path, policy, available, expected):
    import os
    import subprocess

    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / ".github/workflows/unity-player-tests.yml").read_text())
    script = config["jobs"]["license"]["steps"][-1]["run"]
    environment = {
        "SystemRoot": os.environ.get("SystemRoot", ""),
        "POLICY": policy,
        "AVAILABLE": available,
    }
    bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    if not Path(bash).exists():
        pytest.skip("Workflow shell unavailable; hosted Linux executes this gate")
    result = subprocess.run(
        [bash, "-c", script], env=environment, check=False, capture_output=True, timeout=10
    )
    assert result.returncode == expected


@pytest.mark.parametrize("player,expected", [("skipped", 1), ("failure", 1), ("success", 0)])
def test_final_required_script_rejects_skipped_or_failed_player(player, expected):
    import os
    import subprocess

    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / ".github/workflows/unity-player-tests.yml").read_text())
    script = config["jobs"]["requiredPlayerE2E"]["steps"][0]["run"]
    environment = {
        "SystemRoot": os.environ.get("SystemRoot", ""),
        "LICENSE_RESULT": "success",
        "UNITY_OK": "true",
        "PLAYER_RESULT": player,
    }
    bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    if not Path(bash).exists():
        pytest.skip("Workflow shell unavailable; hosted Linux executes this gate")
    result = subprocess.run(
        [bash, "-c", script], env=environment, check=False, capture_output=True, timeout=10
    )
    assert result.returncode == expected


def test_no_progress_uses_bounded_startup_deadline(tmp_path):
    write_json(tmp_path / "request.json", {"job_id": "a" * 32})
    result = execute(
        Invocation(
            [sys.executable, "-c", "import time;time.sleep(10)"],
            tmp_path,
            timeout=0.3,
            deadline_after_progress=0.1,
        )
    )
    assert result["forced_termination"] and not result["live_deadline_armed"]
    assert not result["progress_observations"]


@pytest.mark.parametrize("matching", [True, False])
def test_post_live_deadline_requires_matching_owned_identity(tmp_path, matching):
    import time

    request = {
        "job_id": "a" * 32,
        "definition_hash": "b" * 64,
        "build_id": "c" * 32,
        "payload_hash": "d" * 64,
        "build_source_revision": "fixture",
    }
    write_json(tmp_path / "request.json", request)
    script = (
        "import json,os,pathlib,time; time.sleep(0.4); "
        f"p=pathlib.Path({str(tmp_path / 'progress.json')!r}); "
        f"v={request!r}; "
        f"v.update(process_id=os.getpid()+{0 if matching else 1},main_loop_sequence=1,heartbeat_unix_ms=1,sequence=1,elapsed_ms=1); "
        "p.write_text(json.dumps(v)); time.sleep(10)"
    )
    started = time.monotonic()
    result = execute(
        Invocation(
            [getattr(sys, "_base_executable", sys.executable), "-c", script],
            tmp_path,
            timeout=1,
            deadline_after_progress=0.15,
        )
    )
    assert result["forced_termination"] and result["live_deadline_armed"] is matching
    assert time.monotonic() - started >= 0.4
    assert not result["cancel_requested"]


def test_caller_label_cannot_replace_trusted_build_revision(native):
    directory, bundle, request, process, report = native
    request["source_revision"] = "caller-run-label"
    report["reproduction"]["source_revision"] = "different-caller-label"
    (directory / "request.json").write_text(json.dumps(request))
    (directory / "run.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="caller reproduction"):
        check_report(directory, bundle, process)


@pytest.mark.parametrize(
    "arms,passes",
    [
        ({"shared-batches": True, "fresh-process": True}, True),
        ({"shared-batches": True, "fresh-process": False}, False),
        ({"shared-batches": 1, "fresh-process": True}, False),
        (True, False),
        ({"shared-batches": True, "fresh-process": True, "unknown": True}, False),
    ],
)
def test_real_session_per_arm_completion_shape(
    tmp_path, monkeypatch, arms, passes, evicted_mutation=None, expected_message="both arms"
):
    import player_e2e_cli_evidence as cli_evidence
    from player_e2e_artifacts import digest, read_json

    session_id = "a" * 32
    jobs = [character * 32 for character in "bcd"]
    session = tmp_path / "cli-output" / session_id
    runs = session / "runs"
    runs.mkdir(parents=True)
    outcomes = []
    bundle = {
        "build_id": "e" * 32,
        "build_source_revision": "trusted",
        "payload_hash": "f" * 64,
        "definition_hash": "a" * 64,
    }
    for sequence, job in enumerate(jobs, start=1):
        child = runs / job
        if sequence > 1:
            child.mkdir()
            write_json(child / "run.json", {"job_id": job, "status": "succeeded"})
            write_json(child / "request.json", {"job_id": job})
            write_json(
                child / "process.json",
                {
                    "process_id": sequence,
                    "actual_exit_code": 0,
                    "process_ended": True,
                    "forced_termination": False,
                    "started_unix_ms": 50,
                    "finished_unix_ms": 250,
                },
            )
        outcomes.append(
            {
                **bundle,
                "payload_verification": "launcher_admission",
                "mode": "shared-batches" if sequence == 1 else "fresh-process",
                "process_id": sequence,
                "actual_exit_code": 0,
                "process_ended": True,
                "forced_termination": False,
                "process_started_unix_ms": 50,
                "process_finished_unix_ms": 250,
                "request_sha256": digest(child / "request.json") if sequence > 1 else "e" * 64,
                "schema_version": 1,
                "session_id": session_id,
                "sequence": sequence,
                "job_id": job,
                "repeat_count": 2 if sequence == 1 else 1,
                "status": "succeeded",
                "exit_code": 0,
                "native_report_available": True,
                "native_report_sha256": digest(child / "run.json") if sequence > 1 else "e" * 64,
                "artifact_directory": "runs/" + job,
                "started_unix_ms": 100,
                "finished_unix_ms": 200,
                "client_error": None,
            }
        )
    if evicted_mutation:
        outcomes[0].update(evicted_mutation)
    (session / "outcomes.jsonl").write_text("\n".join(json.dumps(entry) for entry in outcomes))
    write_json(
        session / "session.json",
        {
            "schema_version": 1,
            "session_id": session_id,
            "process_scope": "batch",
            "children_started": 3,
            "children_finalized": 3,
            "retained_job_ids": jobs[1:],
            "status": "succeeded",
            "first_failure": None,
            "arms_complete": arms,
            "iterations_per_process": {"shared-batches": 2, "fresh-process": 1},
            "completed_iterations": {"shared-batches": 2, "fresh-process": 2},
        },
    )
    # Native report admission is tested separately; this test exercises the real
    # session wire shape, child ledger reconciliation, hashes and retention cap.
    monkeypatch.setattr(
        cli_evidence, "check_child", lambda directory, bundle: read_json(directory / "run.json")
    )
    process = {
        "forced_termination": False,
        "actual_exit_code": 0,
        "started_unix_ms": 0,
        "finished_unix_ms": 1000,
    }
    if passes:
        cli_evidence.check_cli(tmp_path, bundle, process, "session-succeeded")
    else:
        with pytest.raises(ValueError, match=expected_message):
            cli_evidence.check_cli(tmp_path, bundle, process, "session-succeeded")


def test_wrong_build_keeps_attributed_skipped_preflight_report(native):
    from player_e2e_reports import check_preflight

    directory, bundle, request, process, report = native
    request["build_id"] = "e" * 32
    process.update(actual_exit_code=2, progress_observations=[])
    report.update(
        status="failed", exit_code=2, failure={"code": "player_preflight_failed"}, steps=[]
    )
    (directory / "request.json").write_text(json.dumps(request))
    (directory / "run.json").write_text(json.dumps(report))
    check_preflight(directory, bundle, process)
    report["steps"] = [{"status": "passed"}]
    (directory / "run.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="executed scenario"):
        check_preflight(directory, bundle, process)


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows owned-tree termination")
def test_keyboard_interrupt_reaps_actual_owned_native_descendant(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes
    import subprocess
    import time
    import player_e2e_process as processes

    base = getattr(sys, "_base_executable", sys.executable)
    pid_file = tmp_path / "child-pid.txt"
    script = (
        "import subprocess,pathlib,time; "
        f"child=subprocess.Popen([{base!r},'-c','import time;time.sleep(30)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(30)"
    )
    actual_sleep = time.sleep

    def interrupt_after_child_exists(seconds):
        if pid_file.exists():
            raise KeyboardInterrupt()
        actual_sleep(seconds)

    monkeypatch.setattr(
        processes,
        "time",
        SimpleNamespace(
            time=time.time, monotonic=time.monotonic, sleep=interrupt_after_child_exists
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        execute(Invocation([base, "-c", script], tmp_path, timeout=5))
    child_pid = int(pid_file.read_text())
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x1000 | 0x100000, False, child_pid)
    if handle:
        try:
            exited = api.WaitForSingleObject(handle, 3000) == 0
            if not exited:
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    check=False,
                    timeout=10,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            assert exited, "Owned descendant survived exceptional parent cleanup"
        finally:
            api.CloseHandle(handle)


@pytest.mark.parametrize(
    "mutation,message",
    [
        ({"build_id": "0" * 32}, "frozen identity"),
        ({"payload_verification": None}, "payload scope"),
        ({"process_ended": False}, "finalize naturally"),
        ({"process_id": 2}, "same actual process boundary"),
        ({"native_report_sha256": "bad"}, "hash shape"),
    ],
)
def test_evicted_shared_row_cannot_falsify_identity_or_process(
    tmp_path, monkeypatch, mutation, message
):
    test_real_session_per_arm_completion_shape(
        tmp_path,
        monkeypatch,
        {"shared-batches": True, "fresh-process": True},
        False,
        evicted_mutation=mutation,
        expected_message=message,
    )


@pytest.mark.parametrize("witness", [False, True])
def test_hang_requires_actual_fixture_live_update_witness(tmp_path, witness):
    directory = tmp_path / "cases" / "hang"
    directory.mkdir(parents=True)
    request = {
        "job_id": "a" * 32,
        "definition_hash": "d" * 64,
        "build_id": "b" * 32,
        "payload_hash": "c" * 64,
        "build_source_revision": "trusted-build",
    }
    write_json(directory / "request.json", request)
    progress = {
        **request,
        "schema_version": 1,
        "process_id": 123,
        "sequence": 2,
        "main_loop_sequence": 1,
        "heartbeat_unix_ms": 1200,
        "elapsed_ms": 100,
    }
    write_json(
        directory / "launch.json",
        {
            "process_ended": True,
            "process_id": 123,
            "finished_unix_ms": 2000,
            "forced_termination": True,
            "live_deadline_armed": True,
            "actual_exit_code": 1,
            "progress_observations": [progress],
        },
    )
    (directory / "process.log").write_text(
        "PLAYER_FIXTURE_AFTER_LIVE_UPDATES:hang" if witness else "Player startup"
    )
    receipt = {
        "completed_unix_ms": 2100,
        "artifact_hashes": {entry["path"]: entry["sha256"] for entry in inventory(directory)},
    }
    if witness:
        checker.check_case(tmp_path, "hang", {}, receipt)
    else:
        with pytest.raises(ValueError, match="actual main-loop hang"):
            checker.check_case(tmp_path, "hang", {}, receipt)


@pytest.mark.parametrize("outcome", ["normal", "timeout", "interrupt"])
def test_owned_stdout_pipe_is_closed_after_actual_process(tmp_path, monkeypatch, outcome):
    import player_e2e_process as processes

    children = []
    actual_popen = processes.subprocess.Popen

    def capture_child(*args, **kwargs):
        child = actual_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(processes.subprocess, "Popen", capture_child)
    script = "print('completed')" if outcome == "normal" else "import time;time.sleep(10)"
    invocation = Invocation([sys.executable, "-c", script], tmp_path, timeout=0.2)
    if outcome == "interrupt":
        actual_clock = processes.time
        actual_sleep = actual_clock.sleep

        def interrupt(_seconds):
            raise KeyboardInterrupt()

        # Subprocess reaping must retain the standard-library sleep function.
        monkeypatch.setattr(
            processes,
            "time",
            SimpleNamespace(
                time=actual_clock.time, monotonic=actual_clock.monotonic, sleep=interrupt
            ),
        )
        assert actual_clock.sleep is actual_sleep
        with pytest.raises(KeyboardInterrupt):
            execute(invocation)
    else:
        execute(invocation)
    assert children[0].poll() is not None
    assert children[0].stdout.closed
