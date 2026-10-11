"""Maintained genuine same-process Player soak commands and independent evidence checks."""

import json
from pathlib import Path
import sys
from xml.etree import ElementTree

from player_e2e_artifacts import digest, read_json, unique_object
from player_e2e_reports import require

CASES = {
    "soak-100": (100, "success", "stop", 600),
    "soak-late-stop": (20, "soak-late-failure", "stop", 180),
    "soak-late-continue": (20, "soak-late-failure", "continue", 180),
    "soak-cancel": (20, "soak-cancel", "continue", 180),
    "soak-timeout": (20, "soak-timeout", "continue", 180),
    "soak-deadline": (100, "success", "continue", 1),
    "soak-initial-io": (20, "success", "continue", 180),
    "soak-mid-io": (20, "soak-mid-io", "continue", 180),
    "soak-final-io": (3, "soak-final-io", "continue", 180),
    "soak-resources": (20, "soak-resources", "stop", 180),
    "soak-unlabeled": (3, "success", "stop", 180),
}


def soak_command(
    case: str, build: Path, directory: Path, revision: str | None
) -> tuple[list[str], str]:
    """Use the real CLI, one native child and a bounded explicit soak request."""
    iterations, mode, policy, runtime = CASES[case]
    command = [
        sys.executable,
        "-m",
        "cli.main",
        "--format",
        "json",
        "play-scenario",
        "player-soak",
        str(build),
    ]
    if case == "soak-initial-io":
        command = [
            sys.executable,
            str(Path(__file__).with_name("player_e2e_soak_startup.py")),
            str(build),
        ]
    command += [
        "--output-dir",
        str(directory / "cli-output"),
        "--iterations",
        str(iterations),
        "--max-runtime-seconds",
        str(runtime),
        "--timeout-seconds",
        "2" if case == "soak-timeout" else "30",
        "--cleanup-wait-seconds",
        "10",
        "--failure-policy",
        policy,
        "--retain-reports",
        "3",
    ]
    if revision is not None:
        command += ["--expected-build-revision", revision]
    return command, mode


def _rows(path: Path, limit: int, rows: int, line_limit: int) -> list[dict]:
    require(path.stat().st_size <= limit, "Soak journal exceeded byte bound")
    payload = path.read_bytes()
    require(
        not payload.startswith(b"\xef\xbb\xbf") and payload.endswith(b"\n"),
        "Journal encoding or final LF differs",
    )
    lines = payload.splitlines(keepends=True)
    require(
        len(lines) <= rows and all(len(line) <= line_limit for line in lines),
        "Journal row bound exceeded",
    )
    return [json.loads(line, object_pairs_hook=unique_object) for line in lines]


def check_soak_case(directory: Path, bundle: dict, invocation: dict, case: str) -> None:
    """Reconcile real PID, immutable identities, journal, retention, failures and JUnit."""
    require(not invocation["forced_termination"], "Soak CLI was forcibly terminated")
    roots = list((directory / "cli-output").iterdir())
    require(
        len(roots) == 1 and roots[0].is_dir(), "Soak must own exactly one native output directory"
    )
    root = roots[0]
    request = read_json(root / "request.json", 16384)
    native = read_json(root / "process.json", 65536)
    client = read_json(root / "client-soak.json", 65536)
    diagnostics = read_json(root / "diagnostics.json", 65536)
    require(
        native["process_ended"] and not native["forced_termination"],
        "Native soak did not exit naturally",
    )
    require(
        native["job_id"] == request["job_id"] == root.name == client["job_id"],
        "Soak job attribution differs",
    )
    require(
        invocation["started_unix_ms"]
        <= native["started_unix_ms"]
        <= native["finished_unix_ms"]
        <= invocation["finished_unix_ms"],
        "Native soak process is stale",
    )
    for key in ("build_id", "definition_hash", "payload_hash", "build_source_revision"):
        require(request[key] == bundle[key], "Soak frozen request differs: " + key)
    requested, _, policy, runtime = CASES[case]
    require(
        request["repeat_count"] == 1
        and request["soak"]
        == {
            "iterations": requested,
            "max_runtime_seconds": runtime,
            "failure_policy": policy,
            "retain_reports": 3,
        },
        "Soak options differ from maintained case",
    )
    junit = ElementTree.parse(root / "junit.xml").getroot()
    require(
        len(junit.findall("testcase")) == int(junit.attrib["tests"]) >= requested,
        "Soak JUnit omits requested iterations",
    )
    persistence = case in {"soak-initial-io", "soak-mid-io", "soak-final-io"}
    if persistence:
        require(
            invocation["actual_exit_code"] != 0 and native["actual_exit_code"] == 2,
            "Persistence failure lost its nonzero native/CLI outcome",
        )
        require(
            client["evidence_state"] == "incomplete"
            and client["status"] != "succeeded"
            and client["client_error"],
            "Persistence failure was accepted as complete",
        )
        require(junit.findall("testcase/error"), "Incomplete persistence JUnit lacks errors")
        log = (directory / "process.log").read_text(encoding="utf-8", errors="replace")
        require(
            "success" not in log.lower() or '"success": false' in log.lower(),
            "Persistence CLI output claims success",
        )
        if case == "soak-initial-io":
            require(
                (root / "soak.json.tmp").is_dir() and not (root / "soak.json").exists(),
                "Initial checkpoint failure was not injected",
            )
            require(
                not (root / "iteration-0001.json").exists(),
                "Initial failure fabricated an attempted iteration",
            )
            startup = read_json(root / "run.json")
            require(
                startup["status"] == "failed"
                and startup["exit_code"] == 2
                and startup["runner_resources_released"]
                and startup["finalization_state"] == "completed"
                and "soak.json.tmp" in startup["error"],
                "Initial persistence failure lost its original native error or release state",
            )
            require(
                all(
                    row["status"] == "skipped" and row["started_unix_ms"] is None
                    for row in startup["iteration_results"]
                ),
                "Initial persistence failure fabricated native repeat starts",
            )
        elif case == "soak-mid-io":
            rows = _rows(root / "iterations.jsonl", 16384000, 1000, 16384)
            require(
                len(rows) == 12 and (root / "iteration-0013.json.tmp").is_dir(),
                "Mid-iteration persistence failure lost prior evidence",
            )
        else:
            require(
                (root / "run.json.tmp").is_dir() and not (root / "run.json").exists(),
                "Final report persistence was not blocked",
            )
            require(
                read_json(root / "soak.json", 65536)["journal_rows"] == 3,
                "Final persistence failure lost completed native journal",
            )
        return
    require(
        client["evidence_state"] == "complete"
        and not client["client_error"]
        and diagnostics["cleanup_state"] == "native_finalized",
        "CLI rejected complete native soak",
    )
    summary = read_json(root / "soak.json", 65536)
    report = read_json(root / "run.json")
    require(
        report["soak"] == summary
        and summary["finalization_state"] == "completed"
        and summary["journal_complete"],
        "Native soak checkpoint/projection is incomplete",
    )
    rows = _rows(root / "iterations.jsonl", 16384000, 1000, 16384)
    require(
        summary["journal_rows"] == len(rows)
        and summary["journal_bytes"] == (root / "iterations.jsonl").stat().st_size
        and summary["journal_sha256"] == digest(root / "iterations.jsonl"),
        "Native journal integrity differs",
    )
    identity = {
        key: request[key]
        for key in (
            "job_id",
            "definition_hash",
            "build_id",
            "payload_hash",
            "build_source_revision",
        )
    }
    for index, row in enumerate(rows, 1):
        require(
            row["iteration"] == index
            and row["process_id"] == native["process_id"] == summary["process_id"],
            "Soak restarted its native process or reordered iterations",
        )
        require(
            all(row[key] == value for key, value in identity.items())
            and row["runner_resources_released"],
            "Soak row identity or resource release differs",
        )
    retained = sorted(path.name for path in root.glob("iteration-*.json"))
    require(
        retained == sorted(summary["retained_report_files"]) and len(retained) <= 3,
        "Retained full native reports exceed bound",
    )
    require(
        report["repeat_count"] == 1
        and len(report["iteration_results"]) == 1
        and report["iteration_results"][0]["iteration"] == 1,
        "Outer soak fabricated engine repeat ledger",
    )
    _check_progress(root, invocation, request, native["process_id"])
    success = case in {"soak-100", "soak-resources", "soak-unlabeled"}
    require(
        (invocation["actual_exit_code"] == native["actual_exit_code"] == 0) == success,
        "Soak exit codes differ from whole-session result",
    )
    if success:
        require(
            summary["status"] == "succeeded"
            and len(rows) == requested
            and all(row["status"] == "succeeded" for row in rows),
            "Requested same-PID iterations did not all pass",
        )
        require(
            not junit.findall("testcase/error") and not junit.findall("testcase/failure"),
            "Passing soak JUnit contains failures",
        )
    elif case.startswith("soak-late-"):
        require(
            summary["status"] == "failed"
            and summary["first_failure_iteration"] == summary["selected_iteration"] == 13
            and "iteration-0013.json" in retained,
            "Later first failure was not selected and retained",
        )
        require(
            len(rows) == (13 if case.endswith("stop") else 20)
            and rows[12]["status"] == "failed"
            and report["status"] == "failed",
            "Soak failure policy differs",
        )
        require(
            "iteration 13 main failure" in report["error"] and junit.findall("testcase/failure"),
            "Original later failure disappeared",
        )
    else:
        expected = "cancelled" if case == "soak-cancel" else "timed_out"
        require(
            summary["status"] == expected
            and len(rows) < requested
            and native["actual_exit_code"] == 1,
            "Cancellation/deadline did not stop the soak",
        )
        require(
            junit.findall("testcase/skipped"), "Unattempted terminal iterations were not skipped"
        )
        if case == "soak-cancel":
            require(
                invocation["cancel_requested"] and len(rows) == 13,
                "Cancellation did not reach iteration 13",
            )
        if case == "soak-timeout":
            require(
                len(rows) == 13, "Per-iteration timeout did not preserve completed predecessors"
            )
    if case == "soak-resources":
        _check_resources(root, rows, native["process_id"], summary)
    if case == "soak-unlabeled":
        require(
            summary["build_source_revision"] is None
            and summary["metrics_summary"] is None
            and all(row["metrics"] == [] for row in rows),
            "Unlabeled optional-diagnostics-disabled Player changed frozen identity or invented samples",
        )


def _check_progress(root: Path, invocation: dict, request: dict, pid: int) -> None:
    samples = invocation["progress_observations"]
    final = read_json(root / "progress.json", 16384)
    require(1 <= len(samples) <= 1024, "Missing bounded global soak progress observations")
    previous = {key: -1 for key in ("sequence", "elapsed_ms", "main_loop_sequence", "iteration")}
    for sample in samples:
        require(
            sample["process_id"] == pid
            and all(
                sample[key] == request[key]
                for key in (
                    "job_id",
                    "definition_hash",
                    "build_id",
                    "payload_hash",
                    "build_source_revision",
                )
            ),
            "Global progress identity differs",
        )
        for key, value in previous.items():
            require(
                type(sample[key]) is int and sample[key] >= value,
                "Soak progress regressed across engines: " + key,
            )
        require(sample["sequence"] > previous["sequence"], "Global progress sequence repeated")
        previous = {key: sample[key] for key in previous}
    require(
        final["process_id"] == pid
        and final["main_loop_sequence"] > 0
        and final["heartbeat_unix_ms"] > 0
        and final["iteration"] <= request["soak"]["iterations"],
        "Soak lacks actual native Update progress",
    )
    require(
        all(final[key] >= value for key, value in previous.items()),
        "Final global progress regressed",
    )


def _check_resources(root: Path, rows: list[dict], pid: int, summary: dict) -> None:
    witnesses = _rows(root / "fixture-resources.jsonl", 1048576, 2000, 4096)
    require(len(witnesses) == 2 * len(rows), "Runtime resource witness is incomplete")
    for index, row in enumerate(witnesses):
        require(
            row["process_id"] == pid
            and row["iteration"] == index // 2 + 1
            and row["error"] is None,
            "Resource witness attribution differs",
        )
        retained = index % 2 == 0
        require(
            row["stage"] == ("retained" if retained else "destroyed-and-disposed"),
            "Resource lifetime stages differ",
        )
        require(
            all(
                row[key] == int(retained)
                for key in ("scriptable_objects", "subscriptions", "handles")
            ),
            "Native clone/token lifetime was not observed",
        )
        require(row["handle_closed"] != retained, "Game handle was not actually closed")
    metrics = summary["metrics_summary"]
    require(
        metrics is not None
        and metrics["sample_count"] == len(rows)
        and len(metrics["bounded_samples"]) <= 10,
        "Opt-in rolling metrics were not bounded",
    )
    require("leak" in metrics["summary"].lower(), "Memory diagnostic limitation disappeared")
    for row in rows:
        require(len(row["metrics"]) == 1, "Opt-in native boundary metric missing")
        sample = row["metrics"][0]
        require(
            sample["iteration"] == row["iteration"]
            and sample["runner_subscription_count"] == 1
            and sample["runner_handle_count"] == 1,
            "Boundary metric runner attribution differs",
        )
