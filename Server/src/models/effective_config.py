"""Secret-safe, scoped effective configuration diagnostics."""

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

ConfigValue: TypeAlias = str | int | float | bool | None | list[str]
ConfigSource: TypeAlias = Literal[
    "default", "environment", "cli", "project_context", "default_map", "prompt", "runtime"
]
ConfigScope: TypeAlias = Literal["local_cli_configuration", "running_server_configuration"]


class ConfigOrigin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    source: ConfigSource = "default"
    source_detail: str = "built_in"
    overrides: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class EffectiveConfigEntry(ConfigOrigin):
    value: ConfigValue
    default: ConfigValue


class EffectiveConfigReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: Literal["unity-mcp/effective-config@1"] = "unity-mcp/effective-config@1"
    scope: ConfigScope
    running_server_config_observed: bool
    cache_ttl_seconds: Literal[0] = 0
    fields: dict[str, EffectiveConfigEntry] = Field(default_factory=dict)
