"""Validated wire definitions shared by the play-scenario tool and CLI."""

from models.play_scenario_limits import (
    DEFINITION_BYTES,
    MAX_AUXILIARY_STEPS,
    MAX_REPEATS,
    MAX_SCENARIO_STEPS,
    MAX_SUITE_SCENARIOS,
)

import json
from typing import Annotated, Literal, assert_never
from unicodedata import category

from models.play_scenario_parameters import (
    ScenarioDataset,
    ScenarioParameter,
    ScenarioParameterBinding,
    expand_parameters,
    validate_parameter_cases,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_serializer,
    model_validator,
)

ScenarioName = Annotated[str, Field(strict=True, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
JobId = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$")]
RepeatCount = Annotated[int, Field(strict=True, ge=1, le=MAX_REPEATS)]
RunTimeout = Annotated[int, Field(strict=True, ge=1, le=86400)]
ScenarioAction = Literal[
    "save",
    "get",
    "list",
    "delete",
    "run",
    "status",
    "cancel",
    "reports",
    "suite_save",
    "suite_get",
    "suite_list",
    "suite_delete",
    "suite_run",
    "suite_status",
    "suite_cancel",
    "suite_reports",
]
StepAction = Literal[
    "load_scene",
    "wait_scene",
    "unload_scene",
    "set_active_scene",
    "click_ui",
    "wait_object",
    "reset_state",
    "wait_state",
    "input",
    "mark_events",
    "wait_events",
]
EventName = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]
TargetId = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]


def normalize_native_definition(value: JsonValue) -> JsonValue:
    """Omit native serialized null step options without weakening authored schema validation."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if normalized.get("draft") is None:
        normalized.pop("draft", None)
    optional = {
        "scene",
        "load_mode",
        "scene_state",
        "target",
        "target_id",
        "reset_ids",
        "click_mode",
        "ui_backend",
        "element_name",
        "element_path",
        "count",
        "active",
        "component",
        "property",
        "stable_for_ms",
        "state_id",
        "state_equals",
        "state_compare",
        "event_id",
        "event_mark",
        "event_since",
        "event_name",
        "event_count",
        "event_order",
        "performance",
        "screenshot",
        "input",
        "wait_frames",
    }
    for field in ("steps", "setup_steps", "cleanup_steps"):
        entries = normalized.get(field)
        if isinstance(entries, list):
            normalized[field] = [
                {
                    key: item
                    for key, item in entry.items()
                    if item is not None or key not in optional
                }
                if isinstance(entry, dict)
                else entry
                for entry in entries
            ]
    return normalized


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


ScenarioScalar = (
    Annotated[bool, Field(strict=True)]
    | Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]
    | Annotated[float, Field(strict=True, allow_inf_nan=False)]
    | Annotated[str, Field(strict=True, max_length=1024)]
)


ScenarioNumber = (
    Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]
    | Annotated[float, Field(strict=True, allow_inf_nan=False)]
)


class PlayScenarioNumericCondition(BaseModel):
    """One finite numeric predicate, preserving exact int64 and binary64 operands."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    operator: Literal["gte", "lte", "range", "approximately"]
    value: ScenarioNumber | None = Field(default=None, exclude_if=lambda value: value is None)
    min: ScenarioNumber | None = Field(default=None, exclude_if=lambda value: value is None)
    max: ScenarioNumber | None = Field(default=None, exclude_if=lambda value: value is None)
    absolute_tolerance: ScenarioNumber | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    relative_tolerance: ScenarioNumber | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator(
        "value", "min", "max", "absolute_tolerance", "relative_tolerance", mode="before"
    )
    @classmethod
    def check_number(cls, value: JsonValue) -> JsonValue:
        """Reject coercion, especially bool and overflowing integers parsed as floats."""
        if type(value) not in (int, float):
            raise ValueError("Numeric operands must be signed 64-bit integers or finite numbers")
        if isinstance(value, int) and not -(2**63) <= value <= 2**63 - 1:
            raise ValueError("Numeric integers must fit signed 64 bits")
        return value

    @model_validator(mode="after")
    def check_operands(self) -> "PlayScenarioNumericCondition":
        """Validate field presence before comparing exact mixed integer/float bounds."""
        operands = self.model_fields_set - {"operator"}
        if self.operator in ("gte", "lte"):
            if operands != {"value"}:
                raise ValueError("gte/lte accept only operator and value")
        elif self.operator == "range":
            if operands != {"min", "max"}:
                raise ValueError("range accepts only operator, min and max")
            # Python compares int/float without rounding the integer to binary64.
            if self.min > self.max:
                raise ValueError("Numeric range min must not exceed max")
        else:
            tolerances = {"absolute_tolerance", "relative_tolerance"}
            if (
                "value" not in operands
                or not operands & tolerances
                or operands - ({"value"} | tolerances)
            ):
                raise ValueError(
                    "approximately requires value and at least one tolerance, without range bounds"
                )
            if any(getattr(self, field) < 0 for field in operands & tolerances):
                raise ValueError("Numeric tolerances must be finite and nonnegative")
        return self


class PlayScenarioPropertyCondition(BaseModel):
    """Compare one serialized component property with an exact finite JSON scalar."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    equals: ScenarioScalar | None = Field(default=None, exclude_if=lambda value: value is None)
    compare: PlayScenarioNumericCondition | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def check_comparison(self) -> "PlayScenarioPropertyCondition":
        """Require exactly one authored, non-null equality or numeric comparison."""
        if len(self.model_fields_set & {"equals", "compare"}) != 1 or (
            self.equals is None and self.compare is None
        ):
            raise ValueError("Property requires exactly one non-null equals or compare")
        return self

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
    warmup_iterations: Annotated[int, Field(strict=True, ge=0, le=MAX_REPEATS - 1)] = 1
    consecutive_increases: Annotated[int, Field(strict=True, ge=2, le=MAX_REPEATS - 1)] = 2
    managed_growth_bytes: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 1048576
    allocated_growth_bytes: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 1048576
    object_growth_count: Annotated[int, Field(strict=True, ge=0, le=10000)] = 0


class PlayScenarioDiagnosticsOptions(BaseModel):
    """Opt into a bounded screenshot alongside unsuccessful-run diagnostics."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    screenshot_on_failure: Annotated[bool, Field(strict=True)] = False
    record_timeline: Annotated[bool, Field(strict=True)] = False


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


class PlayScenarioPerformanceOptions(BaseModel):
    """Opt into duration and selected real-frame budgets; unsupported counters fail explicitly."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_duration_ms: Annotated[int, Field(strict=True, ge=0, le=120000)] | None = None
    max_frame_time_p95_ms: (
        Annotated[float, Field(strict=True, ge=0, le=120000, allow_inf_nan=False)] | None
    ) = None
    max_gc_allocated_bytes_per_frame: (
        Annotated[int, Field(strict=True, ge=0, le=2147483647)] | None
    ) = None
    sample_limit: Annotated[int, Field(strict=True, ge=1, le=4096)] = 4096

    @model_validator(mode="after")
    def check_omitted_limits(self) -> "PlayScenarioPerformanceOptions":
        """Explicit null limits are invalid authored configuration."""
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Optional performance limits must be omitted rather than null")
        return self


class PlayScenarioScreenshotOptions(BaseModel):
    """Capture a named composited frame matching an explicit bounded resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: ScenarioName
    width: Annotated[int, Field(strict=True, ge=1, le=8192)]
    height: Annotated[int, Field(strict=True, ge=1, le=8192)]

    @model_validator(mode="after")
    def check_pixel_budget(self) -> "PlayScenarioScreenshotOptions":
        """Match the native 64-Mi-pixel capture bound."""
        if self.width * self.height > 67108864:
            raise ValueError("Screenshot dimensions exceed the 64-Mi-pixel budget")
        return self


class ScenarioInput(BaseModel):
    """A bounded virtual-device command; press leases survive subsequent drag/release steps."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["key", "mouse", "touch", "gamepad", "wheel", "release_all"]
    state: Literal["press", "move", "release"] = "press"
    key: Annotated[str, Field(strict=True, min_length=1, max_length=128)] | None = None
    button: Literal["left", "right", "middle"] = "left"
    frames: Annotated[int, Field(strict=True, ge=1, le=36000)] = 1
    touch_id: Annotated[int, Field(strict=True, ge=1, le=10)] = 1
    position: (
        list[Annotated[float, Field(strict=True, ge=0, le=1000000, allow_inf_nan=False)]] | None
    ) = None

    control: (
        Literal[
            "leftStick",
            "rightStick",
            "leftTrigger",
            "rightTrigger",
            "buttonSouth",
            "buttonNorth",
            "buttonEast",
            "buttonWest",
            "leftShoulder",
            "rightShoulder",
            "leftStickPress",
            "rightStickPress",
            "start",
            "select",
            "dpadUp",
            "dpadDown",
            "dpadLeft",
            "dpadRight",
        ]
        | None
    ) = None
    value: (
        Annotated[float, Field(strict=True, ge=-1, le=1, allow_inf_nan=False)]
        | Annotated[
            list[Annotated[float, Field(strict=True, ge=-1, le=1, allow_inf_nan=False)]],
            Field(strict=True, min_length=2, max_length=2),
        ]
        | None
    ) = None
    delta: (
        Annotated[
            list[Annotated[float, Field(strict=True, ge=-10000, le=10000, allow_inf_nan=False)]],
            Field(strict=True, min_length=2, max_length=2),
        ]
        | None
    ) = None

    @model_serializer(mode="plain")
    def serialize_command(self) -> dict[str, JsonValue]:
        """Preserve authored payload fields; defaults for other devices are not wire fields."""
        return {field: getattr(self, field) for field in self.model_fields_set}

    @model_validator(mode="after")
    def check_command(self) -> "ScenarioInput":
        """Reject unrelated fields and invalid transitions before persistence."""
        match self.action:
            case "key":
                allowed = {"action", "state", "key", "frames"}
                if self.key is None or not self.key.strip() or self.state == "move":
                    raise ValueError("key requires a named key and state press or release")
            case "mouse":
                allowed = {"action", "state", "button", "position", "frames"}
                if self.state == "move" and self.position is None:
                    raise ValueError("mouse move requires position")
            case "touch":
                allowed = {"action", "state", "touch_id", "position", "frames"}
                if self.state != "release" and self.position is None:
                    raise ValueError("touch press/move requires position")
            case "gamepad":
                allowed = {"action", "control", "value", "state", "frames"}
                if self.control is None or self.state == "move":
                    raise ValueError(
                        "gamepad requires a supported control and state press or release"
                    )
                if self.state == "release":
                    if "value" in self.model_fields_set:
                        raise ValueError("gamepad release must omit value")
                elif self.control in ("leftStick", "rightStick"):
                    if not isinstance(self.value, list):
                        raise ValueError("Gamepad stick press requires a bounded two-number value")
                elif self.control in ("leftTrigger", "rightTrigger"):
                    if not isinstance(self.value, float) or not 0 <= self.value <= 1:
                        raise ValueError("Gamepad trigger press requires a scalar from zero to one")
                elif "value" in self.model_fields_set:
                    raise ValueError("Gamepad button press must omit value")
            case "wheel":
                allowed = {"action", "delta"}
                if self.delta is None:
                    raise ValueError("wheel requires a bounded two-number delta")
            case "release_all":
                allowed = {"action"}
            case unreachable:
                assert_never(unreachable)
        if self.model_fields_set - allowed:
            raise ValueError("Input command contains fields unrelated to its action")
        if self.state != "press" and "frames" in self.model_fields_set:
            raise ValueError("frames is permitted only on press input")
        if self.model_fields_set & {"key", "position", "control", "value", "delta"} and any(
            getattr(self, field) is None
            for field in self.model_fields_set & {"key", "position", "control", "value", "delta"}
        ):
            raise ValueError("Input fields must be omitted rather than null")
        if self.position is not None and len(self.position) != 2:
            raise ValueError("position must contain two screen coordinates")
        return self


class ScenarioStep(BaseModel):
    """One bounded condition/effect with optional exact scene scope; no native IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    action: StepAction
    input: ScenarioInput | None = None
    wait_frames: Annotated[int, Field(strict=True, ge=1, le=36000)] | None = None
    scene: Annotated[str, Field(strict=True, min_length=1, max_length=4096)] | None = None
    load_mode: Literal["single", "additive"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    scene_state: Literal["active", "loaded", "unloaded"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    target: Annotated[str, Field(strict=True, min_length=1, max_length=4096)] | None = None
    target_id: TargetId | None = None
    reset_ids: Annotated[list[TargetId], Field(min_length=1, max_length=16)] | None = None
    state_id: TargetId | None = None
    state_equals: ScenarioScalar | None = None
    state_compare: PlayScenarioNumericCondition | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    ui_backend: Literal["uitoolkit"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    element_name: Annotated[str, Field(strict=True, min_length=1, max_length=256)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    element_path: Annotated[str, Field(strict=True, max_length=4096)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    click_mode: Literal["direct", "raycast"] | None = None
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)] = 30
    count: Annotated[int, Field(strict=True, ge=0, le=10000)] | None = None
    active: Annotated[bool, Field(strict=True)] | None = None
    component: Annotated[str, Field(strict=True, min_length=1, max_length=256)] | None = None
    property: PlayScenarioPropertyCondition | None = None
    stable_for_ms: Annotated[int, Field(strict=True, ge=0, le=600000)] | None = None
    performance: PlayScenarioPerformanceOptions | None = None
    screenshot: PlayScenarioScreenshotOptions | None = None

    event_id: TargetId | None = Field(default=None, exclude_if=lambda value: value is None)
    event_mark: EventName | None = Field(default=None, exclude_if=lambda value: value is None)
    event_since: EventName | None = Field(default=None, exclude_if=lambda value: value is None)
    event_name: EventName | None = Field(default=None, exclude_if=lambda value: value is None)
    event_count: Annotated[int, Field(strict=True, ge=1, le=128)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    event_order: (
        Annotated[list[EventName], Field(strict=True, min_length=1, max_length=16)] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("scene")
    @classmethod
    def check_scene_path(cls, value: str | None) -> str | None:
        """Use the exact canonical asset-path policy for every selected scene."""
        match value:
            case None:
                return None
            case str():
                segments = _path_segments(value)
                if (
                    len(segments) < 2
                    or segments[0] != "Assets"
                    or not value.endswith(".unity")
                    or any(segment.casefold() == "gamedata" for segment in segments)
                    or any(char in segment for segment in segments for char in ':*?"<>|')
                    or any(segment.endswith((".", " ")) for segment in segments)
                ):
                    raise ValueError("Scene must be an Assets/... .unity path outside GameData")
                return value
            case unreachable:
                assert_never(unreachable)

    @field_validator("state_equals", mode="before")
    @classmethod
    def check_state_scalar(cls, value: JsonValue) -> JsonValue:
        """Read-only probes share exact scalar and UTF-16 bounds with property conditions."""
        return PlayScenarioPropertyCondition.check_scalar_bounds(value)

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
        ui_fields = self.model_fields_set & {"ui_backend", "element_name", "element_path"}
        if ui_fields:
            if (
                self.action != "click_ui"
                or self.ui_backend != "uitoolkit"
                or self.click_mode not in (None, "direct")
            ):
                raise ValueError(
                    "UI Toolkit selectors require click_ui, ui_backend=uitoolkit and direct mode"
                )
            if any(getattr(self, field) is None for field in ui_fields):
                raise ValueError("UI Toolkit selector fields must be omitted rather than null")
            if self.element_name is None and self.element_path is None:
                raise ValueError("UI Toolkit clicks require element_name or element_path")
            if self.element_name is not None and (
                not self.element_name.strip()
                or "\\" in self.element_name
                or any(category(char) == "Cc" for char in self.element_name)
                or _utf16_units(self.element_name) > 256
            ):
                raise ValueError("Invalid element_name")
            if self.element_path is not None:
                import re

                parts = self.element_path.split("/")
                if (
                    _utf16_units(self.element_path) > 4096
                    or len(parts) > 128
                    or (
                        self.element_path
                        and (
                            not self.element_path.startswith("/")
                            or any(
                                re.fullmatch(r"0|[1-9][0-9]*", part) is None
                                or int(part) > 2147483647
                                for part in parts[1:]
                            )
                        )
                    )
                ):
                    raise ValueError(
                        "element_path must be a bounded canonical root-relative index path"
                    )
        if "load_mode" in self.model_fields_set and (
            self.action != "load_scene" or self.load_mode is None
        ):
            raise ValueError("load_mode is a non-null option only for load_scene")
        if "scene_state" in self.model_fields_set and (
            self.action != "wait_scene" or self.scene_state is None
        ):
            raise ValueError("scene_state is a non-null option only for wait_scene")
        event_fields = {
            "event_id",
            "event_mark",
            "event_since",
            "event_name",
            "event_count",
            "event_order",
        }
        if self.action in ("mark_events", "wait_events"):
            supplied = self.model_fields_set & event_fields
            required = (
                {"event_id", "event_mark"}
                if self.action == "mark_events"
                else (
                    {"event_since", "event_order"}
                    if "event_order" in supplied
                    else {"event_since", "event_name", "event_count"}
                )
            )
            if supplied != required or self.model_fields_set - (
                required | {"name", "action", "timeout_seconds", "performance", "screenshot"}
            ):
                raise ValueError("Event actions require exactly their mark, count or order fields")
            if any(
                getattr(self, field) is None
                for field in supplied | (self.model_fields_set & {"performance", "screenshot"})
            ):
                raise ValueError("Event options must be omitted rather than null")
            if (
                not self.name.strip()
                or "\\" in self.name
                or any(category(char) == "Cc" for char in self.name)
            ):
                raise ValueError("Step name has an invalid character")
            return self
        if self.model_fields_set & event_fields:
            raise ValueError("Event fields are only permitted on event actions")
        if self.action == "input":
            if self.model_fields_set - {
                "name",
                "action",
                "timeout_seconds",
                "input",
                "wait_frames",
                "performance",
                "screenshot",
            }:
                raise ValueError("Input steps contain unrelated scenario fields")
            if self.input is None:
                raise ValueError("Input steps require input command")
            if "wait_frames" in self.model_fields_set and self.wait_frames is None:
                raise ValueError("wait_frames must be omitted rather than null")
            if self.input.action == "release_all" and self.wait_frames is not None:
                raise ValueError("release_all does not accept wait_frames")
        elif self.model_fields_set & {"input", "wait_frames"}:
            raise ValueError("input and wait_frames are permitted only on input steps")
        match self.action:
            case "reset_state":
                if self.model_fields_set - {
                    "name",
                    "action",
                    "timeout_seconds",
                    "reset_ids",
                    "performance",
                    "screenshot",
                }:
                    message = "reset_state accepts only name, action, timeout_seconds and reset_ids"
                    raise ValueError(message)
                if self.reset_ids is None or len(set(self.reset_ids)) != len(self.reset_ids):
                    message = "reset_state requires unique reset_ids"
                    raise ValueError(message)
            case "wait_state":
                if self.model_fields_set - {
                    "name",
                    "action",
                    "timeout_seconds",
                    "state_id",
                    "state_equals",
                    "state_compare",
                    "stable_for_ms",
                    "performance",
                    "screenshot",
                }:
                    raise ValueError(
                        "wait_state accepts only name, action, timeout_seconds, state_id, one equality or comparison and stable_for_ms"
                    )
                if (
                    self.state_id is None
                    or len(self.model_fields_set & {"state_equals", "state_compare"}) != 1
                    or (self.state_equals is None and self.state_compare is None)
                ):
                    raise ValueError(
                        "wait_state requires state_id and exactly one non-null state_equals or state_compare"
                    )
            case (
                "load_scene"
                | "wait_scene"
                | "unload_scene"
                | "set_active_scene"
                | "click_ui"
                | "wait_object"
                | "input"
            ):
                if "reset_ids" in self.model_fields_set:
                    message = "reset_ids is only valid for reset_state"
                    raise ValueError(message)
            case unreachable:
                assert_never(unreachable)
        if self.action != "wait_state" and self.model_fields_set & {
            "state_id",
            "state_equals",
            "state_compare",
        }:
            raise ValueError("State selectors and comparisons are only valid for wait_state")
        object_fields = {"count", "active", "component", "property"}
        optional_fields = object_fields | {
            "stable_for_ms",
            "target_id",
            "click_mode",
            "performance",
            "screenshot",
        }
        authored_fields = self.model_fields_set & optional_fields
        if any(getattr(self, field) is None for field in authored_fields):
            message = "Optional step fields must be omitted rather than null"
            raise ValueError(message)
        if self.stable_for_ms is not None and self.stable_for_ms >= self.timeout_seconds * 1000:
            message = "stable_for_ms must be less than the step timeout"
            raise ValueError(message)
        if self.action != "click_ui" and "click_mode" in self.model_fields_set:
            message = "click_mode is only valid for click_ui"
            raise ValueError(message)
        if self.target_id is not None and self.count not in (None, 0, 1):
            message = "ID selectors allow only count zero or one"
            raise ValueError(message)
        condition_fields = authored_fields & (object_fields | {"stable_for_ms"})
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
            case "wait_scene" | "wait_state":
                if authored_fields & object_fields:
                    message = "Object conditions are only valid for wait_object"
                    raise ValueError(message)
            case (
                "load_scene"
                | "unload_scene"
                | "set_active_scene"
                | "click_ui"
                | "reset_state"
                | "input"
            ):
                if condition_fields:
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
            case "load_scene" | "wait_scene" | "unload_scene" | "set_active_scene":
                if self.scene is None or self.model_fields_set & {"target", "target_id"}:
                    raise ValueError("Scene steps require scene and do not accept target")
            case "click_ui" | "wait_object":
                if (self.target is None) == (self.target_id is None):
                    message = "Object steps require exactly one target or target_id"
                    raise ValueError(message)
                if "scene" in self.model_fields_set and self.scene is None:
                    raise ValueError("scene must be omitted rather than null")
                if "target" in self.model_fields_set and self.target is None:
                    message = "target must be omitted rather than null"
                    raise ValueError(message)
                if self.target is not None:
                    _path_segments(self.target)
            case "reset_state" | "wait_state" | "input":
                pass
            case unreachable:
                assert_never(unreachable)
        return self


class PlayScenarioQueryBudget(BaseModel):
    """Opt into strict budgets for actual target resolutions and hierarchy visits."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: Annotated[bool, Field(strict=True)] = False
    max_target_searches: Annotated[int, Field(strict=True, ge=0, le=1000000)] = 4096
    max_hierarchy_visits: Annotated[int, Field(strict=True, ge=0, le=10000000)] = 1000000


class PlayScenarioResourceOptions(BaseModel):
    """Opt into registered resource identity growth assertions after cleanup."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: Annotated[bool, Field(strict=True)] = False
    max_scriptable_objects: Annotated[int, Field(strict=True, ge=0, le=4096)] = 0
    max_subscriptions: Annotated[int, Field(strict=True, ge=0, le=4096)] = 0
    max_handles: Annotated[int, Field(strict=True, ge=0, le=4096)] = 0


def _unique_selectors(values: list[str]) -> list[str]:
    """Keep authored order while rejecting duplicate canonical selectors."""
    if len(set(values)) != len(values):
        message = "Selectors must be unique"
        raise ValueError(message)
    return values


class PlayScenario(BaseModel):
    """A bounded scenario whose first executed step resets each repeat."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: ScenarioName
    draft: Annotated[bool, Field(strict=True)] | None = None
    tags: Annotated[list[ScenarioName], Field(max_length=16)] = Field(default_factory=list)
    resources: PlayScenarioResourceOptions = Field(default_factory=PlayScenarioResourceOptions)
    query_budget: PlayScenarioQueryBudget = Field(default_factory=PlayScenarioQueryBudget)
    poll_interval_ms: Annotated[int, Field(strict=True, ge=100, le=2000)] = 250
    steps: Annotated[list[ScenarioStep], Field(min_length=1, max_length=MAX_SCENARIO_STEPS)]
    setup_steps: Annotated[list[ScenarioStep], Field(max_length=MAX_AUXILIARY_STEPS)] = Field(
        default_factory=list
    )
    cleanup_steps: Annotated[list[ScenarioStep], Field(max_length=MAX_AUXILIARY_STEPS)] = Field(
        default_factory=list
    )
    cleanup_timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=1800)] = 30
    completion_stable_ms: Annotated[int, Field(strict=True, ge=0, le=10000)] = 250
    log_policy: PlayScenarioLogPolicy = Field(default_factory=PlayScenarioLogPolicy)
    metrics: PlayScenarioMetricsOptions = Field(default_factory=PlayScenarioMetricsOptions)
    diagnostics: PlayScenarioDiagnosticsOptions = Field(
        default_factory=PlayScenarioDiagnosticsOptions
    )

    parameters: (
        Annotated[
            dict[ScenarioName, ScenarioParameter], Field(strict=True, min_length=1, max_length=16)
        ]
        | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    parameter_bindings: (
        Annotated[list[ScenarioParameterBinding], Field(strict=True, min_length=1, max_length=32)]
        | None
    ) = Field(default=None, exclude_if=lambda value: value is None)
    datasets: (
        Annotated[list[ScenarioDataset], Field(strict=True, min_length=1, max_length=16)] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)

    def materialize(self, dataset: str | None = None) -> "PlayScenario":
        """Return one concrete validated definition with its own bounded case identity."""
        return type(self).model_validate(
            expand_parameters(self.model_dump(mode="json", exclude_none=True), dataset)
        )

    @field_validator("tags")
    @classmethod
    def check_tags(cls, value: list[str]) -> list[str]:
        """Tags are unique canonical slugs shared with suite selection."""
        return _unique_selectors(value)

    @model_validator(mode="after")
    def check_first_step(self) -> "PlayScenario":
        """Every iteration begins by loading a scene, within the definition byte bound."""
        if any(
            getattr(self, field) is None
            for field in self.model_fields_set & {"parameters", "parameter_bindings", "datasets"}
        ):
            raise ValueError("Parameter metadata must be omitted rather than null")
        for case in validate_parameter_cases(
            self.model_dump(mode="json", exclude_none=True), self.parameters
        ):
            type(self).model_validate(case)
        if "draft" in self.model_fields_set and self.draft is None:
            raise ValueError("draft must be omitted rather than null")
        for steps in (self.setup_steps, self.steps, self.cleanup_steps):
            marks: set[str] = set()
            for step in steps:
                if step.action == "mark_events":
                    if step.event_mark in marks or len(marks) >= 16:
                        raise ValueError("Event marks must be unique and at most sixteen per stage")
                    marks.add(step.event_mark)
                elif step.action == "wait_events" and step.event_since not in marks:
                    raise ValueError("event_since must name a prior mark in the same stage")
        first = self.setup_steps[0] if self.setup_steps else self.steps[0]
        if first.action != "load_scene" or first.load_mode == "additive":
            message = "The first executed step must be a Single load_scene anchor"
            raise ValueError(message)
        encoded = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > DEFINITION_BYTES:
            message = "Scenario definition exceeds 1 MiB"
            raise ValueError(message)
        return self


class PlayScenarioSuite(BaseModel):
    """A bounded native suite selects saved names and the union of matching tags."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    name: ScenarioName
    scenarios: Annotated[list[ScenarioName], Field(max_length=MAX_SUITE_SCENARIOS)] = Field(
        default_factory=list
    )
    tags: Annotated[list[ScenarioName], Field(max_length=MAX_SUITE_SCENARIOS)] = Field(
        default_factory=list
    )
    failure_policy: Literal["stop", "continue"] = "stop"

    @field_validator("scenarios", "tags")
    @classmethod
    def check_selectors(cls, value: list[str]) -> list[str]:
        """Duplicate selectors are invalid rather than silently normalized."""
        return _unique_selectors(value)

    @model_validator(mode="after")
    def check_selection(self) -> "PlayScenarioSuite":
        """Admission requires at least one selector; Unity resolves the bounded snapshot."""
        if not self.scenarios and not self.tags:
            message = "A suite requires scenarios or tags"
            raise ValueError(message)
        return self


class PlayScenarioCommand(BaseModel):
    """Parse action-specific arguments before dispatching a single Unity command."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    action: ScenarioAction
    scenario: PlayScenario | None = None
    suite: PlayScenarioSuite | None = None
    suite_id: JobId | None = None
    dataset: ScenarioName | None = Field(default=None, exclude_if=lambda value: value is None)
    source_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    name: ScenarioName | None = None
    job_id: JobId | None = None
    repeat_count: RepeatCount | None = None
    timeout_seconds: RunTimeout | None = None

    @field_validator("source_revision")
    @classmethod
    def check_revision(cls, value: str | None) -> str | None:
        """Bound caller-provided reproduction labels in native UTF-16 units."""
        if value is not None and (
            not value.strip()
            or any(category(char) == "Cc" for char in value)
            or _utf16_units(value) > 128
        ):
            message = "source_revision must be at most 128 UTF-16 units"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def check_arguments(self) -> "PlayScenarioCommand":
        """Reject unused fields before selecting an Editor or issuing any request."""
        fields = set(self.model_dump(exclude_none=True)) - {"action"}
        match self.action:
            case "save":
                required, allowed = {"scenario"}, {"scenario"}
            case "suite_save":
                required, allowed = {"suite"}, {"suite"}
            case "get" | "delete" | "suite_get" | "suite_delete":
                required, allowed = {"name"}, {"name"}
            case "run":
                required, allowed = (
                    {"name"},
                    {
                        "name",
                        "job_id",
                        "repeat_count",
                        "timeout_seconds",
                        "source_revision",
                        "dataset",
                    },
                )
            case "suite_run":
                required, allowed = (
                    {"name"},
                    {"name", "suite_id", "repeat_count", "timeout_seconds", "source_revision"},
                )
            case "status" | "cancel":
                required, allowed = {"job_id"}, {"job_id"}
            case "suite_status" | "suite_cancel":
                required, allowed = {"suite_id"}, {"suite_id"}
            case "reports" | "suite_reports":
                required, allowed = set(), {"name"}
            case "list" | "suite_list":
                required, allowed = set(), set()
            case unreachable:
                assert_never(unreachable)
        if not required <= fields or fields - allowed:
            message = (
                f"{self.action} requires {sorted(required)} and accepts only {sorted(allowed)}"
            )
            raise ValueError(message)
        return self

    def wire_parameters(self) -> dict[str, JsonValue]:
        """Emit only action arguments; supply run defaults at the command boundary."""
        params = self.model_dump(mode="json", exclude_none=True)
        if self.action in ("run", "suite_run"):
            params.setdefault("repeat_count", 1)
            params.setdefault("timeout_seconds", 300)
        return params
