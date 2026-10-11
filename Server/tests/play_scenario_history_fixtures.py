"""Coherent compact session journals for offline history regression tests."""

import json
import socket
import subprocess

import pytest


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _session(directory, statuses=("passed", "failed", "failed")):
    directory.mkdir()
    mode = "fresh-process"
    header = dict(
        schema_version=2,
        session_id="a" * 32,
        mode=mode,
        requested_iterations=len(statuses),
        iterations_per_process={mode: 1},
        started_unix_ms=1000,
    )
    _write(directory / "session-start.json", header)
    admissions, outcomes = [], []
    total = dict.fromkeys(
        (
            "planned",
            "executed",
            "pending",
            "running",
            "passed",
            "failed",
            "timed_out",
            "cancelled",
            "skipped",
            "unknown",
        ),
        0,
    )
    for index, state in enumerate(statuses, 1):
        admission = dict(
            schema_version=1,
            session_id=header["session_id"],
            sequence=index,
            mode=mode,
            job_id=f"{index:032x}",
            repeat_count=1,
        )
        admissions.append(admission)
        counts = dict(total)
        counts.update(planned=1, executed=1)
        counts[state] = 1
        row = dict(
            admission,
            schema_version=2,
            status="succeeded" if state == "passed" else state,
            exit_code=0 if state == "passed" else 1,
            native_report_available=True,
            native_report_sha256="b" * 64,
            process_id=index,
            process_ended=True,
            actual_exit_code=0 if state == "passed" else 1,
            request_sha256="c" * 64,
            process_started_unix_ms=1000 + index * 100,
            process_finished_unix_ms=1050 + index * 100,
            iteration_counts=counts,
            iteration_results_source="native",
            definition_hash="d" * 64,
            build_id="e" * 32,
            payload_hash="f" * 64,
            failure=None if state == "passed" else dict(code="ASSERT_FAILED", iteration=1),
        )
        outcomes.append(row)
    for row in outcomes:
        for key, value in row["iteration_counts"].items():
            total[key] += value
    for name, rows in (("admissions", admissions), ("outcomes", outcomes)):
        (directory / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    _write(
        directory / "session.json",
        dict(
            header,
            status="failed" if any(state != "passed" for state in statuses) else "succeeded",
            finished_unix_ms=2000,
            iteration_counts={mode: total},
            scheduled_iterations={mode: len(statuses)},
            completed_iterations={mode: len(statuses)},
            children_started=len(statuses),
            children_finalized=len(statuses),
            outcomes_recorded=len(statuses),
            first_failure=next((row for row in outcomes if row["exit_code"]), None),
            session_error=None,
            active_child=None,
        ),
    )
    return directory


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("History analysis attempted execution or networking")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
