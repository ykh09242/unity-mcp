"""Read compilation request evidence without invoking refresh on any peer."""

from typing import Annotated, Literal
import re
import json

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from models import MCPResponse
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
    recovery_action: Literal["inspect_native_work_then_restart_editor"] | None
    completion_requires: Literal["matching_pipeline_finish_and_editor_quiescence"]


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


def unknown_compile_job(compile_id: str, reason: str) -> dict:
    return CompileJob(
        schema_version="unity-mcp/compile_job@1", compile_id=compile_id, reason=reason
    ).model_dump()


def parse_compile_job(value, compile_id: str, *, last_successful_compilation=None) -> dict:
    try:
        job = CompileJob.model_validate(value)
        if job.compile_id != compile_id:
            raise ValueError("Mismatched compilation identity")
        if last_successful_compilation is not None:
            try:
                previous = SuccessfulCompilation.model_validate(last_successful_compilation)
                if previous.session_id == job.session_id:
                    job.last_successful_compilation = previous
            except ValidationError:
                pass  # Optional history cannot invalidate current compiler evidence.
        return job.model_dump()
    except (ValidationError, ValueError):
        return unknown_compile_job(compile_id, "tracking_unsupported")


async def read_compile_job(instance: str | None, compile_id: str) -> dict:
    # This command is safe even on old peers: NEVER fall back to refresh_unity.
    try:
        response = await unity_transport.send_job_observation(
            async_send_command_with_retry, instance, "get_compile_job", {"compile_id": compile_id}
        )
    except Exception:
        return unknown_compile_job(compile_id, "observation_unavailable")
    value = response.model_dump() if isinstance(response, MCPResponse) else response
    if not isinstance(value, dict) or value.get("success") is not True:
        if (
            isinstance(value, dict)
            and isinstance(value.get("data"), dict)
            and value["data"].get("next_action") == "poll_same_job"
        ):
            return unknown_compile_job(compile_id, "observation_unavailable")
        return unknown_compile_job(compile_id, "tracking_unsupported")
    result = parse_compile_job(
        value.get("data"),
        compile_id,
        last_successful_compilation=value.get("last_successful_compilation"),
    )
    # New peers keep diagnostics outside the strict ledger contract so older
    # servers can still read compiler outcomes. Invalid optional diagnostics do
    # not invalidate otherwise complete evidence or establish native completion.
    coordination = value.get("native_coordination")
    if coordination is not None:
        try:
            diagnostics = CompileCoordination.model_validate(coordination).model_dump()
        except ValidationError:
            return result
        candidate = {**result, "native_coordination": diagnostics}
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= 65536:
            return candidate
    return result


@mcp_for_unity_tool(
    description="Read compilation status, bounded diagnostics and the last successful compile by compile_id. Unknown means incomplete evidence; readiness and source-revision proof are separate.",
    annotations=ToolAnnotations(
        title="Get Compile Job", readOnlyHint=True, destructiveHint=False, idempotentHint=True
    ),
)
async def get_compile_job(
    ctx: Context,
    compile_id: Annotated[str, "Opaque compile_id returned by refresh_unity with compile=request"],
) -> MCPResponse:
    if not isinstance(compile_id, str) or re.fullmatch(r"[a-f0-9]{32}", compile_id) is None:
        return MCPResponse(
            success=False, error="compile_id must contain 32 lowercase hexadecimal characters"
        )
    instance = await get_unity_instance_from_context(ctx)
    return MCPResponse(success=True, data=await read_compile_job(instance, compile_id))
