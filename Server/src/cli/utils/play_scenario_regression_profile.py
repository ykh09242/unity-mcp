"""Strict user-project regression profiles, independent of repository QA presets."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from cli.utils.play_scenario_soak import SoakRequest
from cli.utils.play_scenario_soak import parse_soak_json
from cli.utils.play_scenario_visual_reports import CheckpointSelection


class ProfileValue(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class Execution(ProfileValue):
    kind: Literal["run", "soak", "session"] = "run"
    build_directory: Annotated[str, Field(min_length=1, max_length=1024)]
    graphical: bool = False
    timeout_seconds: Annotated[int, Field(ge=1, le=1800)] = 300
    cleanup_wait_seconds: Annotated[int, Field(ge=1, le=600)] = 360
    expected_build_revision: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    expected_build_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")] | None = None
    repeat_count: Annotated[int, Field(ge=1, le=10)] = 1
    soak: SoakRequest | None = None
    session_mode: Literal["shared-batches", "fresh-process", "compare"] = "shared-batches"
    iterations: Annotated[int, Field(ge=1, le=1000)] = 100
    batch_size: Annotated[int, Field(ge=2, le=10)] = 10
    max_runtime_seconds: Annotated[int, Field(ge=1, le=86400)] = 3600
    failure_policy: Literal["stop", "continue"] = "stop"
    retain_reports: Annotated[int, Field(ge=1, le=64)] = 16

    @model_validator(mode="after")
    def selected_options(self) -> Self:
        """Reject options that would otherwise be silently ignored by the selected launcher."""
        session_fields = {
            "session_mode",
            "iterations",
            "batch_size",
            "max_runtime_seconds",
            "failure_policy",
            "retain_reports",
        }
        if self.kind != "session" and session_fields & self.model_fields_set:
            raise ValueError("Session options require execution.kind=session")
        if self.kind != "run" and "repeat_count" in self.model_fields_set:
            raise ValueError("repeat_count requires execution.kind=run")
        if (self.kind == "soak") != (self.soak is not None):
            raise ValueError("Only execution.kind=soak requires a soak request")
        return self


class BaselineGate(ProfileValue):
    registry: Annotated[str, Field(min_length=1, max_length=1024)]
    name: Annotated[str, Field(min_length=1, max_length=128)]
    policy: Annotated[str, Field(min_length=1, max_length=1024)]


class VisualGate(ProfileValue):
    baseline_report: Annotated[str, Field(min_length=1, max_length=1024)]
    policy: Annotated[str, Field(min_length=1, max_length=1024)]
    checkpoint: CheckpointSelection


class UiGate(ProfileValue):
    baseline: Annotated[str, Field(min_length=1, max_length=1024)]
    candidate: Annotated[str, Field(min_length=1, max_length=1024)]
    geometry_tolerance: Annotated[float, Field(ge=0, le=10000, allow_inf_nan=False)] = 0.0
    require_occlusion: bool = False


class RegressionProfile(ProfileValue):
    schema_version: Literal[1]
    name: Annotated[str, Field(min_length=1, max_length=128)]
    execution: Execution
    baseline: BaselineGate | None = None
    visual_checks: list[VisualGate] = Field(default_factory=list, max_length=16)
    ui_checks: list[UiGate] = Field(default_factory=list, max_length=16)
    analyze: bool = True

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: int) -> int:
        if type(value) is not int:
            raise ValueError("schema_version must be integer 1")
        return value

    @model_validator(mode="after")
    def compatible_gates(self) -> Self:
        if self.baseline is not None and self.execution.kind != "soak":
            raise ValueError("Named soak baseline gates require execution.kind=soak")
        if self.visual_checks and (self.execution.kind != "run" or not self.execution.graphical):
            raise ValueError("Visual gates require a graphical ordinary run")
        if any(
            gate.checkpoint.iteration > self.execution.repeat_count for gate in self.visual_checks
        ):
            raise ValueError("Visual checkpoint iteration exceeds repeat_count")
        return self


def profile_path(profile: Path, spelling: str) -> Path:
    """Resolve every profile input relative to its file, never the process working directory."""
    return safe_local_path(profile.parent / spelling)


def load_profile(source: Path) -> RegressionProfile:
    """Freeze path meaning so the retained profile can be read from its output directory."""
    profile = RegressionProfile.model_validate(parse_soak_json(read_bytes(source, 65536)))
    execution = profile.execution.model_copy(
        update={"build_directory": str(profile_path(source, profile.execution.build_directory))}
    )
    baseline = profile.baseline
    if baseline:
        baseline = baseline.model_copy(
            update={
                "registry": str(profile_path(source, baseline.registry)),
                "policy": str(profile_path(source, baseline.policy)),
            }
        )
    return profile.model_copy(
        update={
            "execution": execution,
            "baseline": baseline,
            "visual_checks": [
                gate.model_copy(
                    update={
                        "baseline_report": str(profile_path(source, gate.baseline_report)),
                        "policy": str(profile_path(source, gate.policy)),
                    }
                )
                for gate in profile.visual_checks
            ],
            "ui_checks": [
                gate.model_copy(
                    update={
                        "baseline": str(profile_path(source, gate.baseline)),
                        "candidate": str(profile_path(source, gate.candidate)),
                    }
                )
                for gate in profile.ui_checks
            ],
        }
    )
