"""Focused regression coverage for maintained same-PID Player evidence checks."""

from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player_e2e_artifacts import write_json
from player_e2e_soak import CASES, check_soak_case, soak_command


@pytest.mark.parametrize("case", CASES)
def test_commands_keep_one_native_repeat_and_explicit_soak_options(tmp_path, case):
    command, mode = soak_command(case, tmp_path / "build", tmp_path / "case", "trusted")
    assert "--repeat-count" not in command
    assert command[command.index("--iterations") + 1] == str(CASES[case][0])
    assert command[command.index("--failure-policy") + 1] == CASES[case][2]
    assert command[command.index("--retain-reports") + 1] == "3"
    assert mode == CASES[case][1]


def test_unlabeled_build_keeps_expected_revision_absent(tmp_path):
    command, _ = soak_command("soak-unlabeled", tmp_path / "build", tmp_path / "case", None)
    assert "--expected-build-revision" not in command
    assert None not in command


@pytest.fixture
def complete(tmp_path):
    case = tmp_path / "case"
    root = case / "cli-output" / ("a" * 32)
    root.mkdir(parents=True)
    bundle = {
        "build_id": "b" * 32,
        "payload_hash": "c" * 64,
        "definition_hash": "d" * 64,
        "build_source_revision": "trusted",
    }
    identity = {**bundle, "job_id": "a" * 32}
    request = {
        **identity,
        "repeat_count": 1,
        "soak": {
            "iterations": 100,
            "max_runtime_seconds": 600,
            "failure_policy": "stop",
            "retain_reports": 3,
        },
    }
    write_json(root / "request.json", request)
    process = {
        "job_id": identity["job_id"],
        "process_id": 123,
        "started_unix_ms": 100,
        "finished_unix_ms": 900,
        "actual_exit_code": 0,
        "process_ended": True,
        "forced_termination": False,
    }
    write_json(root / "process.json", process)
    write_json(
        root / "client-soak.json",
        {
            "job_id": identity["job_id"],
            "status": "succeeded",
            "evidence_state": "complete",
            "client_error": None,
        },
    )
    write_json(root / "diagnostics.json", {"cleanup_state": "native_finalized"})
    rows = [
        {
            **identity,
            "iteration": i,
            "process_id": 123,
            "status": "succeeded",
            "runner_resources_released": True,
        }
        for i in range(1, 101)
    ]
    payload = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    (root / "iterations.jsonl").write_bytes(payload)
    retained = ["iteration-0098.json", "iteration-0099.json", "iteration-0100.json"]
    for filename in retained:
        write_json(root / filename, {})
    summary = {
        "status": "succeeded",
        "journal_complete": True,
        "finalization_state": "completed",
        "journal_rows": 100,
        "journal_bytes": len(payload),
        "journal_sha256": sha256(payload).hexdigest(),
        "retained_report_files": retained,
        "process_id": 123,
    }
    write_json(root / "soak.json", summary)
    write_json(
        root / "run.json",
        {"soak": summary, "repeat_count": 1, "iteration_results": [{"iteration": 1}]},
    )
    (root / "junit.xml").write_text(
        '<testsuite tests="100">' + "<testcase />" * 100 + "</testsuite>", encoding="utf-8"
    )
    invocation = {
        "forced_termination": False,
        "actual_exit_code": 0,
        "started_unix_ms": 50,
        "finished_unix_ms": 1000,
        "progress_observations": [
            {
                **identity,
                "process_id": 123,
                "sequence": i,
                "elapsed_ms": i,
                "main_loop_sequence": i,
                "iteration": i,
            }
            for i in range(1, 101)
        ],
    }
    write_json(
        root / "progress.json",
        {**invocation["progress_observations"][-1], "heartbeat_unix_ms": 900},
    )
    return case, root, bundle, invocation


def test_hundred_same_pid_rows_are_admitted(complete):
    case, _, bundle, invocation = complete
    check_soak_case(case, bundle, invocation, "soak-100")


@pytest.mark.parametrize(
    "fault", ["pid", "reorder", "hash", "unfinished", "retention", "native_exit", "junit"]
)
def test_positive_soak_rejects_inconsistent_underlying_evidence(complete, fault):
    case, root, bundle, invocation = complete
    if fault in {"pid", "reorder"}:
        payload = (root / "iterations.jsonl").read_bytes()
        payload = (
            payload.replace(b'"process_id": 123', b'"process_id": 456', 1)
            if fault == "pid"
            else payload.replace(b'"iteration": 1,', b'"iteration": 2,', 1)
        )
        (root / "iterations.jsonl").write_bytes(payload)
        summary = json.loads((root / "soak.json").read_text())
        summary["journal_sha256"] = sha256(payload).hexdigest()
        (root / "soak.json").write_text(json.dumps(summary), encoding="utf-8")
        report = json.loads((root / "run.json").read_text())
        report["soak"] = summary
        (root / "run.json").write_text(json.dumps(report), encoding="utf-8")
    elif fault in {"hash", "unfinished"}:
        summary = json.loads((root / "soak.json").read_text())
        summary["journal_sha256" if fault == "hash" else "journal_complete"] = (
            "0" * 64 if fault == "hash" else False
        )
        (root / "soak.json").write_text(json.dumps(summary), encoding="utf-8")
        report = json.loads((root / "run.json").read_text())
        report["soak"] = summary
        (root / "run.json").write_text(json.dumps(report), encoding="utf-8")
    elif fault == "retention":
        write_json(root / "iteration-0001.json", {})
    elif fault == "native_exit":
        native = json.loads((root / "process.json").read_text())
        native["actual_exit_code"] = 1
        (root / "process.json").write_text(json.dumps(native), encoding="utf-8")
    else:
        (root / "junit.xml").write_text('<testsuite tests="100"><testcase /></testsuite>')
    with pytest.raises(ValueError):
        check_soak_case(case, bundle, invocation, "soak-100")
