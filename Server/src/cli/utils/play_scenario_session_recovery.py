# noqa: SIZE_OK - One recovery boundary keeps strict models and journal reconciliation together.
"""Recover a bounded session evidence snapshot without touching its original artifacts."""

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import time
from typing import Annotated, Final, Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from cli.utils.play_scenario_iterations import (
    ATTEMPTED_KEYS,
    STATE_KEYS,
    add_iteration_counts,
    zero_iteration_counts,
)
from cli.utils.play_scenario_player import PlayerLaunchError
from cli.utils.play_scenario_player_session import (
    OUTCOME_FILE_LIMIT,
    OUTCOME_LIMIT,
    SUMMARY_LIMIT,
    _append_record,
    _summary_file,
    _write_session_junit,
)

Mode = Literal["shared-batches", "fresh-process"]
Count = Annotated[int, Field(strict=True, ge=0, le=10)]
Identity = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$")]
Stamp = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
BoundedText = Annotated[str, Field(strict=True, max_length=2048)]
MAX_RECORDS: Final = 2000
Model = TypeVar("Model", bound=BaseModel)


class _Header(BaseModel):
    """Immutable session identity and requested plan, independent of replaceable status."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=2)]
    session_id: Identity
    mode: Literal["shared-batches", "fresh-process", "compare"]
    requested_iterations: Annotated[int, Field(strict=True, ge=1, le=1000)]
    iterations_per_process: dict[Mode, Annotated[int, Field(strict=True, ge=1, le=10)]]
    started_unix_ms: Stamp
    source_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    expected_build_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    expected_build_id: Identity | None = None

    @property
    def modes(self) -> tuple[Mode, ...]:
        return ("shared-batches", "fresh-process") if self.mode == "compare" else (self.mode,)

    @model_validator(mode="after")
    def valid_plan(self) -> "_Header":
        if set(self.iterations_per_process) != set(self.modes):
            raise PlayerLaunchError("Recovery header modes do not match the session plan")
        if self.iterations_per_process.get("fresh-process", 1) != 1:
            raise PlayerLaunchError("Fresh-process recovery batches must contain one iteration")
        return self


class _Admission(BaseModel):
    """One durable admission with no inferred child finalization."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    session_id: Identity
    sequence: Annotated[int, Field(strict=True, ge=1, le=MAX_RECORDS)]
    mode: Mode
    job_id: Identity
    repeat_count: Annotated[int, Field(strict=True, ge=1, le=10)]


class _Counts(BaseModel):
    """A complete strict state partition retained in schema-two outcome rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    planned: Count
    executed: Count
    pending: Count
    running: Count
    passed: Count
    failed: Count
    timed_out: Count
    cancelled: Count
    skipped: Count
    unknown: Count

    @model_validator(mode="after")
    def partition(self) -> "_Counts":
        if self.planned != sum(getattr(self, key) for key in STATE_KEYS):
            raise PlayerLaunchError("Recovery outcome state counts do not partition its plan")
        if self.executed != sum(getattr(self, key) for key in ATTEMPTED_KEYS):
            raise PlayerLaunchError("Recovery outcome executed count lacks attempted states")
        return self


class _Failure(BaseModel):
    """Keep only bounded scalar failure context produced by the session writer."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    code: Annotated[str, Field(strict=True, max_length=128, pattern=r"\S")]
    message: Annotated[str, Field(strict=True, max_length=512)] | None = None
    stage: Literal["setup", "main", "cleanup"] | None = None
    iteration: Annotated[int, Field(strict=True, ge=0, le=10)] | None = None
    step_index: Annotated[int, Field(strict=True, ge=-1, le=63)] | None = None


class _Outcome(BaseModel):
    """Read only compact outcome evidence, never load or follow a native artifact."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=2)] = 1
    session_id: Identity | None = None
    sequence: Annotated[int, Field(strict=True, ge=1, le=MAX_RECORDS)] | None = None
    mode: Mode
    process_scope: Literal["batch"] = "batch"
    job_id: Identity
    repeat_count: Annotated[int, Field(strict=True, ge=1, le=10)]
    status: Literal["succeeded", "failed", "timed_out", "cancelled", "infrastructure_error"]
    exit_code: Annotated[int, Field(strict=True, ge=0, le=130)]
    native_report_available: Annotated[bool, Field(strict=True)] = False
    native_report_sha256: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")] | None = (
        None
    )
    started_unix_ms: Stamp | None = None
    finished_unix_ms: Stamp | None = None
    client_error: BoundedText | None = None
    process_id: Annotated[int, Field(strict=True, ge=1, le=2**32 - 1)] | None = None
    actual_exit_code: Annotated[int, Field(strict=True, ge=-(2**31), le=2**32 - 1)] | None = None
    process_ended: Annotated[bool, Field(strict=True)] = False
    forced_termination: Annotated[bool, Field(strict=True)] = False
    process_started_unix_ms: Stamp | None = None
    process_finished_unix_ms: Stamp | None = None
    request_sha256: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")] | None = None
    iteration_counts: _Counts | None = None
    iteration_results_source: Literal["native", "legacy_steps", "unavailable"] = "unavailable"
    failure: _Failure | None = None
    definition_hash: Annotated[str, Field(strict=True, max_length=64)] | None = None
    build_id: Identity | None = None
    build_source_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    payload_hash: Annotated[str, Field(strict=True, max_length=64)] | None = None
    payload_verification: Annotated[str, Field(strict=True, max_length=128)] | None = None

    @model_validator(mode="after")
    def valid_evidence(self) -> "_Outcome":
        if self.schema_version == 2 and (
            self.session_id is None or self.sequence is None or self.iteration_counts is None
        ):
            raise PlayerLaunchError("Recovery schema-two outcome lacks identity or counters")
        if self.iteration_counts is not None and self.iteration_counts.planned != self.repeat_count:
            raise PlayerLaunchError("Recovery outcome counters do not match admitted repeats")
        for start, finish in (
            (self.started_unix_ms, self.finished_unix_ms),
            (self.process_started_unix_ms, self.process_finished_unix_ms),
        ):
            if start is not None and finish is not None and finish < start:
                raise PlayerLaunchError("Recovery outcome timestamps run backwards")
        if self.process_ended != (self.actual_exit_code is not None):
            raise PlayerLaunchError("Recovery process exit observation and finalization disagree")
        if self.process_ended and (self.process_id is None or self.actual_exit_code is None):
            raise PlayerLaunchError("Recovery process finalization lacks exit evidence")
        if (
            self.schema_version == 2
            and self.status == "succeeded"
            and self.iteration_counts is not None
            and self.iteration_counts.passed != self.repeat_count
        ):
            raise PlayerLaunchError("Recovery successful outcome lacks passing iteration evidence")
        if (
            self.schema_version == 2
            and self.status == "succeeded"
            and not self.native_report_available
        ):
            raise PlayerLaunchError("Recovery successful outcome lacks native report evidence")
        if (
            self.iteration_results_source == "native"
            and self.iteration_counts is not None
            and (
                self.iteration_counts.pending
                or self.iteration_counts.running
                or self.iteration_counts.unknown
            )
        ):
            raise PlayerLaunchError(
                "Recovery native outcome contains unfinished iteration evidence"
            )
        if self.iteration_counts is not None:
            if (
                self.iteration_results_source == "unavailable"
                and self.iteration_counts.unknown != self.repeat_count
            ):
                raise PlayerLaunchError(
                    "Recovery unavailable outcome cannot claim known iterations"
                )
            if self.iteration_results_source in {"native", "legacy_steps"} and (
                not self.native_report_available or self.native_report_sha256 is None
            ):
                raise PlayerLaunchError(
                    "Recovery iteration evidence lacks retained native report identity"
                )
        if (
            self.failure is not None
            and self.failure.iteration is not None
            and self.failure.iteration > self.repeat_count
        ):
            raise PlayerLaunchError("Recovery failure iteration exceeds admitted repeats")
        if self.native_report_available and self.native_report_sha256 is None:
            raise PlayerLaunchError("Recovery available native report lacks its retained SHA256")
        if self.status != "succeeded" and self.exit_code == 0:
            raise PlayerLaunchError("Recovery failed outcome claims a successful exit")
        return self


@dataclass(frozen=True, slots=True)
class _Journal(Generic[Model]):
    """A bounded validated prefix, with an explicit reason its tail is untrusted."""

    rows: tuple[Model, ...]
    issue: str | None


def _safe_path(path: Path) -> Path:
    """Reject protected data paths and linked/reparse ancestors before any read or write."""
    absolute = Path(os.path.abspath(path))
    parts = [part.casefold() for part in absolute.parts]
    if any(
        parts[index : index + 3] == ["assets", "resources", "gamedata"]
        for index in range(len(parts) - 2)
    ):
        raise PlayerLaunchError("Recovery cannot access protected GameData paths")
    for ancestor in (absolute, *absolute.parents):
        try:
            attributes = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(attributes.st_mode) or (
            getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise PlayerLaunchError("Recovery paths cannot contain links or reparse points")
    return absolute


def _json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    """Reject ambiguous JSON objects before interpreting journal or header identities."""
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise PlayerLaunchError("Recovery JSON contains duplicate object keys")
        result[key] = value
    return result


def _header(source: Path) -> _Header:
    path = _safe_path(source / "session-start.json")
    immutable = path.exists()
    if not immutable:
        path = _safe_path(source / "session.json")
    if not path.is_file() or path.stat().st_size > SUMMARY_LIMIT:
        raise PlayerLaunchError("Recovery session header is missing or exceeds its byte bound")
    try:
        with path.open("rb") as stream:
            encoded = stream.read(SUMMARY_LIMIT + 1)
        if len(encoded) > SUMMARY_LIMIT:
            raise PlayerLaunchError("Recovery session header exceeds its byte bound")
        header = _Header.model_validate(json.loads(encoded, object_pairs_hook=_json_object))
    except (ValidationError, ValueError) as exc:
        raise PlayerLaunchError("Recovery session header is invalid") from exc
    if not immutable and header.schema_version != 1:
        raise PlayerLaunchError("Recovery schema-two session requires its immutable header")
    return header


def _journal(source: Path, name: str, model: type[Model]) -> _Journal[Model]:
    path = _safe_path(source / (name + ".jsonl"))
    if not path.exists():
        return _Journal((), name + "_missing")
    if not path.is_file():
        raise PlayerLaunchError("Recovery journals must be regular files")
    rows: list[Model] = []
    issue = None
    with path.open("rb") as stream:
        for _index in range(MAX_RECORDS):
            encoded = stream.readline(OUTCOME_LIMIT + 1)
            if not encoded:
                break
            if len(encoded) > OUTCOME_LIMIT or stream.tell() > OUTCOME_FILE_LIMIT:
                issue = name + "_oversized"
                break
            if not encoded.endswith(b"\n"):
                issue = name + "_torn"
                break
            try:
                rows.append(
                    model.model_validate(json.loads(encoded, object_pairs_hook=_json_object))
                )
            except (ValidationError, ValueError, PlayerLaunchError):
                issue = name + "_invalid"
                break
        else:
            if stream.read(1):
                issue = name + "_row_limit"
    return _Journal(tuple(rows), issue)


def _admission_prefix(
    header: _Header, journal: _Journal[_Admission]
) -> tuple[list[_Admission], str | None]:
    rows: list[_Admission] = []
    ids: set[str] = set()
    scheduled = {mode: 0 for mode in header.modes}
    for entry in journal.rows:
        if entry.job_id in ids or entry.sequence != len(rows) + 1:
            return rows, "admissions_duplicate_or_sequence"
        if entry.session_id != header.session_id or entry.mode not in header.modes:
            return rows, "admissions_identity_mismatch"
        expected_mode = next(
            (mode for mode in header.modes if scheduled[mode] < header.requested_iterations), None
        )
        expected_repeats = min(
            header.iterations_per_process[entry.mode],
            header.requested_iterations - scheduled[entry.mode],
        )
        if entry.mode != expected_mode or entry.repeat_count != expected_repeats:
            return rows, "admissions_plan_mismatch"
        rows.append(entry)
        ids.add(entry.job_id)
        scheduled[entry.mode] += entry.repeat_count
    return rows, journal.issue


@dataclass(frozen=True, slots=True)
class _Projection:
    """Normalized output and its counters share a bounded mutable aggregation dictionary."""

    record: dict[str, JsonValue]
    counts: dict[str, int]


def _project_outcome(header: _Header, row: _Outcome, sequence: int) -> _Projection:
    """Normalize original and historical outcome identity without following native artifacts."""
    record = row.model_dump(mode="json")
    record.update(schema_version=2, session_id=header.session_id, sequence=sequence)
    record["artifact_directory"] = None
    if row.failure is not None:
        record["failure"] = row.failure.model_dump(mode="json", exclude_none=True)
    counts = (
        row.iteration_counts.model_dump()
        if header.schema_version == 2 and row.iteration_counts is not None
        else zero_iteration_counts()
    )
    if header.schema_version == 1:
        counts["planned"] = counts["unknown"] = row.repeat_count
        record["iteration_results_source"] = "unavailable"
        if row.status == "succeeded":
            record["original_status"] = row.status
            record["status"] = "infrastructure_error"
            record["exit_code"] = 1
            record["client_error"] = row.client_error or "recovery_legacy_iteration_unknown"
    record["iteration_counts"] = counts
    return _Projection(record, counts)


def _outcome_prefix(
    header: _Header, journal: _Journal[_Outcome], admissions: list[_Admission]
) -> tuple[list[_Outcome], str | None]:
    rows: list[_Outcome] = []
    ids: set[str] = set()
    scheduled = {mode: 0 for mode in header.modes}
    for entry in journal.rows:
        sequence = len(rows) + 1
        if entry.job_id in ids or (entry.sequence is not None and entry.sequence != sequence):
            return rows, "outcomes_duplicate_or_sequence"
        if entry.mode not in header.modes or (
            entry.session_id is not None and entry.session_id != header.session_id
        ):
            return rows, "outcomes_identity_mismatch"
        if header.schema_version == 2:
            if entry.schema_version != 2 or sequence > len(admissions):
                return rows, "outcomes_admission_mismatch"
            admission = admissions[sequence - 1]
            if (entry.job_id, entry.mode, entry.repeat_count) != (
                admission.job_id,
                admission.mode,
                admission.repeat_count,
            ):
                return rows, "outcomes_admission_mismatch"
        else:
            expected_repeats = min(
                header.iterations_per_process[entry.mode],
                header.requested_iterations - scheduled[entry.mode],
            )
            if entry.repeat_count != expected_repeats:
                return rows, "outcomes_plan_mismatch"
        projected = _project_outcome(header, entry, sequence)
        if (
            len(
                json.dumps(projected.record, ensure_ascii=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            + 1
            > OUTCOME_LIMIT
        ):
            return rows, "outcomes_projection_oversized"
        rows.append(entry)
        ids.add(entry.job_id)
        scheduled[entry.mode] += entry.repeat_count
    return rows, journal.issue


def recover_player_session(
    source_directory: Path, output_directory: Path
) -> tuple[dict[str, JsonValue], Path, int]:
    """Write a separate incomplete snapshot from bounded validated journal prefixes."""
    source, output = _safe_path(source_directory), _safe_path(output_directory)
    if not source.is_dir():
        raise PlayerLaunchError("Recovery source must be a regular session directory")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise PlayerLaunchError("Recovery output must be separate from source and its ancestors")
    header = _header(source)
    admission_journal = _journal(source, "admissions", _Admission)
    outcome_journal = _journal(source, "outcomes", _Outcome)
    admissions, admission_issue = _admission_prefix(header, admission_journal)
    outcomes, outcome_issue = _outcome_prefix(header, outcome_journal, admissions)
    for admission in admissions[len(outcomes) :]:
        counts = zero_iteration_counts()
        counts["planned"] = counts["unknown"] = admission.repeat_count
        outcomes.append(
            _Outcome(
                schema_version=2,
                session_id=header.session_id,
                sequence=admission.sequence,
                mode=admission.mode,
                job_id=admission.job_id,
                repeat_count=admission.repeat_count,
                status="infrastructure_error",
                exit_code=1,
                client_error="recovery_outcome_unavailable",
                iteration_counts=_Counts.model_validate(counts),
            )
        )
    issues = [issue for issue in (admission_issue, outcome_issue) if issue]
    totals = {mode: zero_iteration_counts() for mode in header.modes}
    scheduled = {mode: 0 for mode in header.modes}
    for admission in admissions:
        scheduled[admission.mode] += admission.repeat_count
    if header.schema_version == 1:
        scheduled = {
            mode: sum(row.repeat_count for row in outcomes if row.mode == mode)
            for mode in header.modes
        }
    for mode, total in totals.items():
        total["planned"] = header.requested_iterations
        total["unknown"] = scheduled[mode]
        remaining = header.requested_iterations - scheduled[mode]
        total[
            "skipped" if admission_issue is None and header.schema_version == 2 else "unknown"
        ] += remaining
    recovery_id = uuid4().hex
    output.mkdir(parents=True, exist_ok=True)
    directory = output / recovery_id
    directory.mkdir(exist_ok=False)
    recent: deque[dict[str, JsonValue]] = deque(maxlen=32)
    failures = {"failures": 0, "errors": 0}
    first_failure = None
    started = finalized = 0
    with (directory / "outcomes.jsonl").open("xb") as stream:
        for sequence, row in enumerate(outcomes, 1):
            projection = _project_outcome(header, row, sequence)
            record, counts = projection.record, projection.counts
            total = totals[row.mode]
            total["unknown"] -= row.repeat_count
            counts["planned"] = 0
            totals[row.mode] = add_iteration_counts(total, counts)
            counts["planned"] = row.repeat_count
            started += int(row.process_id is not None)
            finalized += int(row.process_ended)
            if record["exit_code"] != 0:
                failures[
                    "failures"
                    if record["status"] == "failed" and not record["client_error"]
                    else "errors"
                ] += 1
                if first_failure is None:
                    first_failure = dict(record)
            _append_record(stream, record)
            recent.append(record)
            while len(json.dumps(list(recent), ensure_ascii=True).encode("utf-8")) > 49152:
                recent.popleft()
    with (directory / "admissions.jsonl").open("xb") as stream:
        for row in admissions:
            _append_record(stream, row.model_dump(mode="json"))
    summary: dict[str, JsonValue] = {
        **header.model_dump(mode="json"),
        "schema_version": 2,
        "status": "interrupted",
        "process_scope": "batch",
        "recovered": True,
        "recovery_id": recovery_id,
        "recovery_issues": issues,
        "recovery_admissions_complete": admission_issue is None and header.schema_version == 2,
        "session_error": "session_recovered_incomplete",
        "iteration_counts": totals,
        "completed_iterations": {mode: total["executed"] for mode, total in totals.items()},
        "scheduled_iterations": scheduled,
        "children_started": started,
        "children_finalized": finalized,
        "outcomes_recorded": len(outcomes),
        "first_failure": first_failure,
        "active_child": None,
        "arms_complete": {mode: False for mode in header.modes},
        "finished_unix_ms": max(header.started_unix_ms, int(time.time() * 1000)),
        "last_outcomes": list(recent),
        "retained_job_ids": [],
        "outcome_counts": failures,
    }
    _summary_file(summary, directory, "session-start.json")
    _summary_file(summary, directory)
    _write_session_junit(summary, directory)
    return summary, directory, 1
