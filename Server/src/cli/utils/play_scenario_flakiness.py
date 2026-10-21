"""Observe intermittent outcomes from explicit saved reports without execution or retry."""

from models.play_scenario_limits import MAX_REPEATS, MAX_SCENARIO_STEPS, MAX_STEP_RESULTS

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Literal

from cli.utils.play_scenario_analysis_io import AnalysisError, read_json
from cli.utils.play_scenario_iterations import iteration_counts, validate_iteration_results
from cli.utils.play_scenario_soak_environment import ENVIRONMENT_FIELDS, RecordedEnvironment
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

Text = Annotated[str, Field(strict=True, max_length=8192)]
IdentityText = Annotated[str, Field(strict=True, max_length=128)]


class FailureEvidence(BaseModel):
    """Bounded failure identity, independent of later successes."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    stage: Literal["setup", "main", "cleanup"] | None = None
    iteration: Annotated[int, Field(strict=True, ge=1, le=MAX_REPEATS)] | None = None
    step_index: Annotated[int, Field(strict=True, ge=0, le=MAX_SCENARIO_STEPS - 1)] | None = None
    name: IdentityText | None = None
    code: IdentityText | None = None
    message: Text | None = None


class StepEvidence(FailureEvidence):
    status: Text


class Reproduction(BaseModel):
    """Only evidence relevant to compatibility; missing values never imply compatibility."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    definition_hash: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")] | None = None
    unity_version: IdentityText | None = None
    package_version: IdentityText | None = None
    source_revision: IdentityText | None = None
    build_id: IdentityText | None = None
    payload_hash: IdentityText | None = None
    environment: RecordedEnvironment | None = None


class RunEvidence(BaseModel):
    """Input projection; session journals are deliberately unsupported."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    job_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    status: Literal["succeeded", "failed", "timed_out", "cancelled", "running", "pending"]
    repeat_count: Annotated[int, Field(strict=True, ge=1, le=MAX_REPEATS)]
    execution_environment: Literal["editor", "player"] | None = None
    reproduction: Reproduction = Field(default_factory=Reproduction)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    scenario: dict[str, JsonValue] | None = None
    failure: FailureEvidence | None = None
    cleanup_failures: list[FailureEvidence] = Field(default_factory=list, max_length=640)
    steps: list[StepEvidence] = Field(default_factory=list, max_length=MAX_STEP_RESULTS)


class OriginalOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)
    job_id: str
    status: str
    failure: FailureEvidence | None
    cleanup_failures: list[FailureEvidence] = Field(default_factory=list)


class FailureFrequency(BaseModel):
    model_config = ConfigDict(frozen=True)
    stage: str | None
    step_index: int | None
    name: str | None
    code: str | None
    status: str
    count: int = 1


class FlakinessGroup(BaseModel):
    """Export accumulator: mutable collections gather admitted evidence until export."""

    model_config = ConfigDict(frozen=True)
    compatibility: dict[str, JsonValue]
    compatible_history: bool
    counts: dict[str, int]
    sources: list[str] = Field(default_factory=list)
    original_outcomes: list[OriginalOutcome] = Field(default_factory=list)
    failed_steps: list[FailureFrequency] = Field(default_factory=list)
    timeout_durations_ms: list[int] = Field(default_factory=list)
    evidence_notes: list[str] = Field(default_factory=list)
    failure_rate_denominator: int = 0
    failure_rate: float | None = None
    intermittent: bool = False
    failed_step_groups_total: int = 0
    failed_step_groups_omitted: int = 0


class FlakinessAnalysis(BaseModel):
    """JSON export contract shared with CLI and offline HTML rendering."""

    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    status: Literal["analyzed", "incomplete"]
    diagnostic_only: Literal[True] = True
    interpretation: str = (
        "Intermittent means an observed pass and failure/timeout in a compatible group. "
        "Failure rate is (failed + timed_out) / (passed + failed + timed_out + cancelled); "
        "skipped, unknown and unfinished slots are excluded. Original outcomes are retained."
    )
    groups: list[FlakinessGroup]
    unsupported_inputs: list[dict[str, str]]

    def to_dict(self) -> dict[str, JsonValue]:
        """Return structured export data without opening embedded artifact paths."""
        return self.model_dump(mode="json")


def analyze_flakiness(sources: tuple[Path, ...]) -> FlakinessAnalysis:
    """Analyze at most 64 explicitly selected reports; never discover or replay histories."""
    if not 1 <= len(sources) <= 64:
        raise AnalysisError("Select between 1 and 64 explicit report files")
    groups: dict[str, FlakinessGroup] = {}
    unsupported: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    incomplete = False
    for source in sources:
        try:
            raw = read_json(source)
            if "soak" in raw:
                raise AnalysisError(
                    "Soak parent reports are unsupported; a selected iteration does not establish the whole soak outcome"
                )
            run = RunEvidence.model_validate(raw)
            counts = iteration_counts(raw, run.repeat_count)
            ledger = validate_iteration_results(raw, run.repeat_count)
            compatibility = {
                "execution_environment": run.execution_environment,
                **run.reproduction.model_dump(mode="json"),
                "metadata_sha256": sha256(
                    json.dumps(run.metadata, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "scenario_sha256": sha256(
                    json.dumps(run.scenario, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            }
            known = bool(
                run.reproduction.definition_hash
                and run.execution_environment
                and run.reproduction.unity_version
                and run.reproduction.unity_version.strip()
                and run.reproduction.package_version
                and run.reproduction.package_version.strip()
                and run.reproduction.environment
                and all(
                    getattr(run.reproduction.environment, field) is not None
                    and str(getattr(run.reproduction.environment, field)).strip().lower()
                    not in {"", "unknown", "unavailable"}
                    for field in ENVIRONMENT_FIELDS
                )
            )
            key = json.dumps(compatibility, sort_keys=True, separators=(",", ":"))
            if not known:
                key += str(source.absolute())
            identity = (key, run.job_id)
            if identity in identities:
                raise AnalysisError("Duplicate run identity; an attempt may only be counted once")
            identities.add(identity)
            group = groups.setdefault(
                key,
                FlakinessGroup(
                    compatibility=compatibility,
                    compatible_history=known,
                    counts=dict.fromkeys(counts, 0),
                ),
            )
            for state, count in counts.items():
                group.counts[state] += count
            group.original_outcomes.append(
                OriginalOutcome(
                    job_id=run.job_id,
                    status=run.status,
                    failure=run.failure,
                    cleanup_failures=run.cleanup_failures,
                )
            )
            group.sources.append(str(source))
            if (
                ledger is None
                or not known
                or any(counts[state] for state in ("unknown", "pending", "running"))
            ):
                incomplete = True
                group.evidence_notes.append(
                    f"{run.job_id}: ledger={'native' if ledger else 'legacy_inference'}; compatibility_known={known}"
                )
            for step in run.steps:
                if step.status in {"failed", "timed_out"}:
                    failures = ([run.failure] if run.failure else []) + run.cleanup_failures
                    matching_codes = [
                        failure.code
                        for failure in failures
                        if (failure.stage, failure.iteration, failure.step_index)
                        == (step.stage, step.iteration, step.step_index)
                        and failure.code
                    ]
                    group.failed_steps.append(
                        FailureFrequency(
                            stage=step.stage,
                            step_index=step.step_index,
                            name=step.name,
                            code=step.code
                            or (matching_codes[0] if len(set(matching_codes)) == 1 else None),
                            status=step.status,
                        )
                    )
            if run.failure and not any(
                step.status in {"failed", "timed_out"} for step in run.steps
            ):
                group.failed_steps.append(
                    FailureFrequency(
                        stage=run.failure.stage,
                        step_index=run.failure.step_index,
                        name=run.failure.name,
                        code=run.failure.code,
                        status=run.status,
                    )
                )
            if ledger:
                group.timeout_durations_ms.extend(
                    row.finished_unix_ms - row.started_unix_ms
                    for row in ledger
                    if row.status == "timed_out"
                    and row.finished_unix_ms is not None
                    and row.started_unix_ms is not None
                )
        except (OSError, ValueError, ValidationError) as exc:
            incomplete = True
            unsupported.append({"source": str(source), "error": str(exc)[:1024]})
    finalized: list[FlakinessGroup] = []
    for group in groups.values():
        passed = group.counts["passed"]
        failed = group.counts["failed"] + group.counts["timed_out"]
        denominator = passed + failed + group.counts["cancelled"]
        frequencies = Counter(
            (row.stage, row.step_index, row.name, row.code, row.status)
            for row in group.failed_steps
        )
        failed_steps = [
            FailureFrequency(
                stage=identity[0],
                step_index=identity[1],
                name=identity[2],
                code=identity[3],
                status=identity[4],
                count=count,
            )
            for identity, count in frequencies.most_common(64)
        ]
        finalized.append(
            group.model_copy(
                update={
                    "failure_rate_denominator": denominator,
                    "failure_rate": failed / denominator if denominator else None,
                    "intermittent": passed > 0 and failed > 0,
                    "failed_steps": failed_steps,
                    "failed_step_groups_total": len(frequencies),
                    "failed_step_groups_omitted": max(0, len(frequencies) - 64),
                }
            )
        )
    return FlakinessAnalysis(
        status="incomplete" if incomplete else "analyzed",
        groups=finalized,
        unsupported_inputs=unsupported,
    )
