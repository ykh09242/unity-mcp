"""Validated wire definitions shared by the play-scenario tool and CLI."""

import json
from typing import Annotated, Literal, assert_never
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

ScenarioName = Annotated[str, Field(strict=True, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
JobId = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$")]
RepeatCount = Annotated[int, Field(strict=True, ge=1, le=10)]
RunTimeout = Annotated[int, Field(strict=True, ge=1, le=1800)]
ScenarioAction = Literal["save", "get", "list", "delete", "run", "status", "cancel", "reports"]
StepAction = Literal["load_scene", "wait_scene", "click_ui", "wait_object"]


def _utf16_units(value: str) -> int:
    """Match native string.Length bounds without normalizing authored Unicode text."""
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _condition_text(value: str) -> str:
    """Match native component/property Text validation without trimming exact selectors."""
    if (
        not value.strip()
        or "\\" in value
        or any(category(char) == "Cc" for char in value)
        or _utf16_units(value) > 256
    ):
        message = "Condition selector must be nonblank, control-free and at most 256 UTF-16 units"
        raise ValueError(message)
    return value


class PlayScenarioPropertyCondition(BaseModel):
    """Compare one serialized component property with an exact finite JSON scalar."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    equals: (
        Annotated[bool, Field(strict=True)]
        | Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]
        | Annotated[float, Field(strict=True, allow_inf_nan=False)]
        | Annotated[str, Field(strict=True, max_length=1024)]
    )

    @field_validator("equals", mode="before")
    @classmethod
    def check_scalar_bounds(cls, value: JsonValue) -> JsonValue:
        """Enforce native string bounds and prevent integer overflow from parsing as float."""
        match value:
            case bool() | float() | None | list() | dict():
                return value
            case str():
                if _utf16_units(value) > 1024:
                    message = "Property string must be at most 1024 UTF-16 units"
                    raise ValueError(message)
                return value
            case int():
                if not -(2**63) <= value <= 2**63 - 1:
                    message = "Property integer must fit signed 64 bits"
                    raise ValueError(message)
                return value
            case unreachable:
                assert_never(unreachable)

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        """Require the same canonical property selector text as native definitions."""
        return _condition_text(value)


class PlayScenarioLogPolicy(BaseModel):
    """Reject unexpected Error/Assert/Exception logs unless explicitly observed only."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["strict", "log_only"] = "strict"
    allowed_messages: Annotated[
        list[Annotated[str, Field(strict=True, min_length=1, max_length=1024)]],
        Field(max_length=32),
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_allowed_messages(self) -> "PlayScenarioLogPolicy":
        """Literal full-message exceptions must be unique and case-sensitive."""
        if any(_utf16_units(message) > 1024 for message in self.allowed_messages):
            message = "Allowed messages must be at most 1024 UTF-16 units"
            raise ValueError(message)
        if len(set(self.allowed_messages)) != len(self.allowed_messages):
            message = "allowed_messages must be unique"
            raise ValueError(message)
        return self


class PlayScenarioMetricsOptions(BaseModel):
    """Bound optional post-cleanup measurements and diagnostic trend thresholds."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: Annotated[bool, Field(strict=True)] = False
    warmup_iterations: Annotated[int, Field(strict=True, ge=0, le=9)] = 1
    consecutive_increases: Annotated[int, Field(strict=True, ge=2, le=9)] = 2
    managed_growth_bytes: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 1048576
    allocated_growth_bytes: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 1048576
    object_growth_count: Annotated[int, Field(strict=True, ge=0, le=10000)] = 0


class PlayScenarioDiagnosticsOptions(BaseModel):
    """Opt into a bounded screenshot alongside unsuccessful-run diagnostics."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    screenshot_on_failure: Annotated[bool, Field(strict=True)] = False


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
    count: Annotated[int, Field(strict=True, ge=0, le=10000)] | None = None
    active: Annotated[bool, Field(strict=True)] | None = None
    component: Annotated[str, Field(strict=True, min_length=1, max_length=256)] | None = None
    property: PlayScenarioPropertyCondition | None = None
    stable_for_ms: Annotated[int, Field(strict=True, ge=0, le=60000)] | None = None

    @field_validator("component")
    @classmethod
    def check_component(cls, value: str | None) -> str | None:
        """Validate exact type-name text; omitted components have no condition."""
        match value:
            case None:
                return value
            case str():
                return _condition_text(value)
            case unreachable:
                assert_never(unreachable)

    @model_validator(mode="after")
    def check_arguments(self) -> "ScenarioStep":
        """Reject unused selectors, invalid conditions and unsafe scene/object paths."""
        object_fields = {"count", "active", "component", "property"}
        optional_fields = object_fields | {"stable_for_ms"}
        authored_fields = self.model_fields_set & optional_fields
        if any(getattr(self, field) is None for field in authored_fields):
            message = "Optional step fields must be omitted rather than null"
            raise ValueError(message)
        if self.stable_for_ms is not None and self.stable_for_ms >= self.timeout_seconds * 1000:
            message = "stable_for_ms must be less than the step timeout"
            raise ValueError(message)
        match self.action:
            case "wait_object":
                if self.count == 0 and authored_fields & (object_fields - {"count"}):
                    message = "count=0 forbids active, component and property"
                    raise ValueError(message)
                if self.component is not None and self.count not in (None, 1):
                    message = "Component conditions require an effective count of one"
                    raise ValueError(message)
                if self.property is not None and self.component is None:
                    message = "Property conditions require component"
                    raise ValueError(message)
            case "wait_scene":
                if authored_fields & object_fields:
                    message = "Object conditions are only valid for wait_object"
                    raise ValueError(message)
            case "load_scene" | "click_ui":
                if authored_fields:
                    message = "Effect steps do not accept wait conditions"
                    raise ValueError(message)
            case unreachable:
                assert_never(unreachable)
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
    """A bounded scenario whose first executed step resets each repeat."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: ScenarioName
    poll_interval_ms: Annotated[int, Field(strict=True, ge=100, le=2000)] = 250
    steps: Annotated[list[ScenarioStep], Field(min_length=1, max_length=32)]
    setup_steps: Annotated[list[ScenarioStep], Field(max_length=16)] = Field(default_factory=list)
    cleanup_steps: Annotated[list[ScenarioStep], Field(max_length=16)] = Field(default_factory=list)
    cleanup_timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=300)] = 30
    completion_stable_ms: Annotated[int, Field(strict=True, ge=0, le=10000)] = 250
    log_policy: PlayScenarioLogPolicy = Field(default_factory=PlayScenarioLogPolicy)
    metrics: PlayScenarioMetricsOptions = Field(default_factory=PlayScenarioMetricsOptions)
    diagnostics: PlayScenarioDiagnosticsOptions = Field(
        default_factory=PlayScenarioDiagnosticsOptions
    )

    @model_validator(mode="after")
    def check_first_step(self) -> "PlayScenario":
        """Every iteration begins by loading a scene, within the definition byte bound."""
        first = self.setup_steps[0] if self.setup_steps else self.steps[0]
        if first.action != "load_scene":
            message = "The first executed step must be load_scene"
            raise ValueError(message)
        if len(self.setup_steps) + len(self.steps) + len(self.cleanup_steps) > 64:
            message = "The combined step count must not exceed 64"
            raise ValueError(message)
        encoded = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 65536:
            message = "Scenario definition exceeds 64 KiB"
            raise ValueError(message)
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
            case "reports":
                if self.scenario is not None or self.job_id is not None:
                    message = "reports accepts only an optional name"
                    raise ValueError(message)
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
