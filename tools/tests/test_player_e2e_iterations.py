"""Regression checks for later-iteration evidence and synchronized cancellation."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player_e2e_iterations import check_native_iterations, expected_counts, iteration_command
from player_e2e_process import Invocation, execute


@pytest.fixture
def later_report():
    return {
        "iteration_results_version": 1,
        "started_unix_ms": 100,
        "finished_unix_ms": 900,
        "iteration_results": [
            {"iteration": 1, "status": "passed", "started_unix_ms": 100, "finished_unix_ms": 300},
            {"iteration": 2, "status": "failed", "started_unix_ms": 301, "finished_unix_ms": 800},
            {"iteration": 3, "status": "skipped", "started_unix_ms": None, "finished_unix_ms": 900},
        ],
        "scenario": {"steps": [{"name": "first", "action": "wait_state"}]},
        "failure": {"iteration": 2, "stage": "main"},
        "steps": [
            {"iteration": 1, "stage": "main", "step_index": 0, "status": "passed"},
            {"iteration": 3, "status": "skipped"},
        ],
    }


def test_native_later_failure_accepts_untouched_third_slot(later_report):
    check_native_iterations(later_report, "failed")


@pytest.mark.parametrize("field,value", [("status", "passed"), ("started_unix_ms", 400)])
def test_native_untouched_slot_cannot_claim_success_or_execution(later_report, field, value):
    later_report["iteration_results"][2][field] = value
    with pytest.raises(ValueError):
        check_native_iterations(later_report, "failed")


def test_stop_counts_include_future_unscheduled_slots():
    assert expected_counts(6, 1, "failed") == {
        "planned": 6,
        "executed": 2,
        "pending": 0,
        "running": 0,
        "passed": 1,
        "failed": 1,
        "timed_out": 0,
        "cancelled": 0,
        "skipped": 4,
        "unknown": 0,
    }


@pytest.mark.parametrize("policy", ["stop", "continue"])
def test_phase_failure_command_runs_actual_shared_batches(tmp_path, policy):
    command, mode, cancel = iteration_command(
        "iteration-cleanup-" + policy, tmp_path, tmp_path, "trusted"
    )
    assert "player-session" in command and "shared-batches" in command
    assert command[command.index("--iterations") + 1] == "6"
    assert command[command.index("--batch-size") + 1] == "3"
    assert mode == "iteration-cleanup-failure" and cancel is None


@pytest.mark.parametrize("iteration,cancelled", [(1, False), (2, True)])
def test_cancellation_waits_for_actual_owned_later_progress(tmp_path, iteration, cancelled):
    child = tmp_path / "cli-output" / ("a" * 32)
    child.mkdir(parents=True)
    request = {
        "job_id": "a" * 32,
        "definition_hash": "b" * 64,
        "build_id": "c" * 32,
        "payload_hash": "d" * 64,
        "build_source_revision": "fixture",
    }
    (child / "request.json").write_text(json.dumps(request))
    script = (
        "import json,os,pathlib,time; "
        f"p=pathlib.Path({str(child)!r});v={request!r}; "
        f"v.update(process_id=os.getpid(),main_loop_sequence=1,heartbeat_unix_ms=1,sequence=1,elapsed_ms=1,iteration={iteration},stage='main',step_index=2); "
        "(p/'progress.json').write_text(json.dumps(v));deadline=time.monotonic()+1; "
        "exec('while time.monotonic()<deadline and not (p/\"cancel\").exists(): time.sleep(0.02)')"
    )
    process = execute(
        Invocation(
            [sys.executable, "-c", script],
            tmp_path,
            timeout=5,
            cancel_on_progress=True,
            cancel_iteration=2,
        )
    )
    assert process["cancel_requested"] is cancelled
    assert (child / "cancel").exists() is cancelled
    assert process["process_ended"] and not process["forced_termination"]
