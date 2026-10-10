"""Validated wire definitions shared by the play-scenario tool and CLI."""

import json
from typing import Annotated, Literal, assert_never
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

ScenarioName = Annotated[str, Field(strict=True, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
JobId = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$")]
RepeatCount = Annotated[int, Field(strict=True, ge=1, le=10)]
RunTimeout = Annotated[int, Field(strict=True, ge=1, le=1800)]
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
    "load_scene", "wait_scene", "click_ui", "wait_object", "reset_state", "wait_state"
]
TargetId = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]


def normalize_native_definition(value: JsonValue) -> JsonValue:
    """Omit native serialized null step options without weakening authored schema validation."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    optional = {
        "scene",
        "target",
        "target_id",
        "reset_ids",
        "click_mode",
        "count",
        "active",
        "component",
        "property",
        "stable_for_ms",
        "state_id",
        "state_equals",
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


class PlayScenarioPropertyCondition(BaseModel):
    """Compare one serialized component property with an exact finite JSON scalar."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    equals: ScenarioScalar

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


class ScenarioStep(BaseModel):
    """One bounded active-scene condition or effect; targets are paths, never Unity IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    action: StepAction
    scene: Annotated[str, Field(strict=True, min_length=1, max_length=4096)] | None = None
    target: Annotated[str, Field(strict=True, min_length=1, max_length=4096)] | None = None
    target_id: TargetId | None = None
    reset_ids: Annotated[list[TargetId], Field(min_length=1, max_length=16)] | None = None
    state_id: TargetId | None = None
    state_equals: ScenarioScalar | None = None
    click_mode: Literal["direct", "raycast"] | None = None
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=120)] = 30
    count: Annotated[int, Field(strict=True, ge=0, le=10000)] | None = None
    active: Annotated[bool, Field(strict=True)] | None = None
    component: Annotated[str, Field(strict=True, min_length=1, max_length=256)] | None = None
    property: PlayScenarioPropertyCondition | None = None
    stable_for_ms: Annotated[int, Field(strict=True, ge=0, le=60000)] | None = None

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
        match self.action:
            case "reset_state":
                if self.model_fields_set - {"name", "action", "timeout_seconds", "reset_ids"}:
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
                    "stable_for_ms",
                }:
                    raise ValueError(
                        "wait_state accepts only name, action, timeout_seconds, state_id, state_equals and stable_for_ms"
                    )
                if self.state_id is None or self.state_equals is None:
                    raise ValueError(
                        "wait_state requires state_id and a non-null scalar state_equals"
                    )
            case "load_scene" | "wait_scene" | "click_ui" | "wait_object":
                if "reset_ids" in self.model_fields_set:
                    message = "reset_ids is only valid for reset_state"
                    raise ValueError(message)
            case unreachable:
                assert_never(unreachable)
        if self.action != "wait_state" and self.model_fields_set & {"state_id", "state_equals"}:
            raise ValueError("state_id and state_equals are only valid for wait_state")
        object_fields = {"count", "active", "component", "property"}
        optional_fields = object_fields | {"stable_for_ms", "target_id", "click_mode"}
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
            case "load_scene" | "click_ui" | "reset_state":
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
            case "load_scene" | "wait_scene":
                if self.scene is None or self.model_fields_set & {"target", "target_id"}:
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
                if (self.target is None) == (
                    self.target_id is None
                ) or "scene" in self.model_fields_set:
                    message = "Object steps require exactly one target or target_id and no scene"
                    raise ValueError(message)
                if "target" in self.model_fields_set and self.target is None:
                    message = "target must be omitted rather than null"
                    raise ValueError(message)
                if self.target is not None:
                    _path_segments(self.target)
            case "reset_state" | "wait_state":
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
    tags: Annotated[list[ScenarioName], Field(max_length=16)] = Field(default_factory=list)
    resources: PlayScenarioResourceOptions = Field(default_factory=PlayScenarioResourceOptions)
    query_budget: PlayScenarioQueryBudget = Field(default_factory=PlayScenarioQueryBudget)
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

    @field_validator("tags")
    @classmethod
    def check_tags(cls, value: list[str]) -> list[str]:
        """Tags are unique canonical slugs shared with suite selection."""
        return _unique_selectors(value)

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


class PlayScenarioSuite(BaseModel):
    """A bounded native suite selects saved names and the union of matching tags."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    name: ScenarioName
    scenarios: Annotated[list[ScenarioName], Field(max_length=16)] = Field(default_factory=list)
    tags: Annotated[list[ScenarioName], Field(max_length=16)] = Field(default_factory=list)
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
                    {"name", "job_id", "repeat_count", "timeout_seconds", "source_revision"},
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
