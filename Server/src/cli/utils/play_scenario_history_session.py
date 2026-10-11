"""Reconcile complete session parents with every bounded durable journal outcome."""

import json
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, Field

from cli.utils.play_scenario_analysis_io import AnalysisError, read_bytes
from cli.utils.play_scenario_history_models import HistoryEvidence, HistoryOutcome
from cli.utils.play_scenario_iterations import STATE_KEYS, zero_iteration_counts
from cli.utils.play_scenario_player_session import OUTCOME_FILE_LIMIT, OUTCOME_LIMIT, SUMMARY_LIMIT
from cli.utils.play_scenario_query_compare import (
    _check_depth,
    _finite_float,
    _object,
    _reject_constant,
)
from cli.utils.play_scenario_session_recovery import (
    MAX_RECORDS,
    _Admission,
    _Header,
    _Journal,
    _Outcome,
    _admission_prefix,
    _outcome_prefix,
)
from pydantic import JsonValue

Model = TypeVar("Model", bound=BaseModel)
Total = Annotated[int, Field(strict=True, ge=0, le=10000)]


class _SessionFinal(_Header):
    status: Literal["succeeded", "failed", "cancelled", "timed_out", "interrupted", "running"]
    finished_unix_ms: Annotated[int, Field(strict=True, ge=0)] | None
    iteration_counts: dict[str, dict[str, Total]]
    scheduled_iterations: dict[str, Total]
    completed_iterations: dict[str, Total]
    children_started: Total
    children_finalized: Total
    outcomes_recorded: Total
    first_failure: _Outcome | None
    session_error: str | None
    active_child: _Admission | None


def _decode(encoded: bytes) -> dict[str, JsonValue]:
    """Use the native offline parser's finite-number, duplicate-key and depth contracts."""
    text = encoded.decode("utf-8")
    _check_depth(text)
    value = json.loads(
        text, object_pairs_hook=_object, parse_float=_finite_float, parse_constant=_reject_constant
    )
    if not isinstance(value, dict):
        raise AnalysisError("History evidence must contain JSON objects")
    return value


def _read_journal(path: Path, model: type[Model]) -> _Journal[Model]:
    encoded = read_bytes(path, OUTCOME_FILE_LIMIT)
    lines = encoded.splitlines(keepends=True)
    if len(lines) > MAX_RECORDS or any(
        len(line) > OUTCOME_LIMIT or not line.endswith(b"\n") for line in lines
    ):
        raise AnalysisError("History journal is incomplete or exceeds its row bound")
    rows = []
    for line in lines:
        raw = _decode(line)
        if "artifact_directory" in raw and raw["artifact_directory"] not in {
            None,
            "runs/" + str(raw.get("job_id")),
        }:
            raise AnalysisError("Session artifact path leaves its owned child directory")
        rows.append(model.model_validate(raw))
    return _Journal(tuple(rows), None)


def read_session_history(source: Path) -> tuple[HistoryEvidence, ...]:
    """Read complete journals only; retained child samples never establish the whole plan."""
    directory = source.parent
    parent = _SessionFinal.model_validate(_decode(read_bytes(source, SUMMARY_LIMIT)))
    header = _Header.model_validate(
        _decode(read_bytes(directory / "session-start.json", SUMMARY_LIMIT))
    )
    if parent.schema_version != 2 or parent.model_dump(include=set(_Header.model_fields)) != (
        header.model_dump()
    ):
        raise AnalysisError("Session parent disagrees with its immutable schema-two plan")
    admissions, issue = _admission_prefix(
        header, _read_journal(directory / "admissions.jsonl", _Admission)
    )
    if issue:
        raise AnalysisError(issue)
    outcomes, issue = _outcome_prefix(
        header, _read_journal(directory / "outcomes.jsonl", _Outcome), admissions
    )
    if issue or len(outcomes) != len(admissions):
        raise AnalysisError(issue or "Session has admitted children without durable outcomes")
    totals = {mode: zero_iteration_counts() for mode in header.modes}
    scheduled = dict.fromkeys(header.modes, 0)
    previous_finish = header.started_unix_ms
    for row in outcomes:
        if row.iteration_counts is None:
            raise AnalysisError("Schema-two history lacks iteration counters")
        if row.process_started_unix_ms is not None and (
            row.process_started_unix_ms < previous_finish
        ):
            raise AnalysisError("Session process intervals overlap or regress")
        if row.process_finished_unix_ms is not None:
            previous_finish = row.process_finished_unix_ms
        for key, value in row.iteration_counts.model_dump().items():
            totals[row.mode][key] += value
        scheduled[row.mode] += row.repeat_count
    for mode, counts in totals.items():
        counts["skipped"] += header.requested_iterations - scheduled[mode]
        counts["planned"] = header.requested_iterations
    first = next((row for row in outcomes if row.exit_code != 0), None)
    if (
        parent.iteration_counts != totals
        or parent.scheduled_iterations != scheduled
        or parent.completed_iterations
        != {mode: counts["executed"] for mode, counts in totals.items()}
        or parent.outcomes_recorded != len(outcomes)
        or parent.children_started != sum(row.process_id is not None for row in outcomes)
        or parent.children_finalized != sum(row.process_ended for row in outcomes)
        or parent.first_failure != first
        or parent.active_child is not None
        or parent.finished_unix_ms is None
        or parent.finished_unix_ms < previous_finish
        or parent.status == "running"
        or (
            parent.status == "succeeded" and (first is not None or parent.session_error is not None)
        )
        or (
            parent.status == "succeeded"
            and any(counts["passed"] != header.requested_iterations for counts in totals.values())
        )
    ):
        raise AnalysisError("Session final parent disagrees with its durable journal")
    partitions: dict[str, list[HistoryOutcome]] = {}
    compatibility = {}
    positions = dict.fromkeys(header.modes, 0)
    for row in outcomes:
        counts = row.iteration_counts.model_dump()
        start = positions[row.mode] + 1
        positions[row.mode] += row.repeat_count
        attribution = {
            "execution_environment": "player",
            "definition_hash": row.definition_hash,
            "build_id": row.build_id,
            "build_source_revision": row.build_source_revision,
            "payload_hash": row.payload_hash,
            "environment": None,
            "mode": row.mode,
        }
        key = json.dumps(attribution, sort_keys=True)
        compatibility[key] = attribution
        partitions.setdefault(key, []).append(
            HistoryOutcome(
                job_id=row.job_id,
                sequence=row.sequence,
                first_iteration=start,
                last_iteration=positions[row.mode],
                status=row.status,
                counts=counts,
                iteration_results_source=row.iteration_results_source,
                first_failure_iteration=start + row.failure.iteration - 1
                if row.failure is not None and row.failure.iteration
                else (
                    start
                    if row.repeat_count == 1 and counts["failed"] + counts["timed_out"]
                    else None
                ),
                failure_code=row.failure.code if row.failure else None,
                failure=row.failure.model_dump(mode="json") if row.failure else None,
                client_error=row.client_error,
                started_unix_ms=row.started_unix_ms,
                finished_unix_ms=row.finished_unix_ms,
            )
        )
    for mode in header.modes:
        if not any(value["mode"] == mode for value in compatibility.values()):
            attribution = {"mode": mode, "execution_environment": "player", "environment": None}
            key = json.dumps(attribution, sort_keys=True)
            compatibility[key] = attribution
            partitions[key] = []
        elif (
            scheduled[mode] < header.requested_iterations
            and sum(value["mode"] == mode for value in compatibility.values()) > 1
        ):
            attribution = {
                "mode": mode,
                "execution_environment": "player",
                "environment": None,
                "unadmitted_plan": True,
            }
            key = json.dumps(attribution, sort_keys=True)
            compatibility[key] = attribution
            partitions[key] = []
    result = []
    for key, rows in partitions.items():
        counts = zero_iteration_counts()
        for row in rows:
            for state, value in row.counts.items():
                counts[state] += value
        # Unscheduled slots belong to a mode only when that mode has one attribution partition.
        mode = compatibility[key]["mode"]
        if compatibility[key].get("unadmitted_plan"):
            counts["planned"] = counts["skipped"] = header.requested_iterations - scheduled[mode]
        elif sum(value["mode"] == mode for value in compatibility.values()) == 1:
            counts = totals[mode]
        complete = (
            parent.session_error is None
            and parent.status != "interrupted"
            and all(
                row.process_ended and row.iteration_results_source == "native" for row in outcomes
            )
            and not any(counts[state] for state in ("unknown", "running", "pending"))
        )
        result.append(
            HistoryEvidence(
                source,
                header.session_id,
                "session",
                mode,
                parent.status,
                compatibility[key],
                False,
                complete,
                counts,
                tuple(rows),
                (
                    "Session journal lacks per-child environment; no cross-source compatibility is inferred.",
                ),
            )
        )
    return tuple(result)
