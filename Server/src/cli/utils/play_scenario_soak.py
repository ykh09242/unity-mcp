"""Strict admission of bounded evidence from one same-process Player soak."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import stat
from typing import Annotated, Final, Literal

from models.play_scenarios import JobId, PlayScenarioCommand
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

ROW_LIMIT: Final = 16384
JOURNAL_LIMIT: Final = ROW_LIMIT * 1000
SUMMARY_LIMIT: Final = 65536
IDENTITY_FIELDS: Final = (
    "job_id",
    "definition_hash",
    "build_id",
    "build_source_revision",
    "payload_hash",
    "process_id",
)
Count = Annotated[int, Field(ge=0, le=2**63 - 1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ReportFile = Annotated[str, Field(pattern=r"^iteration-[0-9]{4}\.json$")]
Diagnostic = Annotated[str, Field(max_length=512)]


class SoakContractError(ValueError):
    """The evidence cannot establish a complete owned same-process execution."""


class SoakRequest(BaseModel):
    """The explicit schema-2 extension leaves native repeat_count fixed at one."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    iterations: Annotated[int, Field(ge=2, le=1000)] = 100
    max_runtime_seconds: Annotated[int, Field(ge=1, le=86400)] = 3600
    failure_policy: Literal["stop", "continue"] = "stop"
    retain_reports: Annotated[int, Field(ge=1, le=64)] = 4


class SoakIdentity(BaseModel):
    """Every artifact must carry exactly the identity admitted by the launcher."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    job_id: JobId
    definition_hash: Digest
    build_id: JobId
    build_source_revision: Annotated[str, Field(max_length=128)] | None
    payload_hash: Digest
    process_id: Annotated[int, Field(ge=1)]

    @field_validator("build_source_revision")
    @classmethod
    def revision(cls, value: str | None) -> str | None:
        return PlayScenarioCommand.check_revision(value)


class SoakQueryCounts(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    target_searches: Count
    hierarchy_visits: Count


class SoakMetric(BaseModel):
    """Native opt-in samples preserve unavailable measurements as null."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)
    iteration: Annotated[int, Field(ge=1, le=1000)]
    timestamp_unix_ms: Count
    managed_bytes: Count | None
    allocated_bytes: Count | None
    object_count: Count | None
    runner_subscription_count: Count | None
    runner_handle_count: Count | None
    ignored_for_trend: bool
    error: Diagnostic | None


class SoakMetricsSummary(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    sample_count: Annotated[int, Field(ge=0, le=1000)]
    usable_samples: Annotated[int, Field(ge=0, le=1000)]
    warmup_iterations: Annotated[int, Field(ge=0, le=9)]
    bounded_samples: Annotated[list[SoakMetric], Field(max_length=10)]
    warnings: Annotated[list[Annotated[str, Field(max_length=128)]], Field(max_length=3)]
    summary: Annotated[str, Field(max_length=128)]


class SoakRow(SoakIdentity):
    """A complete compact row never substitutes for its selected full run report."""

    schema_version: Annotated[int, Field(ge=1, le=1)]
    iteration: Annotated[int, Field(ge=1, le=1000)]
    status: Literal["succeeded", "failed", "timed_out", "cancelled"]
    started_unix_ms: Count | None
    finished_unix_ms: Count | None
    runner_resources_released: bool
    failure_code: Diagnostic | None
    error: Diagnostic | None
    cleanup_error: Diagnostic | None
    unexpected_log_count: Count
    query_counts: SoakQueryCounts | None
    metrics: Annotated[list[SoakMetric], Field(max_length=1)] | None
    report_file: ReportFile

    @model_validator(mode="after")
    def interval(self) -> "SoakRow":
        if self.started_unix_ms is not None and (
            self.finished_unix_ms is None or self.finished_unix_ms < self.started_unix_ms
        ):
            raise SoakContractError("Soak row timestamps disagree")
        if self.status == "succeeded" and self.started_unix_ms is None:
            raise SoakContractError("Soak success row lacks actual iteration start")
        if self.report_file != f"iteration-{self.iteration:04d}.json":
            raise SoakContractError("Soak row report filename disagrees with iteration")
        if self.metrics is not None and any(
            sample.iteration != self.iteration for sample in self.metrics
        ):
            raise SoakContractError("Soak metric attribution disagrees with global iteration")
        return self

    @property
    def passed(self) -> bool:
        return (
            self.status == "succeeded"
            and self.runner_resources_released
            and self.failure_code is None
            and self.error is None
            and self.cleanup_error is None
            and self.unexpected_log_count == 0
        )


class SoakSummary(SoakIdentity):
    """A checkpoint is terminal evidence only after process and journal admission."""

    schema_version: Annotated[int, Field(ge=1, le=1)]
    process_scope: Literal["same-process"]
    requested_iterations: Annotated[int, Field(ge=2, le=1000)]
    attempted_iterations: Annotated[int, Field(ge=0, le=1000)]
    passed_iterations: Annotated[int, Field(ge=0, le=1000)]
    failed_iterations: Annotated[int, Field(ge=0, le=1000)]
    skipped_iterations: Annotated[int, Field(ge=0, le=1000)]
    recorded_iterations: Annotated[int, Field(ge=0, le=1000)]
    first_failure_iteration: Annotated[int, Field(ge=1, le=1000)] | None
    started_unix_ms: Count
    deadline_unix_ms: Count
    finished_unix_ms: Count | None
    status: Literal["running", "succeeded", "failed", "cancelled", "timed_out"]
    finalization_state: Literal["running", "completed"]
    failure_policy: Literal["stop", "continue"]
    retain_reports: Annotated[int, Field(ge=1, le=64)]
    journal_file: Literal["iterations.jsonl"]
    journal_rows: Annotated[int, Field(ge=0, le=1000)]
    journal_bytes: Annotated[int, Field(ge=0, le=JOURNAL_LIMIT)]
    journal_sha256: Digest | None
    journal_complete: bool
    runner_resources_released: bool
    selected_iteration: Annotated[int, Field(ge=1, le=1000)] | None
    retained_report_files: Annotated[list[ReportFile], Field(max_length=64)]
    error: Annotated[str, Field(max_length=2048)] | None
    metrics_summary: SoakMetricsSummary | None


@dataclass(frozen=True, slots=True)
class SoakJournal:
    """One streaming pass retains at most the contract's thousand compact rows."""

    rows: tuple[SoakRow, ...]
    byte_count: int
    digest: str
    error: str | None = None


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    parsed: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in parsed:
            raise SoakContractError("Soak JSON contains duplicate object keys")
        parsed[key] = value
    return parsed


def _reject_constant(value: str) -> None:
    raise SoakContractError(f"Soak JSON contains non-finite number {value}")


def read_soak_bytes(path: Path, limit: int = SUMMARY_LIMIT) -> bytes:
    """Read one bounded regular file without following file links."""
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        raise SoakContractError("Soak artifact is linked, special or exceeds its byte bound")
    with path.open("rb") as stream:
        encoded = stream.read(limit + 1)
    if len(encoded) > limit:
        raise SoakContractError("Soak artifact exceeds its byte bound")
    return encoded


def parse_soak_json(encoded: bytes) -> dict[str, JsonValue]:
    """Reject duplicate keys, non-finite numbers and non-object native documents."""
    parsed = json.loads(
        encoded.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if not isinstance(parsed, dict):
        raise SoakContractError("Soak artifact must be a JSON object")
    return parsed


def read_soak_json(path: Path, limit: int = SUMMARY_LIMIT) -> dict[str, JsonValue]:
    """Read a single bounded unambiguous native document."""
    return parse_soak_json(read_soak_bytes(path, limit))


def read_soak_journal(path: Path, identity: SoakIdentity, *, partial: bool = False) -> SoakJournal:
    """Parse ordered complete rows once; partial mode exposes only a valid durable prefix."""
    rows: list[SoakRow] = []
    byte_count = 0
    digest = sha256()
    try:
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise SoakContractError("Soak journal is linked or special")
        if metadata.st_size > JOURNAL_LIMIT:
            raise SoakContractError("Soak journal exceeds its total byte bound")
        with path.open("rb") as stream:
            while encoded := stream.readline(ROW_LIMIT + 1):
                if len(rows) >= 1000 or len(encoded) > ROW_LIMIT or not encoded.endswith(b"\n"):
                    raise SoakContractError("Soak journal row is incomplete or exceeds its bound")
                parsed = json.loads(
                    encoded.decode("utf-8"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                )
                row = SoakRow.model_validate(parsed)
                observed = SoakIdentity.model_validate(
                    {key: getattr(row, key) for key in IDENTITY_FIELDS}
                )
                if observed != identity or row.iteration != len(rows) + 1:
                    raise SoakContractError("Soak journal identity or order mismatch")
                if (
                    rows
                    and rows[-1].finished_unix_ms is not None
                    and (
                        row.started_unix_ms is not None
                        and row.started_unix_ms < rows[-1].finished_unix_ms
                    )
                ):
                    raise SoakContractError("Soak journal iterations overlap or regress")
                rows.append(row)
                byte_count += len(encoded)
                digest.update(encoded)
    except (OSError, ValueError) as exc:
        if not partial:
            raise
        return SoakJournal(tuple(rows), byte_count, digest.hexdigest(), str(exc)[:2048])
    return SoakJournal(tuple(rows), byte_count, digest.hexdigest())


def admit_soak_summary(
    summary: SoakSummary,
    request: SoakRequest,
    identity: SoakIdentity,
    journal: SoakJournal,
) -> None:
    """Require the final checkpoint to equal exact persisted rows and requested policy."""
    observed = SoakIdentity.model_validate({key: getattr(summary, key) for key in IDENTITY_FIELDS})
    attempted = sum(row.started_unix_ms is not None for row in journal.rows)
    passed = sum(row.passed for row in journal.rows)
    first_failure = next(
        (
            row.iteration
            for row in journal.rows
            if row.started_unix_ms is not None and not row.passed
        ),
        None,
    )
    selected = first_failure or next(
        (row.iteration for row in reversed(journal.rows) if row.started_unix_ms is not None), None
    )
    if (
        observed != identity
        or summary.requested_iterations != request.iterations
        or summary.failure_policy != request.failure_policy
        or summary.retain_reports != request.retain_reports
        or summary.deadline_unix_ms != summary.started_unix_ms + request.max_runtime_seconds * 1000
        or summary.attempted_iterations != attempted
        or summary.passed_iterations != passed
        or summary.failed_iterations != attempted - passed
        or summary.skipped_iterations != request.iterations - attempted
        or summary.recorded_iterations != len(journal.rows)
        or summary.first_failure_iteration != first_failure
        or summary.selected_iteration != selected
        or summary.journal_rows != len(journal.rows)
        or summary.journal_bytes != journal.byte_count
        or summary.journal_sha256 != journal.digest
        or journal.error
        or not summary.journal_complete
        or summary.finalization_state != "completed"
        or summary.status == "running"
        or not summary.runner_resources_released
        or summary.finished_unix_ms is None
        or summary.finished_unix_ms < summary.started_unix_ms
        or len(journal.rows) > request.iterations
    ):
        raise SoakContractError("Soak final checkpoint disagrees with owned durable evidence")
    if summary.status == "succeeded" and (
        attempted != request.iterations or passed != request.iterations or summary.error is not None
    ):
        raise SoakContractError("Soak success lacks all requested passed iterations")
    for index, row in enumerate(journal.rows):
        if (row.started_unix_ms is not None and row.started_unix_ms < summary.started_unix_ms) or (
            row.finished_unix_ms is not None and row.finished_unix_ms > summary.finished_unix_ms
        ):
            raise SoakContractError("Soak journal interval leaves the final session interval")
        if index < len(journal.rows) - 1 and (
            row.started_unix_ms is None
            or not row.runner_resources_released
            or row.status in {"timed_out", "cancelled"}
        ):
            raise SoakContractError(
                "Soak continued after an iteration requiring unconditional stop"
            )
    if summary.metrics_summary is not None:
        measurements = [sample for row in journal.rows for sample in row.metrics or ()]
        metrics = summary.metrics_summary
        usable = sum(
            sample.iteration > metrics.warmup_iterations and sample.error is None
            for sample in measurements
        )
        if (
            metrics.sample_count != len(measurements)
            or metrics.usable_samples != usable
            or metrics.bounded_samples != measurements[-10:]
        ):
            raise SoakContractError(
                "Soak metrics summary disagrees with persisted boundary samples"
            )
    if (
        request.failure_policy == "stop"
        and first_failure is not None
        and (first_failure != len(journal.rows))
    ):
        raise SoakContractError("Soak journal continued after stop-policy failure")
    retained = [row.report_file for row in journal.rows[-request.retain_reports :]]
    if selected is not None:
        selected_file = journal.rows[selected - 1].report_file
        if selected_file not in retained:
            retained = (
                [selected_file, *retained[-(request.retain_reports - 1) :]]
                if request.retain_reports > 1
                else [selected_file]
            )
    if len(set(summary.retained_report_files)) != len(summary.retained_report_files) or set(
        summary.retained_report_files
    ) != set(retained):
        raise SoakContractError("Soak retained report set disagrees with retention policy")
