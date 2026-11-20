"""Assess retained Player outcomes against a reviewed pair without starting a process."""

from hashlib import sha256

from cli.utils.play_scenario_player import PROCESS_LOG_LIMIT, REPORT_LIMIT
from services.verification.input_chunks import SourceSample, decode_object
from services.verification.player_runner import _state_result


def evaluate_native(outcome, output, pair, side, job, *, expectation, save_upgrade):
    native = outcome.directory
    if native != output / job:
        raise ValueError("reproduction_native_directory_mismatch")
    sample = SourceSample()
    files = {}
    records = {}
    for filename, limit in (
        ("run.json", REPORT_LIMIT),
        ("process.json", 65536),
        ("diagnostics.json", 65536),
        ("player.log", PROCESS_LOG_LIMIT),
    ):
        content = sample.read(native / filename, limit)
        files[filename] = sha256(content).hexdigest()
        if filename.endswith(".json"):
            records[filename] = decode_object(content)
    raw, process, diagnostics = (
        records["run.json"],
        records["process.json"],
        records["diagnostics.json"],
    )
    evidence = raw.get("reproduction", {})
    if (
        raw != outcome.report
        or diagnostics != outcome.diagnostics
        or any(row.get("job_id") != job for row in (raw, process, diagnostics))
        or raw.get("execution_environment") != "player"
        or raw.get("finalization_state") != "completed"
        or raw.get("runner_resources_released") is not True
        or raw.get("report_error")
        or raw.get("progress_error")
        or diagnostics.get("cleanup_state") != "native_finalized"
        or diagnostics.get("payload_verification") != "launcher_admission"
        or diagnostics.get("deadline_reached") is not False
        or diagnostics.get("process_id") != process.get("process_id")
        or process.get("process_ended") is not True
        or process.get("forced_termination") is not False
        or process.get("actual_exit_code") != raw.get("exit_code")
        or diagnostics.get("process_exit_code") != raw.get("exit_code")
        or outcome.client_error
        or outcome.exit_code != raw.get("exit_code")
        or evidence.get("definition_hash") != pair.definition_hash
        or evidence.get("payload_hash") != side.payload_hash
        or evidence.get("build_source_revision") != side.source_revision
        or evidence.get("build_id") != side.build_id
    ):
        raise ValueError("reproduction_native_identity_or_finalization_mismatch")
    rows = raw.get("steps")
    if (
        not isinstance(rows, list)
        or not rows
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("stage"), str)
            or not isinstance(row.get("status"), str)
            for row in rows
        )
    ):
        raise ValueError("reproduction_native_steps_unavailable")
    steps = [row for row in rows if row["stage"] == "main"]
    checkpoints = [
        index
        for index, row in enumerate(steps)
        if row.get("name") == expectation.checkpoint and row.get("action") == expectation.action
    ]
    try:
        state, observed = _state_result(native, expectation.state)
    except (OSError, ValueError) as error:
        if not save_upgrade:
            raise
        state, observed = "incomplete", {"reason": type(error).__name__}
    sample.finish()
    if state == "incomplete" and not save_upgrade:
        raise ValueError("reproduction_state_observation_incomplete: " + observed["reason"])
    expected_exit = 0 if expectation.status == "succeeded" else 1
    expected_step = "passed" if expectation.status == "succeeded" else expectation.status
    checkpoint_index = checkpoints[0] if len(checkpoints) == 1 else None
    checkpoint_status = None if checkpoint_index is None else steps[checkpoint_index].get("status")
    checkpoint_reached = (
        checkpoint_status in {"passed", "failed", "timed_out"}
        and all(row.get("status") == "passed" for row in steps[:checkpoint_index])
        and all(
            row.get("status") in {"passed", "skipped"} for row in rows if row["stage"] == "setup"
        )
    )
    checkpoint_matched = (
        raw.get("status") == expectation.status
        and raw.get("exit_code") == expected_exit
        and checkpoint_reached
        and checkpoint_status == expected_step
        and all(
            row.get("status") in {"passed", "skipped"}
            for row in rows
            if row is not steps[checkpoint_index]
        )
    )
    return {
        "matched": checkpoint_matched and state == "passed",
        "job_id": job,
        "status": raw.get("status"),
        "exit_code": raw.get("exit_code"),
        "state": observed,
        "state_status": state,
        "checkpoint_reached": checkpoint_reached,
        "checkpoint_status": checkpoint_status,
        "log_marker": expectation.state.log_marker,
        "files": files,
        "artifacts": f"{output.name}/{job}",
        "checkpoint": expectation.checkpoint,
        "action": expectation.action,
        "steps": [
            {key: row.get(key) for key in ("stage", "name", "action", "status")} for row in rows
        ],
        "definition_hash": pair.definition_hash,
        "build_id": side.build_id,
        "payload_hash": side.payload_hash,
        "source_revision": side.source_revision,
        "environment": evidence.get("environment"),
        "launcher_host": process.get("launch_environment"),
    }


def _observed_upgrade_failure(runs):
    """Require both opened-run oracles before attributing a failure to the save."""
    candidates = (runs["candidate"], runs["restart"])
    return all(
        row["checkpoint_reached"] and row["state_status"] in {"passed", "failed"}
        for row in candidates
    ) and any(
        row["state_status"] == "failed" or row["save"]["status"] != "present" for row in candidates
    )


def pair_verdict(runs, *, save_upgrade):
    """Return the schema-v1 report fields and CLI exit code for the completed phases."""
    if all(row["matched"] for row in runs.values()):
        return {"status": "save_preserved" if save_upgrade else "reproduced"}, 0
    if not save_upgrade:
        return {"status": "expectation_mismatch"}, 1
    if _observed_upgrade_failure(runs):
        return {"status": "migration_failed"}, 1
    return {"status": "unavailable", "reason": "save_upgrade_migration_evidence_incomplete"}, 2
