"""Later-iteration cases backed by actual CLI/native Player evidence."""

import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from player_e2e_artifacts import contained, digest, read_json, unique_object
from player_e2e_cli_evidence import check_child
from player_e2e_reports import require


def iteration_command(
    case: str, build: Path, directory: Path, revision: str
) -> tuple[list[str], str, int | None]:
    """Run a bounded shared batch or one repeat with the maintained fixture mode."""
    command = [sys.executable, "-m", "cli.main", "--format", "json", "play-scenario"]
    if case in ("iteration-cancel", "iteration-timeout"):
        command += ["player-run", str(build), "--repeat-count", "3", "--require-verified-payload"]
        mode = case
        cancel_iteration = 2 if case == "iteration-cancel" else None
    else:
        _, stage, policy = case.split("-")
        command += [
            "player-session",
            str(build),
            "--mode",
            "shared-batches",
            "--iterations",
            "6",
            "--batch-size",
            "3",
            "--failure-policy",
            policy,
            "--retain-reports",
            "2",
            "--max-runtime-seconds",
            "120",
            "--interval-seconds",
            "0",
        ]
        mode = "iteration-" + stage + "-failure"
        cancel_iteration = None
    command += [
        "--output-dir",
        str(directory / "cli-output"),
        "--timeout-seconds",
        "30",
        "--cleanup-wait-seconds",
        "10",
        "--expected-build-revision",
        revision,
    ]
    return command, mode, cancel_iteration


def check_native_iterations(report: dict, status: str) -> None:
    """Require actual later failure and wholly untouched third-iteration slots."""
    require(report.get("iteration_results_version") == 1, "Native iteration ledger is missing")
    rows = report.get("iteration_results")
    require(isinstance(rows, list) and len(rows) == 3, "Native iteration ledger length differs")
    require([row.get("iteration") for row in rows] == [1, 2, 3], "Native iteration IDs differ")
    require(
        [row.get("status") for row in rows] == ["passed", status, "skipped"],
        "Native later-iteration outcomes differ",
    )
    for row in rows[:2]:
        require(
            type(row.get("started_unix_ms")) is int
            and type(row.get("finished_unix_ms")) is int
            and report["started_unix_ms"]
            <= row["started_unix_ms"]
            <= row["finished_unix_ms"]
            <= report["finished_unix_ms"],
            "Executed iteration has invalid native timestamps",
        )
    require(rows[2].get("started_unix_ms") is None, "Untouched native iteration claims execution")
    require(rows[0]["finished_unix_ms"] <= rows[1]["started_unix_ms"], "Native iterations overlap")
    require(
        all(
            step.get("status") == "passed" for step in report["steps"] if step.get("iteration") == 1
        ),
        "The first native iteration did not pass fully",
    )
    require(
        all(
            step.get("status") == "skipped"
            for step in report["steps"]
            if step.get("iteration") == 3
        ),
        "The untouched third native iteration executed a step",
    )
    for stage, field in (("setup", "setup_steps"), ("main", "steps"), ("cleanup", "cleanup_steps")):
        authored = report["scenario"].get(field, [])
        observed = [
            step
            for step in report["steps"]
            if step.get("iteration") == 1 and step.get("stage") == stage
        ]
        require(len(observed) == len(authored), "First native iteration has missing authored steps")
        require(
            [step.get("step_index") for step in observed] == list(range(len(authored))),
            "First native iteration step indices differ",
        )
    failure = report.get("failure") or {}
    require(failure.get("iteration") == 2, "Native failure was not attributed to iteration 2")


def expected_counts(planned: int, batches: int, status: str) -> dict:
    """The fixture passes one iteration, fails one, and skips the remainder per batch."""
    counts = dict.fromkeys(
        ("pending", "running", "passed", "failed", "timed_out", "cancelled", "skipped", "unknown"),
        0,
    )
    counts.update(
        planned=planned, executed=2 * batches, passed=batches, skipped=planned - 2 * batches
    )
    counts[status] = batches
    return counts


def check_iteration_case(directory: Path, bundle: dict, process: dict, case: str) -> None:
    """Reconcile native ledgers with actual nonzero CLI exit, journal, summary and JUnit."""
    require(
        process.get("actual_exit_code") != 0 and not process.get("forced_termination"),
        "Later native failure did not produce a natural nonzero CLI exit",
    )
    roots = list((directory / "cli-output").iterdir())
    require(len(roots) == 1, "Later-iteration CLI did not produce one fresh output root")
    root = roots[0]
    if case in ("iteration-cancel", "iteration-timeout"):
        status = "cancelled" if case == "iteration-cancel" else "timed_out"
        report = check_child(root, bundle)
        require(report.get("status") == status, "Later native interruption status differs")
        check_native_iterations(report, status)
        require(
            report.get("failure", {}).get("stage") == "main", "Interruption occurred outside main"
        )
        if case == "iteration-cancel":
            require(
                process.get("cancel_requested") is True, "Later cancellation was never requested"
            )
            require(
                any(
                    row.get("iteration") == 2
                    and row.get("stage") == "main"
                    and row.get("step_index") == 2
                    for row in process["progress_observations"]
                ),
                "Cancellation lacks actual later-iteration progress synchronization",
            )
        return
    _, stage, policy = case.split("-")
    batches = 2 if policy == "continue" else 1
    summary = read_json(root / "session.json", 65536)
    require(summary.get("schema_version") == 2, "Session iteration schema differs")
    journal = root / "outcomes.jsonl"
    require(journal.stat().st_size < 65536, "Bounded iteration journal exceeds its case scope")
    outcomes = [
        json.loads(line, object_pairs_hook=unique_object)
        for line in journal.read_bytes().splitlines()
    ]
    require(len(outcomes) == batches, "Later failure stop/continue admission differs")
    require(
        summary.get("children_started") == summary.get("children_finalized") == batches
        and summary.get("scheduled_iterations") == {"shared-batches": 3 * batches}
        and summary.get("completed_iterations") == {"shared-batches": 2 * batches}
        and summary.get("iteration_counts")
        == {"shared-batches": expected_counts(6, batches, "failed")}
        and summary.get("arms_complete") == {"shared-batches": False}
        and summary.get("status") == "failed",
        "Session JSON iteration accounting differs from actual native execution",
    )
    first = summary.get("first_failure") or {}
    require(
        first.get("job_id") == outcomes[0].get("job_id"), "Session first failure identity was lost"
    )
    retained = summary.get("retained_job_ids")
    require(
        set(retained or []) == {row["job_id"] for row in outcomes},
        "Later failure artifacts were lost",
    )
    for sequence, outcome in enumerate(outcomes, start=1):
        require(
            outcome.get("schema_version") == 2
            and outcome.get("sequence") == sequence
            and outcome.get("iteration_counts") == expected_counts(3, 1, "failed")
            and outcome.get("iteration_results_source") == "native"
            and outcome.get("failure", {}).get("iteration") == 2
            and outcome.get("failure", {}).get("stage") == stage,
            "Session child iteration accounting/primary failure differs",
        )
        child = contained(root, outcome["artifact_directory"])
        require(
            digest(child / "run.json") == outcome.get("native_report_sha256"),
            "Native child hash differs",
        )
        require(
            digest(child / "request.json") == outcome.get("request_sha256"),
            "Native request hash differs",
        )
        report = check_child(child, bundle, cleanup_failure=stage == "cleanup")
        check_native_iterations(report, "failed")
        require(report.get("failure", {}).get("stage") == stage, "Native phase attribution differs")
        require(
            any(
                step.get("iteration") == 2
                and step.get("stage") == stage
                and step.get("status") == "failed"
                for step in report["steps"]
            ),
            "Native phase failure lacks an executed failed step",
        )
        require(
            outcome.get("actual_exit_code") == report.get("exit_code") != 0,
            "Native/child exit differs",
        )
    try:
        junit = ET.parse(root / "junit.xml").getroot()
    except ET.ParseError as error:
        raise ValueError("Session JUnit XML is malformed") from error
    cases = junit.findall("testcase")
    require(
        len(cases) == batches + 1
        and sum(test.find("failure") is not None for test in cases) == batches
        and sum(test.find("error") is not None for test in cases) == 1,
        "Session JUnit child failure/completion outcomes differ",
    )
    properties = {
        item.get("name"): item.get("value") for item in junit.findall("properties/property")
    }
    for state, count in expected_counts(6, batches, "failed").items():
        require(
            properties.get("iteration.shared-batches." + state) == str(count),
            "Session JUnit iteration property differs: " + state,
        )
    for test in cases:
        if test.get("name") == "session_completion":
            continue
        child_properties = {
            item.get("name"): item.get("value") for item in test.findall("properties/property")
        }
        for state, count in expected_counts(3, 1, "failed").items():
            require(
                child_properties.get("iteration." + state) == str(count),
                "Session child JUnit iteration property differs: " + state,
            )
