"""Bounded recovery advice; facts and suspected causes have separate certainty."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecoveryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    origin: Literal["routing", "editor_state", "source_freshness", "local_process"]
    observed_at_unix_ms: int = Field(ge=0)
    age_ms: int | None = Field(default=None, ge=0)
    fact: Literal[
        "connected",
        "unavailable",
        "selection_required",
        "auth_required",
        "unknown",
        "wrong_target",
        "capacity",
        "reload_pending",
        "compile_failed",
        "stale_snapshot",
        "native_observed",
        "process_exited",
        "process_running",
        "native_probe_timed_out",
    ]


class RecoveryDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: Literal["unity-mcp/source_recovery@1"] = "unity-mcp/source_recovery@1"
    reason: Literal[
        "compilation_failed",
        "reload_in_progress",
        "transport_disconnected",
        "editor_exited",
        "editor_unresponsive",
        "unknown",
    ] = "unknown"
    certainty: Literal["observed", "suspected", "unknown"] = "unknown"
    next_action: Literal[
        "inspect_compile_diagnostics",
        "wait_and_observe_existing_work",
        "inspect_selected_connection",
        "inspect_selected_editor",
        "select_exact_instance",
        "use_authenticated_client",
        "observe_existing_jobs",
    ] = "inspect_selected_editor"
    source_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    compile_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    evidence: list[RecoveryEvidence] = Field(default_factory=list, max_length=8)
    limitations: list[
        Literal[
            "modal_and_safe_mode_not_observed",
            "editor_local_process_unavailable",
            "editor_log_not_inspected",
            "native_evidence_unavailable",
            "source_evidence_stale",
        ]
    ] = Field(default_factory=list, max_length=5)
