"""Read bounded saved evidence; a native final file alone is not process exit proof."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import stat
from typing import Annotated, Final

from models.play_scenarios import (
    JobId,
    PlayScenario,
    PlayScenarioCommand,
    normalize_native_definition,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from cli.utils.play_scenario_player import PlayerFinalReport, REPORT_LIMIT
from cli.utils.play_scenario_soak import (
    Digest,
    IDENTITY_FIELDS,
    SoakIdentity,
    SoakJournal,
    SoakRequest,
    SoakContractError,
    parse_soak_json,
    read_soak_bytes,
    read_soak_json,
)
from cli.utils.play_scenario_soak_reports import ClientSoakSummary, _ProcessReceipt, admit_soak_run

ENVIRONMENT_FIELDS: Final = (
    "platform",
    "build_target",
    "runtime",
    "scripting_backend",
    "architecture",
)
Label = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")]


class _Request(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    schema_version: Annotated[int, Field(ge=2, le=2)]
    job_id: JobId
    definition_hash: Digest
    build_id: JobId
    build_source_revision: Label | None
    payload_hash: Digest
    scenario_name: Annotated[str, Field(min_length=1, max_length=128)]
    repeat_count: Annotated[int, Field(ge=1, le=1)]
    timeout_seconds: Annotated[int, Field(ge=1, le=1800)]
    source_revision: Label | None = None
    soak: SoakRequest

    @field_validator("source_revision", "build_source_revision")
    @classmethod
    def revision(cls, value: str | None) -> str | None:
        return PlayScenarioCommand.check_revision(value)


class _Environment(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")
    unity_version: Label
    package_version: Label
    platform: Label | None = None
    build_target: Label | None = None
    runtime: Label | None = None
    scripting_backend: Label | None = None
    architecture: Label | None = None


@dataclass(frozen=True, slots=True)
class SavedSoak:
    identity: SoakIdentity
    scenario: PlayScenario
    request: _Request
    environment: _Environment
    journal: SoakJournal

    def provenance(self) -> dict[str, JsonValue]:
        """Keep build differences and unknown environment fields visible."""
        return {
            **self.identity.model_dump(mode="json"),
            "source_revision": self.request.source_revision,
            **self.environment.model_dump(mode="json"),
            "process_scope": "same-process",
            "requested_iterations": self.request.soak.iterations,
            "timeout_seconds": self.request.timeout_seconds,
            "max_runtime_seconds": self.request.soak.max_runtime_seconds,
            "metric_options": self.scenario.model_dump(mode="json")["metrics"],
            "journal_sha256": self.journal.digest,
        }


def read_saved_soak(directory: Path) -> SavedSoak:
    """Admit one complete successful history using a single streamed journal pass."""
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or directory.is_symlink()
        or getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise SoakContractError("Soak artifact directory must be a regular unlinked directory")
    request_bytes = read_soak_bytes(directory / "request.json")
    request = _Request.model_validate(parse_soak_json(request_bytes))
    receipt = _ProcessReceipt.model_validate(read_soak_json(directory / "process.json"))
    if (
        receipt.job_id != request.job_id
        or receipt.request_sha256 != sha256(request_bytes).hexdigest()
        or not receipt.process_ended
        or receipt.forced_termination
        or receipt.actual_exit_code != 0
    ):
        raise SoakContractError("Process receipt lacks matching natural successful exit evidence")
    identity = SoakIdentity.model_validate(
        {
            **{key: getattr(request, key) for key in IDENTITY_FIELDS if key != "process_id"},
            "process_id": receipt.process_id,
        }
    )
    definition_bytes = read_soak_bytes(directory / "definition.json")
    # The launcher adds exactly one platform newline outside the frozen hash input.
    if definition_bytes.endswith(b"\r\n"):
        definition_bytes = definition_bytes[:-2]
    elif definition_bytes.endswith(b"\n"):
        definition_bytes = definition_bytes[:-1]
    if sha256(definition_bytes).hexdigest() != identity.definition_hash:
        raise SoakContractError("Frozen scenario definition bytes disagree with their hash")
    scenario = PlayScenario.model_validate(
        normalize_native_definition(parse_soak_json(definition_bytes))
    )
    raw = read_soak_json(directory / "run.json", REPORT_LIMIT)
    report = PlayerFinalReport.model_validate(raw)
    if (
        report.player_schema_version != 2
        or report.status != "succeeded"
        or report.exit_code != 0
        or report.job_id != identity.job_id
        or report.scenario != scenario
        or report.timeout_seconds != request.timeout_seconds
        or request.scenario_name != scenario.name
        or report.report_error is not None
        or report.progress_error is not None
        or raw.get("persistence_error") is not None
    ):
        raise SoakContractError("Comparison requires a successful matching schema-2 final report")
    for key in IDENTITY_FIELDS:
        if key not in {"job_id", "process_id"} and report.reproduction.get(key) != getattr(
            identity, key
        ):
            raise SoakContractError(f"Final reproduction identity mismatch: {key}")
    if (
        report.reproduction.get("source_revision") != request.source_revision
        or report.reproduction.get("payload_verification") != "launcher_admission"
    ):
        raise SoakContractError("Final reproduction lacks matching verified launcher provenance")
    environment = _Environment.model_validate(report.reproduction)
    summary, journal = admit_soak_run(directory, raw, request.soak, identity)
    if summary.status != "succeeded":
        raise SoakContractError("Comparison requires fully observed successful planned iterations")
    client_raw = read_soak_json(directory / "client-soak.json")
    # Writing defaults are convenient; persisted admission evidence must be explicit.
    if not set(ClientSoakSummary.model_fields).issubset(client_raw):
        raise SoakContractError("Client soak evidence is missing required persisted fields")
    client = ClientSoakSummary.model_validate(client_raw)
    expected = {
        "evidence_state": "complete",
        "status": "succeeded",
        "job_id": identity.job_id,
        "process_id": identity.process_id,
        "requested_iterations": summary.requested_iterations,
        "recorded_iterations": summary.recorded_iterations,
        "attempted_iterations": summary.attempted_iterations,
        "passed_iterations": summary.passed_iterations,
        "failed_iterations": 0,
        "skipped_iterations": 0,
        "unknown_iterations": 0,
        "journal_bytes": journal.byte_count,
        "journal_sha256": journal.digest,
        "client_error": None,
    }
    if any(getattr(client, key) != value for key, value in expected.items()):
        raise SoakContractError(
            "Client soak evidence disagrees with complete native/process evidence"
        )
    warmup = scenario.metrics.warmup_iterations
    if any(
        sample.ignored_for_trend != (row.iteration <= warmup)
        for row in journal.rows
        for sample in row.metrics or []
    ):
        raise SoakContractError(
            "Boundary sample warmup flags disagree with the frozen metric options"
        )
    return SavedSoak(identity, scenario, request, environment, journal)


def require_compatible(baseline: SavedSoak, candidate: SavedSoak) -> list[str]:
    """Match recorded experiment conditions while preserving build labels as provenance."""
    conditions = {
        "scenario definition/hash": (
            (baseline.scenario, baseline.identity.definition_hash),
            (candidate.scenario, candidate.identity.definition_hash),
        ),
        "metric options": (baseline.scenario.metrics, candidate.scenario.metrics),
        "iteration count": (baseline.request.soak.iterations, candidate.request.soak.iterations),
        "iteration timeout": (baseline.request.timeout_seconds, candidate.request.timeout_seconds),
        "runtime bound": (
            baseline.request.soak.max_runtime_seconds,
            candidate.request.soak.max_runtime_seconds,
        ),
        "Unity version": (baseline.environment.unity_version, candidate.environment.unity_version),
        "package version": (
            baseline.environment.package_version,
            candidate.environment.package_version,
        ),
    }
    for label, (left, right) in conditions.items():
        if left != right:
            raise SoakContractError(f"Incompatible {label}")
    unknown = []
    for field in ENVIRONMENT_FIELDS:
        left, right = getattr(baseline.environment, field), getattr(candidate.environment, field)
        if left is None or right is None:
            unknown.append(field)
        elif left != right:
            raise SoakContractError(f"Incompatible recorded environment: {field}")
    return unknown
