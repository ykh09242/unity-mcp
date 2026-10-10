"""Compare complete saved native runs without transport, replay or inferred query counts."""

import json
import math
import os
from pathlib import Path
import stat
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from models.play_scenarios import (
    JobId,
    PlayScenario,
    RepeatCount,
    RunTimeout,
    StepAction,
    normalize_native_definition,
)
from cli.utils.play_scenario_iterations import IterationResult, validate_iteration_results

REPORT_LIMIT: Final = 2097152
MAX_COUNT: Final = 2**63 - 1
Count = Annotated[int, Field(strict=True, ge=0, le=MAX_COUNT)]
EmptyError = Literal[None, ""]


class QueryComparisonError(ValueError):
    """The supplied evidence cannot establish a comparable pair of completed runs."""


class _Counts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target_searches: Count
    hierarchy_visits: Count


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
    step_index: Annotated[int, Field(strict=True, ge=0, le=31)]
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
    resource_checks: Annotated[list[_ResourceCheck], Field(strict=True, max_length=10)]
    query_counts: _Counts
    reproduction: _Reproduction
    steps: Annotated[list[_Step], Field(strict=True, min_length=1, max_length=640)]

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


def compare_reports(
    baseline: dict[str, JsonValue],
    candidate: dict[str, JsonValue],
    *,
    max_target_searches_increase: int,
    max_hierarchy_visits_increase: int,
) -> dict[str, JsonValue]:
    """Compare recorded aggregate counts; a passing budget is not proof of a speedup."""
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
    for name in ("target_searches", "hierarchy_visits"):
        before, after = getattr(left.query_counts, name), getattr(right.query_counts, name)
        limit = getattr(limits, name)
        queries[name] = {
            "baseline": before,
            "candidate": after,
            "delta": after - before,
            "max_increase": limit,
            "within_budget": after - before <= limit,
        }
    return {
        "schema_version": 1,
        "status": "within_budget"
        if all(row["within_budget"] for row in queries.values())
        else "budget_exceeded",
        "baseline": _identity(left),
        "candidate": _identity(right),
        "queries": queries,
    }


def _object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _reject_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON constant")


def _check_depth(encoded: str) -> None:
    """Bound nesting before JSON recursion, respecting escaped characters inside strings."""
    depth, quoted, escaped = 0, False, False
    for char in encoded:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("JSON nesting exceeds 32 levels")
        elif char in "]}":
            depth -= 1


def _reject_remote_namespace(source: Path) -> None:
    """Reject Windows remote/device syntax before even a filesystem metadata lookup."""
    if os.fspath(source).replace("\\", "/").startswith(("//", "/??/")):
        raise ValueError("Remote or device input namespace")


def read_report(source: Path, label: str) -> dict[str, JsonValue]:
    """Read one bounded regular file; never follow links or paths embedded in reports."""
    try:
        _reject_remote_namespace(source)
        path = Path(os.path.abspath(source))
        _reject_remote_namespace(path)
        parts = [part.casefold() for part in path.parts]
        if any(
            parts[index : index + 3] == ["assets", "resources", "gamedata"]
            for index in range(len(parts) - 2)
        ):
            raise ValueError("Protected data path")
        for ancestor in (*reversed(path.parents), path):
            attributes = ancestor.lstat()
            if stat.S_ISLNK(attributes.st_mode) or (
                getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("Linked or reparse input path")
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > REPORT_LIMIT:
            raise ValueError("Input is not a regular file within 2 MiB")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise ValueError("Input changed before reading")
            encoded = stream.read(REPORT_LIMIT + 1)
        if len(encoded) > REPORT_LIMIT:
            raise ValueError("Input exceeds 2 MiB")
        decoded = encoded.decode("utf-8-sig")
        _check_depth(decoded)
        value = json.loads(
            decoded,
            object_pairs_hook=_object,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
        if not isinstance(value, dict):
            raise ValueError("A single native report object is required")
        return value
    except (OSError, ValueError, RecursionError) as exc:
        raise QueryComparisonError(
            f"{label} report must be a readable, unlinked regular UTF-8 JSON file within 2 MiB and 32 levels"
        ) from exc
