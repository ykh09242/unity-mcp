"""Validated wire definitions shared by the play-scenario tool and CLI."""

from typing import Annotated, Literal, assert_never
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

ScenarioName = Annotated[str, Field(strict=True, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
JobId = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$")]
RepeatCount = Annotated[int, Field(strict=True, ge=1, le=10)]
RunTimeout = Annotated[int, Field(strict=True, ge=1, le=1800)]
ScenarioAction = Literal["save", "get", "list", "delete", "run", "status", "cancel"]
StepAction = Literal["load_scene", "wait_scene", "click_ui", "wait_object"]


def _path_segments(path: str) -> list[str]:
    """Require an exact canonical relative path, without normalization or ID aliases."""
    segments = path.split("/")
    if (
        "\\" in path
        or not path.strip()
        or any(category(char) == "Cc" for char in path)
        or any(segment in ("", ".", "..") for segment in segments)
        or len(segments) > 128
    ):
        raise ValueError("Path must be canonical, relative and at most 128 segments")
    return segments


class ScenarioStep(BaseModel):
    """One bounded active-scene condition or effect; targets are paths, never Unity IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    action: StepAction
    scene: Annotated[str, Field(strict=True, min_length=1, max_length=4096)] | None = None
    target: Annotated[str, Field(strict=True, min_length=1, max_length=4096)] | None = None
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=120)] = 30

    @model_validator(mode="after")
    def check_arguments(self) -> "ScenarioStep":
        """Reject unused selectors and unsafe or ambiguous scene/object paths."""
        if (
            not self.name.strip()
            or "\\" in self.name
            or any(category(char) == "Cc" for char in self.name)
        ):
            raise ValueError("Step name has an invalid character")
        match self.action:
            case "load_scene" | "wait_scene":
                if self.scene is None or "target" in self.model_fields_set:
                    raise ValueError("Scene steps require scene and do not accept target")
                segments = _path_segments(self.scene)
                if (
                    len(segments) < 2
                    or segments[0] != "Assets"
                    or not self.scene.endswith(".unity")
                    or any(segment.casefold() == "gamedata" for segment in segments)
                    or any(char in segment for segment in segments for char in ':*?"<>|')
                    or any(segment.endswith((".", " ")) for segment in segments)
                ):
                    raise ValueError("Scene must be an Assets/... .unity path outside GameData")
            case "click_ui" | "wait_object":
                if self.target is None or "scene" in self.model_fields_set:
                    raise ValueError("Object steps require target and do not accept scene")
                _path_segments(self.target)
            case unreachable:
                assert_never(unreachable)
        return self


class PlayScenario(BaseModel):
    """A saved, bounded scenario whose first step resets the scene for each repeat."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: ScenarioName
    poll_interval_ms: Annotated[int, Field(strict=True, ge=100, le=2000)] = 250
    steps: Annotated[list[ScenarioStep], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def check_first_step(self) -> "PlayScenario":
        """Every iteration begins by loading the first scene."""
        if self.steps[0].action != "load_scene":
            raise ValueError("The first step must be load_scene")
        return self


class PlayScenarioCommand(BaseModel):
    """Parse action-specific arguments before dispatching a single Unity command."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    action: ScenarioAction
    scenario: PlayScenario | None = None
    name: ScenarioName | None = None
    job_id: JobId | None = None
    repeat_count: RepeatCount | None = None
    timeout_seconds: RunTimeout | None = None

    @model_validator(mode="after")
    def check_arguments(self) -> "PlayScenarioCommand":
        """Only run accepts repeat/timeout, and only run/status/cancel accept job_id."""
        if self.action != "run" and (
            self.repeat_count is not None or self.timeout_seconds is not None
        ):
            raise ValueError("repeat_count and timeout_seconds are only valid for run")
        match self.action:
            case "save":
                if self.scenario is None or self.name is not None or self.job_id is not None:
                    raise ValueError("save requires scenario and accepts no name or job_id")
            case "get" | "delete":
                if self.name is None or self.scenario is not None or self.job_id is not None:
                    raise ValueError("get/delete require name and accept no scenario or job_id")
            case "run":
                if self.name is None or self.scenario is not None:
                    raise ValueError("run requires name and accepts no scenario")
            case "status" | "cancel":
                if self.job_id is None or self.name is not None or self.scenario is not None:
                    raise ValueError("status/cancel require job_id and accept no name or scenario")
            case "list":
                if self.scenario is not None or self.name is not None or self.job_id is not None:
                    raise ValueError("list accepts no scenario, name or job_id")
            case unreachable:
                assert_never(unreachable)
        return self

    def wire_parameters(self) -> dict[str, JsonValue]:
        """Emit only action arguments; supply run defaults at the command boundary."""
        params = self.model_dump(mode="json", exclude_none=True)
        if self.action == "run":
            params.setdefault("repeat_count", 1)
            params.setdefault("timeout_seconds", 300)
        return params
