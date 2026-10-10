"""Verify real CLI invocations and bounded session retention evidence."""

import json
import re
from pathlib import Path

from player_e2e_artifacts import contained, digest, read_json, unique_object
from player_e2e_reports import check_report, check_progress, require


def check_child(directory: Path, bundle: dict, *, cleanup_failure: bool = False) -> dict:
    """CLI success must be backed by its native child exit and report, not stdout."""
    process = read_json(directory / "process.json", 65536)
    report = check_report(directory, bundle, process, cleanup_failure=cleanup_failure)
    snapshots = process.get("progress_observations", [])
    final = read_json(directory / "progress.json", 16384)
    # The launcher retains compact counters after its identity checks. Recheck the
    # actual final snapshot identity independently and reconcile its sequence.
    request = read_json(directory / "request.json")
    check_progress([final], request, process["process_id"])
    require(
        snapshots and snapshots[-1]["sequence"] <= final["sequence"],
        "CLI progress samples exceed final snapshot",
    )
    identity = {
        key: final[key]
        for key in (
            "schema_version",
            "job_id",
            "definition_hash",
            "build_id",
            "payload_hash",
            "build_source_revision",
            "process_id",
        )
    }
    check_progress([{**identity, **sample} for sample in snapshots], request, process["process_id"])
    return report


def check_cli(directory: Path, bundle: dict, process: dict, expected: str) -> None:
    """Reconcile arm totals, every bounded outcome, retained hashes and first failure pinning."""
    require(not process["forced_termination"], "CLI exceeded its owned deadline")
    output = directory / "cli-output"
    roots = []
    if output.exists():
        for path in output.iterdir():
            roots.append(path)
            require(len(roots) <= 1, "CLI output count exceeds one fresh root")
    require(len(roots) == 1, "CLI must produce exactly one fresh output root")
    child_root = roots[0]
    if expected == "cli-succeeded":
        require(process["actual_exit_code"] == 0, "Real CLI repeat invocation failed")
        report = check_child(child_root, bundle)
        require(
            report["status"] == "succeeded" and report["repeat_count"] == 2,
            "Real CLI repeat body did not complete twice",
        )
        return
    summary = read_json(child_root / "session.json", 65536)
    require(child_root.name == summary.get("session_id"), "Session directory/identity mismatch")
    require(
        summary.get("schema_version") == 2 and summary.get("process_scope") == "batch",
        "Session summary schema/scope mismatch",
    )
    jsonl = child_root / "outcomes.jsonl"
    require(jsonl.stat().st_size <= 8 * 1024 * 1024, "Session outcome byte bound exceeded")
    with jsonl.open("rb") as stream:
        payload = stream.read(8 * 1024 * 1024 + 1)
    require(len(payload) <= 8 * 1024 * 1024, "Session outcome grew beyond its byte bound")
    lines = payload.splitlines()
    require(
        1 <= len(lines) <= 2000 and all(len(line) <= 4096 for line in lines),
        "Session outcome count/line bounds exceeded",
    )
    outcomes = [json.loads(line, object_pairs_hook=unique_object) for line in lines]
    require(
        len(outcomes) == summary.get("children_started") == summary.get("children_finalized"),
        "Session child totals differ from real outcomes",
    )
    require(
        len({entry["job_id"] for entry in outcomes}) == len(outcomes),
        "Session reused a job identity",
    )
    retained = summary.get("retained_job_ids")
    expected_count = (
        3 if expected == "session-succeeded" else (1 if expected == "session-stopped" else 2)
    )
    cap = 2 if expected == "session-succeeded" else 1
    require(len(outcomes) == expected_count, "Session stop/continue/comparison count mismatch")
    require(
        isinstance(retained, list) and 1 <= len(retained) <= cap,
        "Session artifact retention exceeds its cap",
    )
    actual = set()
    for path in (child_root / "runs").iterdir():
        actual.add(path.name)
        require(len(actual) <= cap, "Session retained directory count exceeds cap")
    require(actual == set(retained), "Session retained directory list differs from summary")
    invocations = set()
    for index, outcome in enumerate(outcomes, start=1):
        for key in ("build_id", "build_source_revision", "payload_hash", "definition_hash"):
            require(
                outcome.get(key) == bundle.get(key), "Session row frozen identity mismatch: " + key
            )
        require(
            outcome.get("payload_verification") == "launcher_admission",
            "Session row payload scope is unverified",
        )
        require(
            re.fullmatch(r"[0-9a-f]{32}", str(outcome.get("job_id"))) is not None,
            "Session row job identity is invalid",
        )
        for key in ("native_report_sha256", "request_sha256"):
            require(
                re.fullmatch(r"[0-9a-f]{64}", str(outcome.get(key))) is not None,
                "Session row hash shape is invalid: " + key,
            )
        pid = outcome.get("process_id")
        started, finished = (
            outcome.get("process_started_unix_ms"),
            outcome.get("process_finished_unix_ms"),
        )
        require(
            type(pid) is int
            and 0 < pid <= 2**32 - 1
            and type(started) is int
            and type(finished) is int
            and process["started_unix_ms"] <= started <= finished <= process["finished_unix_ms"],
            "Session row lacks a fresh actual process boundary",
        )
        require(
            outcome.get("process_ended") is True and outcome.get("forced_termination") is False,
            "Session row process did not finalize naturally",
        )
        require(
            type(outcome.get("actual_exit_code")) is int
            and outcome["actual_exit_code"] == outcome.get("exit_code"),
            "Session row actual exit differs from recorded outcome",
        )
        invocation = (pid, started)
        require(invocation not in invocations, "Session reused the same actual process boundary")
        invocations.add(invocation)
        expected_mode = (
            "shared-batches" if expected == "session-succeeded" and index == 1 else "fresh-process"
        )
        require(
            outcome.get("mode") == expected_mode
            and outcome.get("repeat_count") == (2 if expected_mode == "shared-batches" else 1),
            "Session row arm/process scope differs",
        )
        require(
            outcome.get("sequence") == index
            and outcome.get("session_id") == summary.get("session_id"),
            "Session outcome sequence/identity mismatch",
        )
        require(
            process["started_unix_ms"]
            <= outcome.get("started_unix_ms", 0)
            <= outcome.get("finished_unix_ms", 0)
            <= process["finished_unix_ms"],
            "Session outcome lifetime is stale",
        )
        require(
            outcome.get("native_report_available") is True and outcome.get("client_error") is None,
            "Session outcome lacks native finalization",
        )
        require(
            outcome.get("status") == ("succeeded" if expected == "session-succeeded" else "failed"),
            "Unexpected native session outcome",
        )
        require(
            outcome.get("exit_code") == (0 if expected == "session-succeeded" else 1),
            "Session native exit mismatch",
        )
        if outcome["job_id"] in retained:
            child = contained(child_root, outcome["artifact_directory"])
            require(child == child_root / "runs" / outcome["job_id"], "Session child path mismatch")
            require(
                digest(child / "run.json") == outcome.get("native_report_sha256"),
                "Retained child report hash mismatch",
            )
            report = check_child(child, bundle)
            child_process = read_json(child / "process.json", 65536)
            for key in ("process_id", "actual_exit_code", "process_ended", "forced_termination"):
                require(
                    outcome.get(key) == child_process.get(key),
                    "Session retained process evidence mismatch",
                )
            require(
                started == child_process.get("started_unix_ms")
                and finished == child_process.get("finished_unix_ms"),
                "Session retained process lifetime mismatch",
            )
            require(
                outcome["request_sha256"] == digest(child / "request.json"),
                "Session retained request hash mismatch",
            )
            require(
                report["job_id"] == outcome["job_id"] and report["status"] == outcome["status"],
                "Retained child identity/status mismatch",
            )
    if expected == "session-succeeded":
        arms = summary.get("arms_complete")
        require(
            isinstance(arms, dict)
            and set(arms) == {"shared-batches", "fresh-process"}
            and all(value is True for value in arms.values()),
            "Comparison session did not complete both arms",
        )
        require(
            process["actual_exit_code"] == 0
            and summary.get("status") == "succeeded"
            and summary.get("arms_complete") == {"shared-batches": True, "fresh-process": True}
            and summary.get("first_failure") is None,
            "Comparison session did not complete both arms",
        )
        require(
            summary.get("iterations_per_process") == {"shared-batches": 2, "fresh-process": 1},
            "Comparison session process scopes differ",
        )
        require(
            summary.get("completed_iterations") == {"shared-batches": 2, "fresh-process": 2},
            "Comparison session iteration totals differ",
        )
        require(
            [entry["repeat_count"] for entry in outcomes] == [2, 1, 1],
            "Comparison session did not use actual distinct process scopes",
        )
    else:
        first = summary.get("first_failure", {})
        require(
            process["actual_exit_code"] == 1
            and summary.get("status") == "failed"
            and first.get("job_id") == outcomes[0]["job_id"]
            and first.get("job_id") in retained,
            "Session first failure was lost under retention",
        )
