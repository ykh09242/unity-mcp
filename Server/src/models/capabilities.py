"""Bounded, evidence-based project capability discovery contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from models.native_recovery import NativeCoordination

WireCapability = Literal[
    "editor_state_v1", "large_result_v1", "large_result_gzip_v1", "command_cancel_v1"
]


class CapabilityModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CapabilityConnection(CapabilityModel):
    transport: Literal["http", "stdio"]
    status: Literal["connected", "unavailable", "selection_required", "auth_required", "unknown"]
    instance_id: str | None = None
    project_hash: str | None = None
    generation: str | None = None
    server_advertised: list[WireCapability] = Field(default_factory=list)
    negotiated: list[WireCapability] | None = None
    peer_advertised: None = None
    admission_available: bool | None = None
    pending_commands: int | None = None
    tenant_pending_commands: int | None = None
    reason: str | None = None
    next_action: str = "none"


class CapabilityTarget(CapabilityModel):
    instance_id: str | None = None
    project_hash: str | None = None
    execution_environment: Literal["editor", "player", "unknown"] = "unknown"
    headless: bool | None = None


class CapabilityReadiness(CapabilityModel):
    status: Literal["ready", "blocked", "unknown"] = "unknown"
    blocking_reasons: list[str] = Field(default_factory=list)
    next_action: str = "read_editor_state"
    source: Literal["editor_state"] = "editor_state"
    native_coordination: NativeCoordination | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class CapabilityFeature(CapabilityModel):
    supported: bool | None = None
    ready: bool | None = None
    source: str
    next_action: str
    limits: dict[str, int | float] = Field(default_factory=dict)
    status: Literal["ready", "blocked", "unsupported", "unknown"] = "unknown"
    reason: str | None = None
    blocking_reasons: list[str] = Field(default_factory=list)
    tool_enabled: bool | None = None
    package_name: str | None = None
    package_version: str | None = None
    required_version: str | None = None
    observed: dict[str, str | bool | None] = Field(default_factory=dict)


class CapabilityEvidence(CapabilityModel):
    status: Literal["observed", "unknown"] = "unknown"
    reason: str | None = None
    next_action: str = "retry_later"


class CapabilityLimits(CapabilityModel):
    query_timeout_seconds: float
    query_cleanup_grace_seconds: float
    query_max_elapsed_seconds: float
    max_probes: Literal[5, 6] = 5
    response_bytes: int
    response_depth: int
    response_nodes: int
    command_payload_bytes: int | None = None
    pending_per_session: int | None = None
    pending_per_user: int | None = None
    pending_global: int | None = None


class ProjectCapabilities(CapabilityModel):
    schema_version: Literal["unity-mcp/capabilities@1"] = "unity-mcp/capabilities@1"
    cache_ttl_seconds: Literal[0] = 0
    partial: bool = True
    target: CapabilityTarget
    connection: CapabilityConnection
    limits: CapabilityLimits
    readiness: CapabilityReadiness = Field(default_factory=CapabilityReadiness)
    packages: dict[str, bool | None] = Field(default_factory=dict)
    features: dict[str, CapabilityFeature] = Field(default_factory=dict)
    evidence: dict[str, CapabilityEvidence] = Field(default_factory=dict)


class ProjectCapabilitiesResponse(CapabilityModel):
    success: Literal[True] = True
    data: ProjectCapabilities
