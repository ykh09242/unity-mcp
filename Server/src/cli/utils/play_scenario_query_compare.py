"""Compare complete saved native runs without transport, replay or inferred query counts."""

import json
from typing import Annotated, Final, Literal

from cli.utils.play_scenario_iterations import IterationResult, validate_iteration_results
from cli.utils.play_scenario_report_json import REPORT_LIMIT, QueryComparisonError, read_report
from models.play_scenario_limits import (
    MAX_REPEATS,
    MAX_SCENARIO_STEPS,
    MAX_STEP_RESULTS,
)
from models.play_scenarios import (
    JobId,
    PlayScenario,
    RepeatCount,
    RunTimeout,
    StepAction,
    normalize_native_definition,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

__all__ = [
    "MAX_COUNT",
    "MAX_STEPS",
    "REPORT_LIMIT",
    "QueryComparisonError",
    "compare_reports",
    "read_report",
]

MAX_COUNT: Final = 2**63 - 1
MAX_STEPS: Final = MAX_STEP_RESULTS
QUERY_METRICS: Final = ("target_searches", "hierarchy_visits")
Count = Annotated[int, Field(strict=True, ge=0, le=MAX_COUNT)]
EmptyError = Literal[None, ""]


class _Counts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_searches: Count
    hierarchy_visits: Count


class _StepQueryCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    query_counts: _Counts


class _StepQueryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    steps: Annotated[list[_StepQueryCounts], Field(strict=True, min_length=1, max_length=MAX_STEPS)]


class _Reproduction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    definition_hash: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
    unity_version: Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"\S")]
    package_version: Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"\S")]
    source_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None


class _Step(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    stage: Literal["setup", "main", "cleanup"]
    iteration: RepeatCount
    step_index: Annotated[int, Field(strict=True, ge=0, le=MAX_SCENARIO_STEPS - 1)]
    name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    action: StepAction
    status: Literal["passed"]
    started_unix_ms: Count
    finished_unix_ms: Count
    poll_count: Annotated[int, Field(strict=True, ge=1, le=MAX_COUNT)]


class _ResourceCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    iteration: RepeatCount
    baseline_unix_ms: Count
    checked_unix_ms: Count
    passed: Annotated[bool, Field(strict=True)]
    error: EmptyError
    new_scriptable_objects: Count
    new_subscriptions: Count
    new_handles: Count


class _Run(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    job_id: JobId
    scenario: PlayScenario
    execution_environment: Literal["editor", "player"]
    status: Literal["succeeded"]
    phase: Literal["finished"]
    repeat_count: RepeatCount
    started_unix_ms: Count
    finished_unix_ms: Count
    runner_resources_released: Annotated[bool, Field(strict=True)]
    error: EmptyError
    report_error: EmptyError
    pending_status: None
    pending_error: EmptyError
    cleanup_error: EmptyError
    failure: None
    cleanup_failures: Annotated[list[JsonValue], Field(strict=True, max_length=0)]
    unexpected_log_count: Annotated[int, Field(strict=True, ge=0, le=0)]
    unexpected_log_error: EmptyError
    last_unexpected_log_error: EmptyError
    resource_checks: Annotated[list[_ResourceCheck], Field(strict=True, max_length=MAX_REPEATS)]
    query_counts: _Counts
    reproduction: _Reproduction
    steps: Annotated[list[_Step], Field(strict=True, min_length=1, max_length=MAX_STEPS)]

    @field_validator("scenario", mode="before")
    @classmethod
    def parse_native_definition(cls, value: JsonValue) -> JsonValue:
        """Use the same serialized-null handling as the existing Player boundary."""
        return normalize_native_definition(value)


class _PlayerCompletion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    player_schema_version: Annotated[int, Field(strict=True, ge=1, le=2)]
    finalization_state: Literal["completed"]
    exit_code: Annotated[int, Field(strict=True, ge=0, le=0)]
    timeout_seconds: RunTimeout
    progress_error: EmptyError


def _check_budgets(run: _Run, ledger: tuple[IterationResult, ...]) -> None:
    """Reject success labels that contradict explicit native query or resource assertions."""
    budget = run.scenario.query_budget
    if budget.enabled and (
        run.query_counts.target_searches > budget.max_target_searches
        or run.query_counts.hierarchy_visits > budget.max_hierarchy_visits
    ):
        raise ValueError("Successful run exceeds its native query budget")
    resources = run.scenario.resources
    if len(run.resource_checks) != (run.repeat_count if resources.enabled else 0):
        raise ValueError("Resource checks do not cover the declared plan")
    for check, row in zip(run.resource_checks, ledger):
        iteration_steps = [step for step in run.steps if step.iteration == row.iteration]
        if (
            not iteration_steps
            or not check.passed
            or check.iteration != row.iteration
            or row.started_unix_ms is None
            or row.finished_unix_ms is None
            or not row.started_unix_ms
            <= check.baseline_unix_ms
            <= iteration_steps[0].started_unix_ms
            or not iteration_steps[-1].finished_unix_ms
            <= check.checked_unix_ms
            <= row.finished_unix_ms
            or check.new_scriptable_objects > resources.max_scriptable_objects
            or check.new_subscriptions > resources.max_subscriptions
            or check.new_handles > resources.max_handles
        ):
            raise ValueError("Resource assertion evidence is invalid or exceeds its budget")


def _admit(raw: dict[str, JsonValue], label: str) -> _Run:
    """Check explicit completion and the exact ordered partition of every planned step."""
    try:
        run = _Run.model_validate(raw)
        if "schema_version" in raw:
            raise ValueError("Unsupported root report schema_version")
        if not run.runner_resources_released or run.finished_unix_ms < run.started_unix_ms:
            raise ValueError("Run completion or resource release is invalid")
        ledger = validate_iteration_results(raw, run.repeat_count, terminal=True)
        if ledger is None:
            raise ValueError("An explicit iteration ledger is required")
        if run.execution_environment == "player":
            _PlayerCompletion.model_validate(raw)
        elif any(
            key in raw for key in ("player_schema_version", "finalization_state", "exit_code")
        ):
            raise ValueError("Editor report contains Player finalization fields")
        planned = [
            (iteration, stage, index, step)
            for iteration in range(1, run.repeat_count + 1)
            for stage, definitions in (
                ("setup", run.scenario.setup_steps),
                ("main", run.scenario.steps),
                ("cleanup", run.scenario.cleanup_steps),
            )
            for index, step in enumerate(definitions)
        ]
        if len(run.steps) != len(planned):
            raise ValueError("Step results do not cover the complete plan")
        previous_finish = run.started_unix_ms
        for result, (iteration, stage, index, definition) in zip(run.steps, planned):
            row = ledger[iteration - 1]
            if (
                (result.iteration, result.stage, result.step_index, result.name, result.action)
                != (iteration, stage, index, definition.name, definition.action)
                or row.started_unix_ms is None
                or row.finished_unix_ms is None
                or not row.started_unix_ms
                <= result.started_unix_ms
                <= result.finished_unix_ms
                <= row.finished_unix_ms
                or result.started_unix_ms < previous_finish
            ):
                raise ValueError("Step identity or completion evidence is invalid")
            previous_finish = result.finished_unix_ms
        _check_budgets(run, ledger)
        return run
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.errors(include_input=False)[0]["loc"])
        raise QueryComparisonError(
            f"{label} report has invalid or missing {location[:128]}"
        ) from exc
    except ValueError as exc:
        raise QueryComparisonError(f"{label} report: {exc}") from exc


def _identity(run: _Run) -> dict[str, JsonValue]:
    return {
        "job_id": run.job_id,
        "scenario_name": run.scenario.name,
        "execution_environment": run.execution_environment,
        "repeat_count": run.repeat_count,
        **run.reproduction.model_dump(mode="json"),
    }


def _step_counts(raw: dict[str, JsonValue], run: _Run, label: str) -> list[_Counts]:
    """Validate the whole counter ledger, including rows excluded from display."""
    try:
        evidence = _StepQueryEvidence.model_validate(raw)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.errors(include_input=False)[0]["loc"])
        raise QueryComparisonError(
            f"{label} report has invalid or missing {location[:128]}"
        ) from exc
    counts = [step.query_counts for step in evidence.steps]
    for name in QUERY_METRICS:
        # Native run and step counters accumulate the same deltas with signed-64 saturation.
        total = min(MAX_COUNT, sum(getattr(count, name) for count in counts))
        if total != getattr(run.query_counts, name):
            raise QueryComparisonError(f"{label} report step {name} total differs from run total")
    return counts


def _step_details(
    steps: list[_Step], left: list[_Counts], right: list[_Counts], limit: int
) -> dict[str, JsonValue]:
    rows = []
    for step, before, after in zip(steps[:limit], left, right):
        rows.append(
            {
                "iteration": step.iteration,
                "stage": step.stage,
                "step_index": step.step_index,
                "name": step.name,
                "action": step.action,
                "queries": {
                    name: {
                        "baseline": getattr(before, name),
                        "candidate": getattr(after, name),
                        "delta": getattr(after, name) - getattr(before, name),
                    }
                    for name in QUERY_METRICS
                },
            }
        )
    return {
        "total_steps": len(steps),
        "shown_steps": len(rows),
        "omitted_steps": len(steps) - len(rows),
        "rows": rows,
    }


def compare_reports(
    baseline: dict[str, JsonValue],
    candidate: dict[str, JsonValue],
    *,
    max_target_searches_increase: int,
    max_hierarchy_visits_increase: int,
    step_details_limit: int | None = None,
) -> dict[str, JsonValue]:
    """Compare recorded counts; optional step details do not change aggregate budgets."""
    if step_details_limit is not None and (
        type(step_details_limit) is not int or not 1 <= step_details_limit <= MAX_STEPS
    ):
        raise QueryComparisonError(f"Step detail limit must be an integer from 1 to {MAX_STEPS}")
    try:
        limits = _Counts(
            target_searches=max_target_searches_increase,
            hierarchy_visits=max_hierarchy_visits_increase,
        )
    except ValidationError as exc:
        raise QueryComparisonError(
            "Query increase limits must be non-negative signed 64-bit integers"
        ) from exc
    left, right = _admit(baseline, "baseline"), _admit(candidate, "candidate")
    for field in ("execution_environment", "repeat_count"):
        if getattr(left, field) != getattr(right, field):
            raise QueryComparisonError(f"Reports differ in {field}")
    for field in ("definition_hash", "unity_version"):
        if getattr(left.reproduction, field) != getattr(right.reproduction, field):
            raise QueryComparisonError(f"Reports differ in {field}")
    # JSON preserves scalar types (False versus 0) that Python model equality can conflate.
    if json.dumps(left.scenario.model_dump(mode="json"), sort_keys=True) != json.dumps(
        right.scenario.model_dump(mode="json"), sort_keys=True
    ):
        raise QueryComparisonError("Reports differ in normalized scenario definition")
    queries = {}
    for name in QUERY_METRICS:
        before, after = getattr(left.query_counts, name), getattr(right.query_counts, name)
        limit = getattr(limits, name)
        queries[name] = {
            "baseline": before,
            "candidate": after,
            "delta": after - before,
            "max_increase": limit,
            "within_budget": after - before <= limit,
        }
    result = {
        "schema_version": 1,
        "status": "within_budget"
        if all(row["within_budget"] for row in queries.values())
        else "budget_exceeded",
        "baseline": _identity(left),
        "candidate": _identity(right),
        "queries": queries,
    }
    if step_details_limit is not None:
        result["step_details"] = _step_details(
            left.steps,
            _step_counts(baseline, left, "baseline"),
            _step_counts(candidate, right, "candidate"),
            step_details_limit,
        )
    return result
