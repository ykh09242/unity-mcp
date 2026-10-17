"""Strict safe diagnostic observations; schemas and exception text are never evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from models.capabilities import CapabilityConnection

DiagnosticReason = Literal[
    "unsupported_handler",
    "attribute_unreadable",
    "metadata_invalid",
    "schema_or_default_invalid",
    "duplicate_shadowed",
    "duplicate_name",
    "native_disabled",
    "publication_failed",
    "batch_rejected",
    "diagnostic_limit",
    "not_observed",
    "unsupported_source",
    "connection_changed",
    "source_unavailable",
    "none",
]
DiagnosticAction = Literal[
    "fix_handler_signature",
    "review_tool_declaration",
    "review_schema_and_defaults",
    "rename_duplicate_or_review_order",
    "remove_duplicate_then_refresh",
    "review_tool_toggle",
    "inspect_registration_then_refresh",
    "repair_batch_then_refresh",
    "read_named_tool_or_reduce_catalog",
    "retry_after_connection_or_refresh",
    "none",
]


class DiagnosticModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ToolDiagnosticRow(DiagnosticModel):
    tool_name: str | None = Field(default=None, max_length=128)
    candidate_id: str | None = Field(default=None, max_length=64)
    declaring_type: str | None = Field(default=None, max_length=256)
    assembly: str | None = Field(default=None, max_length=64)
    stage: Literal["native_discovery", "native_enablement", "server_publication"]
    state: Literal[
        "discovered", "rejected", "disabled", "registered", "registration_failed", "unknown"
    ]
    reason: DiagnosticReason = "none"
    next_action: DiagnosticAction = "none"
    native_enabled: StrictBool | None = None


class NativeToolDiagnostics(DiagnosticModel):
    schema_version: Literal["unity-mcp/native-tool-diagnostics@1"] = (
        "unity-mcp/native-tool-diagnostics@1"
    )
    epoch: str | None = Field(default=None, max_length=64)
    complete: StrictBool = False
    truncated: StrictBool = False
    dropped: int = Field(default=0, ge=0)
    rows: list[ToolDiagnosticRow] = Field(default_factory=list, max_length=64)


class ToolDiagnosticAttempt(DiagnosticModel):
    source_kind: Literal["http", "stdio", "manual"]
    project_id: str = Field(max_length=256)
    generation: str | None = Field(default=None, max_length=64)
    catalog_generation: str | None = Field(default=None, max_length=64)
    attempt: int = Field(ge=1)
    catalog_revision: int = Field(ge=0)
    prior_catalog_retained: StrictBool = False
    truncated: StrictBool = False
    rows: list[ToolDiagnosticRow] = Field(default_factory=list, max_length=64)


class ToolDiagnosticLimits(DiagnosticModel):
    byte_accounting: Literal["utf8_json_including_scope_metadata"] = (
        "utf8_json_including_scope_metadata"
    )
    native_encoded_bytes: Literal[131072] = 131072
    server_scope_encoded_bytes: Literal[32768] = 32768
    server_total_encoded_bytes: Literal[524288] = 524288
    native_retained_rows: Literal[512] = 512
    source_scopes: Literal[256] = 256
    row_accounting: Literal["aggregate_response"] = "aggregate_response"
    named_rows: Literal[16] = 16
    preview_rows: Literal[64] = 64


class ToolDiagnostics(DiagnosticModel):
    schema_version: Literal["unity-mcp/tool-diagnostics@1"] = "unity-mcp/tool-diagnostics@1"
    cache_ttl_seconds: Literal[0] = 0
    connection: CapabilityConnection
    partial: StrictBool = True
    native: NativeToolDiagnostics = Field(default_factory=NativeToolDiagnostics)
    server: list[ToolDiagnosticAttempt] = Field(default_factory=list, max_length=3)
    reason: DiagnosticReason = "not_observed"
    limits: ToolDiagnosticLimits = Field(default_factory=ToolDiagnosticLimits)
    query_read_seconds: float = 5.0
    query_cleanup_grace_seconds: float = 1.0
    query_max_elapsed_seconds: float = 6.0


class ToolDiagnosticsResponse(DiagnosticModel):
    success: Literal[True] = True
    data: ToolDiagnostics
