"""Small, strict doctor receipts; capability schema version one stays unchanged."""

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

EXACT_TARGET_PATTERN: Final = r"(?=\S)[^@/\\\x00-\x1f\x7f]{1,128}@[0-9a-fA-F]{8,64}\Z"

SafeVersion = Annotated[str, Field(pattern=r"^[0-9][0-9A-Za-z+_.-]{0,79}$")]
Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Identifier = Annotated[str, Field(max_length=256, pattern=r"^[^\x00-\x1f\x7f]*$")]


class DoctorModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DoctorCheck(DoctorModel):
    status: Literal["passed", "failed", "unknown", "not_checked"]
    reason: Literal[
        "observed",
        "none",
        "source_unavailable",
        "unity_unavailable",
        "selection_required",
        "wrong_target",
        "source_mismatch",
        "comparison_unknown",
        "deadline",
        "capacity",
        "connection_changed",
        "readiness_blocked",
        "recovery_required",
        "readiness_unknown",
        "auth_required",
    ]
    next_action: Literal[
        "none",
        "retry_later",
        "select_instance",
        "reconnect_unity",
        "inspect_editor",
        "inspect_native_work_then_restart_editor",
        "restart_client_server",
        "verify_selected_source",
        "use_authenticated_client",
    ]


class DoctorPackage(DoctorModel):
    name: Literal["com.ykh09242.unity-mcp"] | None = None
    version: SafeVersion | None = None
    package_server_commit: Commit | None = None
    effective_source_kind: Literal[
        "package_pin", "override_pin", "override_unpinned", "unavailable"
    ] = "unavailable"
    effective_server_commit: Commit | None = None
    skill_revision: Commit | None = None


class DoctorServer(DoctorModel):
    version: SafeVersion | Literal["unknown"]
    source_commit: Commit | None = None
    server_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class DoctorTarget(DoctorModel):
    instance_id: Identifier | None = None
    project_hash: Identifier | None = None
    generation: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    execution_environment: Literal["editor", "player", "unknown"] = "unknown"


class DoctorLimits(DoctorModel):
    query_timeout_seconds: float
    query_cleanup_grace_seconds: float
    query_max_elapsed_seconds: float
    max_probes: Literal[5] = 5
    receipt_bytes: Literal[32768] = 32768


class DoctorReceipt(DoctorModel):
    schema_version: Literal["unity-mcp/doctor@1"] = "unity-mcp/doctor@1"
    cache_ttl_seconds: Literal[0] = 0
    scope: Literal["current_mcp_request"] = "current_mcp_request"
    observed_protocol: Annotated[str, Field(max_length=32)] | None = None
    connection_verified: bool = False
    server: DoctorServer
    target: DoctorTarget = Field(default_factory=DoctorTarget)
    package: DoctorPackage = Field(default_factory=DoctorPackage)
    editor_version: SafeVersion | None = None
    source_comparison: Literal["matched", "mismatch", "unknown"] = "unknown"
    checks: dict[Literal["target", "native_read", "source", "readiness"], DoctorCheck]
    limits: DoctorLimits
    residual_server_work: Literal["unknown"] = "unknown"


class DoctorResourceResponse(DoctorModel):
    success: Literal[True] = True
    data: DoctorReceipt


class DoctorCLIReceipt(DoctorModel):
    schema_version: Literal["unity-mcp/doctor-cli@1"] = "unity-mcp/doctor-cli@1"
    scope: Literal["diagnostic_sdk"] = "diagnostic_sdk"
    external_client_verified: Literal[False] = False
    residual_server_work: Literal["unknown"] = "unknown"
    residual_auth_io: Literal["none", "unknown"] = "none"
    endpoint: Annotated[str, Field(max_length=320)] | None = None
    requested_target: Identifier | None = None
    requested_protocol: Literal["auto", "legacy"] = "auto"
    observed_protocol: Annotated[str, Field(max_length=32)] | None = None
    connection_verified: bool = False
    overall: Literal["verified", "failed", "incomplete"] = "incomplete"
    exit_code: Literal[0, 1, 2, 3, 4] = 3
    reason: Literal[
        "verified",
        "invalid_target",
        "invalid_endpoint",
        "dependency_missing",
        "internal_dependency_error",
        "server_unreachable",
        "protocol_incompatible",
        "auth_required",
        "doctor_unavailable",
        "discovery_incomplete",
        "diagnostic_limit",
        "deadline",
        "wrong_target",
        "source_mismatch",
        "unity_unavailable",
        "comparison_unknown",
        "source_unavailable",
        "selection_required",
        "capacity",
        "connection_changed",
        "invalid_receipt",
        "target_unavailable",
    ]
    next_action: Literal[
        "none",
        "select_exact_instance",
        "start_selected_server",
        "use_authenticated_client",
        "update_selected_server",
        "retry_later",
        "restart_client_server",
        "verify_selected_source",
        "install_declared_dependencies",
        "inspect_editor",
    ]
    stages: dict[
        Literal["dependency", "discovery", "native_read"],
        Literal["passed", "failed", "unknown", "not_checked"],
    ] = Field(default_factory=dict)
    data: DoctorReceipt | None = None
