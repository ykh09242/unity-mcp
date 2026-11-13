"""Read compilation request evidence without invoking refresh on any peer."""

from typing import Annotated, Literal
import re
import json

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from models import MCPResponse
from models.evidence_diagnostics import EvidenceDiagnostics
from models.native_recovery import NativeRecovery, RecoveryAction, parse_recovery, recovery_action
from models.source_freshness import SourceFreshness, parse_freshness
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
import transport.unity_transport as unity_transport
from transport.legacy.unity_connection import async_send_command_with_retry


class CompileDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    severity: Literal["error", "warning"]
    assembly: str = Field(max_length=256)
    file: str = Field(max_length=512)
    line: int = Field(ge=0)
    column: int = Field(ge=0)
    message: str = Field(max_length=1024)
    diagnostic_text_omitted: bool = False

    @model_validator(mode="after")
    def safe_locations(self):
        absolute = r"[A-Za-z]:[\\/]|\\\\|(?<![A-Za-z0-9_])/(?:[^\s/]+/)*[^\s]+"
        if (
            any(part == ".." for part in self.file.replace("\\", "/").split("/"))
            or any(separator in self.assembly for separator in ("/", "\\"))
            or any(re.search(absolute, value) for value in (self.file, self.message))
        ):
            raise ValueError("Unsafe diagnostic location")
        return self


class CompileCoordination(BaseModel):
    """Bounded native ownership diagnostics, independent of compilation outcome."""

    model_config = ConfigDict(extra="forbid", strict=True)
    scope: Literal["owned_request_pipeline_events"]
    phase: Literal[
        "no_owned_reservation",
        "pending",
        "started",
        "finished",
        "recovery_required",
        "other_owned_reservation",
    ]
    reason: Literal["native_job_active", "recovery_required"] | None
    retryable: bool
    recovery_action: RecoveryAction | None
    recovery: NativeRecovery | None = Field(default=None, exclude_if=lambda value: value is None)
    completion_requires: Literal[
        "matching_pipeline_finish_and_editor_quiescence",
        "matching_pipeline_finish_or_native_task_termination_then_editor_quiescence",
    ]

    @field_validator("recovery", mode="before")
    @classmethod
    def optional_recovery(cls, value):
        return value if isinstance(value, NativeRecovery) else parse_recovery(value)

    @model_validator(mode="after")
    def bounded_guidance(self):
        self.recovery_action = recovery_action(self.reason, self.recovery, self.recovery_action)
        return self


class SuccessfulCompilation(BaseModel):
    """Last retained successful assembly compilation; not proof of current sources."""

    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    generation: int = Field(ge=1)
    started_at_unix_ms: int = Field(ge=0)
    finished_at_unix_ms: int = Field(ge=0)
    assembly_count: int = Field(ge=1, le=1000000)
    warning_count: int = Field(ge=0, le=1000000)

    @model_validator(mode="after")
    def ordered_events(self):
        if self.finished_at_unix_ms < self.started_at_unix_ms:
            raise ValueError("Invalid compilation event order")
        return self


class CompileJob(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["unity-mcp/compile_job@1"]
    compile_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    generation: int | None = Field(default=None, ge=1)
    requested_at_unix_ms: int | None = Field(default=None, ge=0)
    started_at_unix_ms: int | None = Field(default=None, ge=0)
    finished_at_unix_ms: int | None = Field(default=None, ge=0)
    state: Literal["requested", "running", "finished"] = "finished"
    outcome: Literal["success", "failed", "unknown"] = "unknown"
    attribution: Literal["observed_after_request", "ambiguous", "unconfirmed"] = "unconfirmed"
    reason: str = Field(default="", max_length=80, pattern=r"^[a-z_]*$")
    candidate_generations: list[int] = Field(default_factory=list, max_length=4)
    error_count: int = Field(default=0, ge=0, le=1000000)
    warning_count: int = Field(default=0, ge=0, le=1000000)
    diagnostics: list[CompileDiagnostic] = Field(default_factory=list, max_length=100)
    assembly_count: int = Field(default=0, ge=0, le=1000000)
    diagnostics_truncated: bool = False
    omitted_diagnostics: int = Field(default=0, ge=0, le=2000000)
    collection_complete: bool = False
    native_coordination: CompileCoordination | None = None
    status: Literal["compiled", "already_up_to_date", "failed", "pending", "unknown"] = "unknown"
    last_successful_compilation: SuccessfulCompilation | None = None
    evidence_diagnostics: EvidenceDiagnostics | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def truthful_outcome(self):
        if self.outcome != "unknown" and (
            self.state != "finished"
            or self.attribution != "observed_after_request"
            or not self.collection_complete
            or self.session_id is None
            or self.generation is None
            or self.candidate_generations != [self.generation]
            or self.requested_at_unix_ms is None
            or self.started_at_unix_ms is None
            or self.finished_at_unix_ms is None
            or self.assembly_count == 0
        ):
            raise ValueError("Incomplete compilation evidence")
        if self.outcome != "unknown" and self.requested_at_unix_ms > self.started_at_unix_ms:
            raise ValueError("Compilation started before its request")
        if (self.outcome == "success" and self.error_count) or (
            self.outcome == "failed" and not self.error_count
        ):
            raise ValueError("Compiler outcome disagrees with errors")
        if any(value < 1 for value in self.candidate_generations):
            raise ValueError("Invalid compilation candidate generation")
        if (
            self.started_at_unix_ms is not None
            and self.finished_at_unix_ms is not None
            and self.finished_at_unix_ms < self.started_at_unix_ms
        ):
            raise ValueError("Invalid compilation event order")
        if (
            sum(item.severity == "error" for item in self.diagnostics) > self.error_count
            or sum(item.severity == "warning" for item in self.diagnostics) > self.warning_count
        ):
            raise ValueError("Diagnostic totals disagree")
        # Derive the display status from evidence, never from a peer's status label.
        self.status = "unknown"
        if self.outcome in ("success", "failed"):
            self.status = "compiled" if self.outcome == "success" else "failed"
        elif self.state in ("requested", "running"):
            self.status = "pending"
        elif (
            self.reason == "no_assembly_compilation"
            and self.attribution == "observed_after_request"
            and self.collection_complete
            and self.session_id is not None
            and self.generation is not None
            and self.candidate_generations == [self.generation]
            and self.requested_at_unix_ms is not None
            and self.started_at_unix_ms is not None
            and self.finished_at_unix_ms is not None
            and self.requested_at_unix_ms <= self.started_at_unix_ms
            and self.assembly_count == 0
            and self.error_count == 0
        ):
            self.status = "already_up_to_date"
        if self.last_successful_compilation is not None and (
            self.last_successful_compilation.session_id != self.session_id
        ):
            self.last_successful_compilation = None
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")) > 65536:
            raise ValueError("Compilation evidence exceeds its record bound")
        return self


def unknown_compile_job(
    compile_id: str, reason: str, diagnostics: EvidenceDiagnostics | None = None
) -> dict:
    return CompileJob(
        schema_version="unity-mcp/compile_job@1",
        compile_id=compile_id,
        reason=reason,
        evidence_diagnostics=diagnostics,
    ).model_dump()


def parse_compile_job(value, compile_id: str, *, last_successful_compilation=None) -> dict:
    if value is None:
        return unknown_compile_job(compile_id, "tracking_unsupported")
    if isinstance(value, dict) and value.get("schema_version") not in (
        None,
        "unity-mcp/compile_job@1",
    ):
        return unknown_compile_job(
            compile_id, "unsupported_schema", EvidenceDiagnostics(code="unsupported_schema")
        )
    try:
        native = (
            {key: item for key, item in value.items() if key != "evidence_diagnostics"}
            if isinstance(value, dict)
            else value
        )
        job = CompileJob.model_validate(native)
        if job.compile_id != compile_id:
            return unknown_compile_job(compile_id, "job_identity_mismatch")
        if last_successful_compilation is not None:
            try:
                previous = SuccessfulCompilation.model_validate(last_successful_compilation)
                if previous.session_id == job.session_id:
                    job.last_successful_compilation = previous
            except ValidationError:
                pass  # Optional history cannot invalidate current compiler evidence.
        return job.model_dump()
    except ValidationError as error:
        return unknown_compile_job(
            compile_id,
            "invalid_evidence",
            EvidenceDiagnostics.from_validation(error, CompileJob.model_fields),
        )
    except (ValueError, TypeError, RecursionError):
        return unknown_compile_job(
            compile_id, "invalid_evidence", EvidenceDiagnostics(code="invalid_encoding")
        )


async def read_compile_job(
    instance: str | None,
    compile_id: str,
    *,
    observe_source: bool = False,
    evidence: dict | None = None,
) -> dict:
    # This command is safe even on old peers: NEVER fall back to refresh_unity.
    try:
        params = {"compile_id": compile_id}
        if observe_source:
            params["observe_source"] = True
        response = await unity_transport.send_job_observation(
            async_send_command_with_retry, instance, "get_compile_job", params
        )
    except Exception:
        return unknown_compile_job(compile_id, "observation_unavailable")
    value = response.model_dump() if isinstance(response, MCPResponse) else response
    if not isinstance(value, dict) or type(value.get("success")) is not bool:
        return unknown_compile_job(
            compile_id, "invalid_evidence", EvidenceDiagnostics(code="invalid_shape")
        )
    if not value["success"]:
        message = value.get("error") or value.get("message") or ""
        message = message.lower() if isinstance(message, str) else ""
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        if value.get("code") == "unknown_command" or message in (
            "unknown command",
            "unknown command: get_compile_job",
        ):
            return unknown_compile_job(compile_id, "tracking_unsupported")
        if (
            data.get("next_action") == "poll_same_job"
            or data.get("reason") == "outcome_unknown"
            or message
            in ("disconnected", "connection closed", "timeout", "temporarily unavailable")
        ):
            return unknown_compile_job(compile_id, "observation_unavailable")
        return unknown_compile_job(compile_id, "observation_rejected")
    if value.get("data") is None:
        return unknown_compile_job(
            compile_id, "invalid_evidence", EvidenceDiagnostics(code="invalid_shape")
        )
    result = parse_compile_job(
        value.get("data"),
        compile_id,
        last_successful_compilation=value.get("last_successful_compilation"),
    )
    if evidence is not None and value.get("source_freshness") is not None:
        evidence["source_freshness"] = parse_freshness(value["source_freshness"])
    # New peers keep diagnostics outside the strict ledger contract so older
    # servers can still read compiler outcomes. Invalid optional diagnostics do
    # not invalidate otherwise complete evidence or establish native completion.
    coordination = value.get("native_coordination")
    if coordination is not None:
        try:
            # Future optional sibling fields cannot invalidate the strict compiler ledger.
            known = (
                {
                    key: item
                    for key, item in coordination.items()
                    if key
                    in (
                        "scope",
                        "phase",
                        "reason",
                        "retryable",
                        "recovery_action",
                        "recovery",
                        "completion_requires",
                    )
                }
                if isinstance(coordination, dict)
                else coordination
            )
            parsed = CompileCoordination.model_validate(known)
            diagnostics = parsed.model_dump()
        except ValidationError:
            return result
        candidate = {**result, "native_coordination": diagnostics}
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= 65536:
            return candidate
    return result


def compile_follow_up(
    job: dict,
    instance: str | None,
    *,
    recovery_required: bool = False,
    source: SourceFreshness | None = None,
    source_observed: bool = False,
) -> dict:
    """Suggest observation of the same request without replaying compilation."""
    coordination = job.get("native_coordination") or {}
    if recovery_required or coordination.get("phase") == "recovery_required":
        return {"action": "inspect_native_recovery"}
    if job["state"] in ("requested", "running") or job["reason"] == "observation_unavailable":
        arguments = {"compile_id": job["compile_id"]}
        if instance:
            arguments["unity_instance"] = instance
        return {
            "action": "poll_same_job",
            "next_call": {"tool": "get_compile_job", "arguments": arguments},
        }
    actions = {
        "success": "verify_source_freshness",
        "failed": "inspect_compiler_diagnostics",
    }
    reason_actions = {
        "tracking_unsupported": "inspect_tracking_support",
        "unsupported_schema": "inspect_protocol_compatibility",
        "invalid_evidence": "inspect_evidence_diagnostics",
        "job_identity_mismatch": "inspect_job_identity",
        "observation_rejected": "inspect_observation_rejection",
        "not_found": "inspect_job_identity",
    }
    follow_up = {
        "action": actions.get(job["outcome"], reason_actions.get(job["reason"], "inspect_evidence"))
    }
    if (
        not source_observed
        and (job["outcome"] == "success" or job["reason"] == "result_unavailable")
        and (source is None or source.reason == "reload_requires_observation")
    ):
        arguments = {"compile_id": job["compile_id"], "observe_source": True}
        if instance:
            arguments["unity_instance"] = instance
        follow_up["next_call"] = {"tool": "get_compile_job", "arguments": arguments}
    return follow_up


class CompileJobResponse(MCPResponse):
    source_freshness: dict | None = Field(default=None, exclude_if=lambda value: value is None)
    follow_up: dict | None = Field(default=None, exclude_if=lambda value: value is None)


@mcp_for_unity_tool(
    description="Read compilation status, bounded diagnostics and the last successful compile by compile_id. Unknown means incomplete evidence; readiness and source-revision proof are separate.",
    annotations=ToolAnnotations(
        title="Get Compile Job", readOnlyHint=True, destructiveHint=False, idempotentHint=True
    ),
)
async def get_compile_job(
    ctx: Context,
    compile_id: Annotated[str, "Opaque compile_id returned by refresh_unity with compile=request"],
    observe_source: Annotated[
        bool, "Capture current source inputs without importing or compiling"
    ] = False,
) -> CompileJobResponse:
    if not isinstance(compile_id, str) or re.fullmatch(r"[a-f0-9]{32}", compile_id) is None:
        return CompileJobResponse(
            success=False, error="compile_id must contain 32 lowercase hexadecimal characters"
        )
    instance = await get_unity_instance_from_context(ctx)
    evidence = {}
    job = await read_compile_job(
        instance, compile_id, observe_source=observe_source, evidence=evidence
    )
    source = evidence.get("source_freshness")
    return CompileJobResponse(
        success=True,
        data=job,
        follow_up=compile_follow_up(job, instance, source=source, source_observed=observe_source),
        source_freshness=source.model_dump(by_alias=True) if source else None,
    )
