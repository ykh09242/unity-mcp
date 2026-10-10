"""Native report and progress proof for Player assurance gates."""

from pathlib import Path
import re

from player_e2e_artifacts import read_json


def require(condition: bool, message: str) -> None:
    """Fail closed with a criterion-specific diagnostic."""
    if not condition:
        raise ValueError(message)


def check_progress(observations: list, request: dict, pid: int) -> None:
    """Initial publication cannot stand in for increasing actual Update snapshots."""
    require(
        isinstance(observations, list) and 1 <= len(observations) <= 256,
        "Missing bounded progress observations",
    )
    previous_sequence = -1
    previous_elapsed = -1
    previous_loop = -1
    previous_heartbeat = -1
    alive = False
    for progress in observations:
        require(isinstance(progress, dict), "Progress must be an object")
        for key in (
            "job_id",
            "definition_hash",
            "build_id",
            "payload_hash",
            "build_source_revision",
        ):
            require(progress.get(key) == request.get(key), "Progress identity mismatch: " + key)
        require(
            progress.get("schema_version") == 1 and progress.get("process_id") == pid,
            "Progress schema/process identity mismatch",
        )
        sequence = progress.get("sequence")
        elapsed = progress.get("elapsed_ms")
        loop = progress.get("main_loop_sequence")
        heartbeat = progress.get("heartbeat_unix_ms")
        require(
            type(sequence) is int and sequence > previous_sequence,
            "Progress sequence did not increase atomically",
        )
        require(
            type(elapsed) is int and elapsed >= previous_elapsed, "Progress elapsed time regressed"
        )
        require(
            type(loop) is int and loop >= 0 and type(heartbeat) is int and heartbeat >= 0,
            "Progress heartbeat counters are invalid",
        )
        require(
            loop >= previous_loop and heartbeat >= previous_heartbeat,
            "Progress loop/heartbeat regressed",
        )
        alive |= loop > 0 and heartbeat > 0
        previous_loop, previous_heartbeat = loop, heartbeat
        previous_sequence, previous_elapsed = sequence, elapsed
    require(alive, "Initial snapshot is not actual main-loop liveness")


def check_report(
    directory: Path, bundle: dict, process: dict, *, cleanup_failure: bool = False
) -> dict:
    """Require identity, terminal state, cleanup and actual native exit agreement."""
    request = read_json(directory / "request.json", 16384)
    report = read_json(directory / "run.json")
    require(
        re.fullmatch(r"[0-9a-f]{32}", str(request.get("job_id"))) is not None,
        "Invalid job identity",
    )
    for key in ("build_id", "payload_hash", "build_source_revision", "definition_hash"):
        require(request.get(key) == bundle.get(key), "Request/build mismatch: " + key)
        require(
            report.get("reproduction", {}).get(key) == bundle.get(key),
            "Report/build mismatch: " + key,
        )
    require(
        report.get("reproduction", {}).get("source_revision") == request.get("source_revision"),
        "Report caller reproduction label differs from request",
    )
    for key in ("job_id", "repeat_count", "timeout_seconds"):
        require(report.get(key) == request.get(key), "Report/request mismatch: " + key)
    require(report.get("scenario") == bundle.get("definition"), "Report frozen scenario mismatch")
    require(
        report.get("execution_environment") == "player"
        and report.get("player_schema_version") == 2,
        "Report is not verified v2 Player evidence",
    )
    require(
        report.get("reproduction", {}).get("payload_verification") == "launcher_admission",
        "Payload verification scope is missing",
    )
    require(
        report.get("finalization_state") == "completed"
        and report.get("report_error") is None
        and report.get("runner_resources_released") is True,
        "Report did not complete/release",
    )
    require(
        process.get("process_ended") is True and process.get("forced_termination") is False,
        "Native process did not finalize naturally",
    )
    require(
        report.get("exit_code") == process.get("actual_exit_code"), "Native exit/report mismatch"
    )
    require(
        (report.get("status") == "succeeded") == (process.get("actual_exit_code") == 0),
        "Native success/exit disagreement",
    )
    started, finished = report.get("started_unix_ms"), report.get("finished_unix_ms")
    require(
        type(started) is int
        and type(finished) is int
        and process["started_unix_ms"] <= started <= finished <= process["finished_unix_ms"],
        "Report lifetime is stale or unfinished",
    )
    steps = report.get("steps", [])
    cleanup = [step for step in steps if step.get("stage") == "cleanup"]
    require(
        cleanup
        and (
            cleanup_failure
            or (
                any(step.get("status") == "passed" for step in cleanup)
                and all(step.get("status") in ("passed", "skipped") for step in cleanup)
            )
        ),
        "Native cleanup body did not pass",
    )
    if report["status"] == "succeeded":
        main = [step for step in steps if step.get("stage") == "main"]
        require(
            main and all(step.get("status") == "passed" for step in main),
            "Native main body did not pass",
        )
        for iteration in range(1, request["repeat_count"] + 1):
            for stage, field in (
                ("setup", "setup_steps"),
                ("main", "steps"),
                ("cleanup", "cleanup_steps"),
            ):
                definition = bundle["definition"].get(field, [])
                observed = [
                    step
                    for step in steps
                    if step.get("stage") == stage and step.get("iteration") == iteration
                ]
                require(len(observed) == len(definition), "Native repeated body step count differs")
                require(
                    [step.get("step_index") for step in observed] == list(range(len(definition))),
                    "Native repeated body step indices differ",
                )
                require(
                    all(step.get("status") == "passed" for step in observed),
                    "Native repeated body did not pass",
                )
                for authored, actual in zip(definition, observed):
                    require(
                        authored.get("action") == actual.get("action")
                        and authored.get("name") == actual.get("name"),
                        "Native repeated body differs from its frozen definition",
                    )
        require(
            any(step.get("action") == "wait_state" for step in main),
            "Readonly state-provider body was not observed",
        )
    return report


def check_preflight(directory: Path, bundle: dict, process: dict) -> None:
    """A parsed wrong-build request receives an attributed, wholly skipped native report."""
    request = read_json(directory / "request.json", 16384)
    report = read_json(directory / "run.json")
    require(request.get("build_id") != bundle.get("build_id"), "Wrong-build request did not differ")
    require(
        report.get("job_id") == request.get("job_id")
        and report.get("scenario") == bundle.get("definition")
        and report.get("repeat_count") == request.get("repeat_count")
        and report.get("timeout_seconds") == request.get("timeout_seconds"),
        "Preflight report attribution differs",
    )
    require(
        report.get("status") == "failed"
        and report.get("exit_code") == 2
        and report.get("failure", {}).get("code") == "player_preflight_failed"
        and report.get("execution_environment") == "player"
        and report.get("player_schema_version") == 2,
        "Wrong-build native preflight failure is missing",
    )
    require(
        report.get("finalization_state") == "completed"
        and report.get("runner_resources_released") is True
        and report.get("report_error") is None
        and report.get("progress_error") is None,
        "Preflight report did not finalize naturally",
    )
    require(
        process["actual_exit_code"] == 2
        and not process["forced_termination"]
        and not process["progress_observations"],
        "Preflight started a main loop or did not exit naturally",
    )
    require(
        process["started_unix_ms"]
        <= report.get("started_unix_ms", 0)
        <= report.get("finished_unix_ms", 0)
        <= process["finished_unix_ms"],
        "Preflight report lifetime is stale",
    )
    for key in (
        "build_id",
        "build_source_revision",
        "definition_hash",
        "payload_hash",
        "unity_version",
        "package_version",
    ):
        require(
            report.get("reproduction", {}).get(key) == bundle.get(key),
            "Preflight embedded identity mismatch: " + key,
        )
    require(
        report.get("reproduction", {}).get("source_revision") == request.get("source_revision"),
        "Preflight caller label differs",
    )
    steps = report.get("steps")
    expected_steps = (
        sum(
            len(bundle["definition"].get(field, []))
            for field in ("setup_steps", "steps", "cleanup_steps")
        )
        * request["repeat_count"]
    )
    require(
        isinstance(steps, list)
        and len(steps) == expected_steps
        and all(step.get("status") == "skipped" for step in steps),
        "Wrong-build preflight executed scenario steps",
    )
