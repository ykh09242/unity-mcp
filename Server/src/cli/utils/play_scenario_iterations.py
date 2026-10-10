"""Parse native repetition ledgers and conservatively infer legacy iteration accounting."""

from typing import Annotated, Final, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

IterationStatus = Literal[
    "pending", "running", "passed", "failed", "timed_out", "cancelled", "skipped"
]
Timestamp = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
STATE_KEYS: Final = (
    "pending",
    "running",
    "passed",
    "failed",
    "timed_out",
    "cancelled",
    "skipped",
    "unknown",
)
ATTEMPTED_KEYS: Final = ("running", "passed", "failed", "timed_out", "cancelled")
TERMINAL_RUN_STATES: Final = frozenset({"succeeded", "failed", "timed_out", "cancelled"})


class IterationResultsError(ValueError):
    """A report claims an iteration ledger that cannot establish truthful accounting."""


class IterationResult(BaseModel):
    """One strictly parsed native repetition with state-specific timestamp evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    iteration: Annotated[int, Field(strict=True, ge=1, le=10)]
    status: IterationStatus
    started_unix_ms: Timestamp | None
    finished_unix_ms: Timestamp | None

    @model_validator(mode="after")
    def valid_timestamps(self) -> "IterationResult":
        """Reject untouched, active and completed states with contradictory timestamps."""
        start, finish = self.started_unix_ms, self.finished_unix_ms
        match self.status:
            case "pending":
                valid = start is None and finish is None
            case "skipped":
                valid = start is None
            case "running":
                valid = start is not None and finish is None
            case "passed" | "failed" | "timed_out" | "cancelled":
                valid = start is not None and finish is not None and finish >= start
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise IterationResultsError("Iteration status and timestamps disagree")
        return self


class _NativeLedger(BaseModel):
    """Parse only the versioned ledger fields without narrowing the original report."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    status: Annotated[str, Field(strict=True)] | None = None
    iteration_results_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    iteration_results: list[IterationResult] = Field(strict=True, min_length=1, max_length=10)


def validate_iteration_results(
    report: dict[str, JsonValue], repeat_count: int, *, terminal: bool = False
) -> tuple[IterationResult, ...] | None:
    """Return a native ledger, or None only when both additive fields are absent.

    JSON dictionaries are the shared report boundary; parsed rows are immutable models.
    Explicit terminal admission also rejects active rows even if run status is missing.
    """
    if type(repeat_count) is not int or not 1 <= repeat_count <= 10:
        raise IterationResultsError("Iteration repeat_count must be an integer between 1 and 10")
    fields = {"iteration_results_version", "iteration_results"} & report.keys()
    if not fields:
        return None
    if len(fields) != 2:
        raise IterationResultsError("Iteration ledger requires both version and results")
    try:
        ledger = _NativeLedger.model_validate(report)
        rows = tuple(ledger.iteration_results)
    except ValidationError as exc:
        raise IterationResultsError("Iteration ledger fields are invalid") from exc
    if tuple(row.iteration for row in rows) != tuple(range(1, repeat_count + 1)):
        raise IterationResultsError("Iteration ledger must contain each planned ID in order")
    is_terminal = terminal or ledger.status in TERMINAL_RUN_STATES
    if is_terminal and any(row.status in {"pending", "running"} for row in rows):
        raise IterationResultsError("Terminal iteration ledger contains unfinished rows")
    if report.get("status") == "succeeded" and any(row.status != "passed" for row in rows):
        raise IterationResultsError("Successful report contains non-passed iterations")
    start, finish = report.get("started_unix_ms"), report.get("finished_unix_ms")
    previous_finish = None
    for row in rows:
        if row.started_unix_ms is not None:
            if type(start) is int and row.started_unix_ms < start:
                raise IterationResultsError("Iteration begins before the report")
            if previous_finish is not None and row.started_unix_ms < previous_finish:
                raise IterationResultsError("Iteration intervals overlap or run backwards")
        if row.finished_unix_ms is not None:
            if type(finish) is int and row.finished_unix_ms > finish:
                raise IterationResultsError("Iteration finishes after the report")
            if type(start) is int and row.finished_unix_ms < start:
                raise IterationResultsError("Iteration finishes before the report")
        if row.started_unix_ms is not None:
            previous_finish = row.finished_unix_ms
    return rows


def zero_iteration_counts() -> dict[str, int]:
    """Create an independent JSON-compatible counter for aggregation."""
    return {"planned": 0, "executed": 0, **dict.fromkeys(STATE_KEYS, 0)}


def add_iteration_counts(total: dict[str, int], added: dict[str, int]) -> dict[str, int]:
    """Return the sum of complete counters without mutating either input."""
    return {key: total[key] + added[key] for key in zero_iteration_counts()}


class _LegacyStep(BaseModel):
    """Read optional historical step evidence; malformed rows provide no proof."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    iteration: Annotated[int, Field(strict=True, ge=1, le=10)]
    status: str
    started_unix_ms: Timestamp | None = None
    finished_unix_ms: Timestamp | None = None


def _legacy_states(report: dict[str, JsonValue], repeat_count: int) -> list[str]:
    """Infer attempted slots without treating preallocated skipped steps as execution.

    A previously admitted legacy success retains its complete-success semantics.
    Other outcomes require real starts. Unresolved earlier attempts remain running
    (legacy evidence only) so executed still records every proven attempt.
    """
    if report.get("status") == "succeeded":
        return ["passed"] * repeat_count
    groups: dict[int, list[_LegacyStep]] = {}
    raw_steps = report.get("steps")
    if isinstance(raw_steps, list):
        for raw in raw_steps:
            try:
                step = _LegacyStep.model_validate(raw)
            except ValidationError:
                continue
            if step.iteration <= repeat_count:
                groups.setdefault(step.iteration, []).append(step)
    attempted = [
        iteration
        for iteration, steps in groups.items()
        if any(step.started_unix_ms is not None and step.status != "skipped" for step in steps)
    ]
    last_attempted = max(attempted, default=0)
    states = ["unknown"] * repeat_count
    failure = report.get("failure")
    failure_iteration = failure.get("iteration") if isinstance(failure, dict) else None
    for iteration, steps in groups.items():
        state = "unknown"
        if iteration in attempted:
            state = "running"
            started = [step for step in steps if step.started_unix_ms is not None]
            failures = {step.status for step in started} & {"failed", "timed_out", "cancelled"}
            if failures:
                state = next(key for key in ("failed", "timed_out", "cancelled") if key in failures)
            elif iteration < last_attempted and all(
                step.status == "passed" and step.started_unix_ms is not None for step in steps
            ):
                state = "passed"
            if (
                (state == "running" and iteration == last_attempted)
                or (type(failure_iteration) is int and failure_iteration == iteration)
            ) and report.get("status") in {"failed", "timed_out", "cancelled"}:
                state = str(report["status"])
        elif all(step.status == "skipped" and step.started_unix_ms is None for step in steps):
            state = "skipped"
        states[iteration - 1] = state
    return states


def iteration_counts(report: dict[str, JsonValue], repeat_count: int) -> dict[str, int]:
    """Count explicit native states, falling back to documented historical inference."""
    rows = validate_iteration_results(report, repeat_count)
    states = (
        [row.status for row in rows] if rows is not None else _legacy_states(report, repeat_count)
    )
    counts = zero_iteration_counts()
    for state in states:
        counts[state] += 1
    counts["planned"] = sum(counts[key] for key in STATE_KEYS)
    counts["executed"] = sum(counts[key] for key in ATTEMPTED_KEYS)
    return counts
