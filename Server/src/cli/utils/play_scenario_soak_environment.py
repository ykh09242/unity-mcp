"""Admit the immutable environment snapshot captured once per native launch."""

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cli.utils.play_scenario_soak import SoakContractError

ENVIRONMENT_FIELDS: Final = (
    "operating_system",
    "platform",
    "architecture",
    "scripting_backend",
    "build_mode",
    "runtime_version",
)
METADATA_PATTERN: Final = r"^[^\x00-\x1f\x7f-\x9f]+$"


def _metadata(value: str | None, max_units: int) -> str | None:
    """Match native UTF-16 bounds and reject malformed surrogate sequences."""
    if value is None:
        return None
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    try:
        scalar = encoded.decode("utf-16-le", errors="strict")
    except UnicodeError as exc:
        raise SoakContractError("Recorded environment metadata has malformed Unicode") from exc
    if len(encoded) // 2 > max_units:
        raise SoakContractError("Recorded environment metadata exceeds its UTF-16 bound")
    return scalar


class RecordedEnvironment(BaseModel):
    """Unavailable native facts remain explicit nulls; no host facts are inferred."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    schema_version: Annotated[int, Field(ge=1, le=1)]
    operating_system: (
        Annotated[str, Field(min_length=1, max_length=256, pattern=METADATA_PATTERN)] | None
    )
    platform: Annotated[str, Field(min_length=1, max_length=64, pattern=METADATA_PATTERN)] | None
    architecture: (
        Annotated[str, Field(min_length=1, max_length=32, pattern=METADATA_PATTERN)] | None
    )
    scripting_backend: Literal["mono", "il2cpp"] | None
    build_mode: Literal["editor", "development", "release"] | None
    runtime_version: (
        Annotated[str, Field(min_length=1, max_length=64, pattern=METADATA_PATTERN)] | None
    )

    @field_validator("operating_system")
    @classmethod
    def operating_system_metadata(cls, value: str | None) -> str | None:
        return _metadata(value, 256)

    @field_validator("platform", "runtime_version")
    @classmethod
    def runtime_metadata(cls, value: str | None) -> str | None:
        return _metadata(value, 64)

    @field_validator("architecture")
    @classmethod
    def architecture_metadata(cls, value: str | None) -> str | None:
        return _metadata(value, 32)

    def require_budget_support(self) -> None:
        """Only positively recorded desktop Player conditions support regression gating."""
        if any(getattr(self, field) is None for field in ENVIRONMENT_FIELDS):
            raise SoakContractError("Budget requires every recorded environment field to be known")
        if (
            self.platform not in {"WindowsPlayer", "LinuxPlayer", "OSXPlayer"}
            or self.architecture not in {"x86", "x64", "arm", "arm64"}
            or self.build_mode not in {"development", "release"}
            or any(
                str(getattr(self, field)).strip().lower() in {"", "unknown", "unavailable"}
                for field in ENVIRONMENT_FIELDS
            )
        ):
            raise SoakContractError(
                "Budget requires a supported recorded desktop Player environment"
            )
