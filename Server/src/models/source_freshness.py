"""Sampled source/compile/load evidence; readiness alone never supplies it."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from models.evidence_diagnostics import EvidenceDiagnostics


class Coverage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    complete: bool = False
    reasons: list[str] = Field(default_factory=list, max_length=256)
    file_count: int = Field(default=0, ge=0, le=100000)
    input_bytes: int = Field(default=0, ge=0, le=256 * 1024 * 1024)
    atomic_compiler_input_provenance: Literal[False] = False


class AssemblyIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    name: str = Field(max_length=512)
    output_mvid: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    loaded_mvid: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class SourceFreshness(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, populate_by_name=True)
    schema_version: Literal["unity-mcp/source_freshness@1"] = Field(
        default="unity-mcp/source_freshness@1", alias="schema"
    )
    status: Literal[
        "observed_current", "changed", "pending", "compile_failed", "incomplete", "unavailable"
    ] = "unavailable"
    basis: Literal["stable_inputs_compile_and_load_observed"] = (
        "stable_inputs_compile_and_load_observed"
    )
    project_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    source_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    context_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    unity_version: str | None = Field(default=None, max_length=64)
    observed_at_unix_ms: int | None = Field(default=None, ge=0)
    compile_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    compile_generation: int | None = Field(default=None, ge=0)
    source_revision_confirmed: Literal[False] = False
    reason: str = Field(default="unsupported", max_length=512)
    coverage: Coverage = Field(default_factory=Coverage)
    loaded_assemblies: list[AssemblyIdentity] = Field(default_factory=list, max_length=512)
    process_id: int | None = Field(default=None, ge=1)
    observation_in_progress: bool = False
    evidence_diagnostics: EvidenceDiagnostics | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def consistent_current(self):
        if self.status == "observed_current" and (
            not all(
                (
                    self.project_id,
                    self.session_id,
                    self.source_token,
                    self.source_digest,
                    self.context_digest,
                    self.compile_id,
                )
            )
            or self.compile_generation is None
            or self.observed_at_unix_ms is None
            or not self.coverage.complete
            or self.coverage.reasons
            or not self.loaded_assemblies
            or any(
                not a.output_mvid or a.output_mvid != a.loaded_mvid for a in self.loaded_assemblies
            )
        ):
            raise ValueError("Current-source claim lacks complete compile/load evidence")
        return self


def parse_freshness(value) -> SourceFreshness:
    if value is None:
        return SourceFreshness()
    try:
        if not isinstance(value, dict):
            return SourceFreshness(
                reason="invalid_evidence",
                evidence_diagnostics=EvidenceDiagnostics(code="invalid_shape"),
            )
        if value.get("schema") != "unity-mcp/source_freshness@1":
            return SourceFreshness(
                reason="invalid_evidence",
                evidence_diagnostics=EvidenceDiagnostics(code="unsupported_schema"),
            )
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
            return SourceFreshness(
                reason="invalid_evidence",
                evidence_diagnostics=EvidenceDiagnostics(code="payload_limit_exceeded"),
            )
        return SourceFreshness.model_validate(
            {key: item for key, item in value.items() if key != "evidence_diagnostics"}
        )
    except ValidationError as error:
        return SourceFreshness(
            reason="invalid_evidence",
            evidence_diagnostics=EvidenceDiagnostics.from_validation(
                error, SourceFreshness.model_fields
            ),
        )
    except (ValueError, TypeError, RecursionError):
        return SourceFreshness(
            reason="invalid_evidence",
            evidence_diagnostics=EvidenceDiagnostics(code="invalid_encoding"),
        )
