"""CLI inspection receipts keep SDK advertisement separate from project evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool


class ToolAvailability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    native_enabled: StrictBool | None = None
    packages: dict[str, StrictBool | None] = Field(default_factory=dict)
    features: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    editor_readiness: dict[str, JsonValue] = Field(default_factory=dict)


class ToolDescriptionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["unity-mcp/tool-description@1"] = "unity-mcp/tool-description@1"
    name: str
    requested_instance: str | None = None
    scope: Literal["sdk_session", "selected_project"] = "sdk_session"
    observed_protocol: str | None = None
    advertised: StrictBool | None = None
    tool: dict[str, JsonValue] | None = None
    project_descriptor: dict[str, JsonValue] | None = None
    schema_source: Literal["sdk_tools_list", "project_declared_only", "unavailable"] = "unavailable"
    fallback_advertised: StrictBool | None = None
    selection_verified: StrictBool | None = None
    availability: ToolAvailability = Field(default_factory=ToolAvailability)
    groups: list[str] = Field(default_factory=list)
    evidence: dict[str, dict[str, str]] = Field(default_factory=dict)
    observation_consistency: Literal["independent_reads"] = "independent_reads"
    partial: bool = True
    reason: str = "source_unavailable"
    next_action: str = "retry_later"
    exit_code: Literal[0, 1, 2, 3] = 3
    residual_auth_io: Literal["none", "unknown"] = "none"
    cleanup_complete: StrictBool | None = None
    limits: dict[str, int | float] = Field(default_factory=dict)
