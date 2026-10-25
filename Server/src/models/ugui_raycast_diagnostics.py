"""Closed native raycast receipts, including honest opted-in callback failure evidence."""

import json
from typing import Annotated, Final, Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, model_validator

from models.response_limits import response_size

Finite = Annotated[float, Field(allow_inf_nan=False)]
NativeId = Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]
Position = Annotated[list[Finite], Field(min_length=2, max_length=2)]
Reason = Literal[
    "invalid_scene_target",
    "unsupported_native_version",
    "screen_position_unavailable",
    "ugui_unavailable",
    "unsupported_native_shape",
    "missing_event_system",
    "ambiguous_event_system",
    "unsupported_event_system",
    "raycaster_limit",
    "registry_uninitialized",
    "unsupported_raycaster",
    "unsupported_display_route",
    "unsupported_blocking_objects",
    "unsupported_graphic",
    "max_nodes",
    "max_depth",
    "max_evaluations",
    "max_characters",
    "custom_filters_not_allowed",
    "invalid_metadata",
    "query_inventory_changed",
    "invalid_native_hit",
    "native_query_threw",
]
LIMITATIONS: Final = (
    "Current native cached raycast state; no layout, render, input or selection update is requested.",
    "Hits and module presence do not guarantee visibility, OS input or event receipt.",
    "Opted-in user filters can mutate state, dispatch events, throw or never return; no rollback, atomic snapshot or hard deadline is promised.",
)


class UnsupportedRaycastResponse(ValueError):
    def __init__(self) -> None:
        super().__init__("unsupported_raycast_response")


class Closed(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class CallbackPolicy(Closed):
    allow_custom_filters: StrictBool
    custom_filters_present: StrictBool | None
    native_query_attempted: StrictBool
    user_code_may_have_run: StrictBool
    callback_invocations: Literal["not_instrumented"]
    package_explicit_event_dispatch: StrictBool
    package_explicit_source_edits: StrictBool

    @model_validator(mode="after")
    def consistent(self) -> Self:
        expected = (
            self.native_query_attempted
            and self.allow_custom_filters
            and self.custom_filters_present is True
        )
        if (
            self.user_code_may_have_run != expected
            or self.package_explicit_event_dispatch
            or self.package_explicit_source_edits
            or (self.native_query_attempted and self.custom_filters_present is None)
            or (
                self.native_query_attempted
                and self.custom_filters_present
                and not self.allow_custom_filters
            )
        ):
            raise UnsupportedRaycastResponse()
        return self


class InputState(Closed):
    event_system_id: NativeId | None
    current_module_type: Annotated[str, Field(min_length=1, max_length=256)] | None
    current_module_active: StrictBool | None
    delivery: Literal["not_attempted"]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            self.event_system_id == 0
            or (self.event_system_id is None and self.current_module_type is not None)
            or (self.current_module_type is None) != (self.current_module_active is None)
            or (
                self.current_module_type is not None
                and len(self.current_module_type.encode("utf-16-le")) // 2 > 256
            )
        ):
            raise UnsupportedRaycastResponse()
        return self


class Hit(Closed):
    rank: Annotated[int, Field(ge=0, le=127)]
    game_object_id: NativeId
    name: Annotated[str, Field(max_length=256)]
    raycaster_id: NativeId
    root_raycaster_id: NativeId
    event_camera_id: NativeId | None
    sorting_layer_id: NativeId
    sorting_layer_value: NativeId
    sorting_order: NativeId
    depth: Annotated[int, Field(ge=0, le=2**31 - 1)]
    distance: Annotated[Finite, Field(ge=0)]
    index: Annotated[Finite, Field(ge=0)]
    display_index: Annotated[int, Field(ge=0, le=0)]
    screen_position: Position

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            not self.game_object_id
            or not self.raycaster_id
            or not self.root_raycaster_id
            or self.event_camera_id == 0
            or len(self.name.encode("utf-16-le")) // 2 > 256
        ):
            raise UnsupportedRaycastResponse()
        return self


class Budget(Closed):
    max_nodes: Annotated[int, Field(ge=1, le=1000)]
    max_depth: Annotated[int, Field(ge=0, le=24)]
    max_evaluations: Annotated[int, Field(ge=1, le=200000)]
    max_characters: Annotated[int, Field(ge=1, le=262144)]
    raycasters_inspected: Annotated[int, Field(ge=0, le=32)]
    graphics_inspected: Annotated[int, Field(ge=0, le=1000)]
    evaluations: Annotated[int, Field(ge=0, le=200000)]
    characters_reserved: Annotated[int, Field(ge=0, le=262144)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            self.graphics_inspected > self.max_nodes
            or self.evaluations < self.graphics_inspected + self.raycasters_inspected
            or self.evaluations > self.max_evaluations
            or self.characters_reserved > self.max_characters
        ):
            raise UnsupportedRaycastResponse()
        return self


class UnsupportedComponent(Closed):
    instance_id: Annotated[int, Field(strict=True, ge=-(2**31), le=2**31 - 1)]
    component_type: Annotated[str, Field(max_length=256)]
    path: Annotated[str, Field(max_length=256)]
    path_truncated: StrictBool


class RaycastEvidence(Closed):
    unsupported_component: UnsupportedComponent | None = None
    kind: Literal["ugui_raycast_diagnostics"]
    schema_version: Literal[1]
    framework: Literal["ugui"]
    mode: Literal["edit", "play"]
    frame: Annotated[int, Field(ge=0, le=2**31 - 1)]
    target_id: NativeId
    screen_position: Position
    coordinate_system: Literal["current_screen_pixels_bottom_left"]
    display: Annotated[int, Field(ge=0, le=0)]
    status: Literal["observed", "unknown", "failed"]
    reason: Reason | None
    policy: CallbackPolicy
    input: InputState
    hits: Annotated[list[Hit], Field(max_length=128)]
    hits_total: Annotated[int, Field(ge=0, le=2**31 - 1)] | None
    hits_truncated: StrictBool
    first_hit_targets_handler: StrictBool | None
    budget: Budget
    limitations: Annotated[list[str], Field(min_length=3, max_length=3)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.unsupported_component is not None and (
            self.reason != "unsupported_graphic" or self.budget.characters_reserved < 512
        ):
            raise UnsupportedRaycastResponse()
        if not self.target_id or self.limitations != list(LIMITATIONS):
            raise UnsupportedRaycastResponse()
        metadata = sum(len(hit.name.encode("utf-16-le")) // 2 for hit in self.hits)
        metadata += len((self.input.current_module_type or "").encode("utf-16-le")) // 2
        reserved_minimum = 256 * (len(self.hits) + (self.input.current_module_type is not None))
        if (
            metadata > self.budget.characters_reserved
            or reserved_minimum > self.budget.characters_reserved
        ):
            raise UnsupportedRaycastResponse()
        match self.status:
            case "observed":
                if (
                    self.reason is not None
                    or not self.policy.native_query_attempted
                    or self.input.event_system_id is None
                    or self.hits_total is None
                    or self.hits_total < len(self.hits)
                    or len(self.hits) != min(128, self.budget.graphics_inspected, self.hits_total)
                    or (
                        not self.policy.user_code_may_have_run
                        and self.hits_total > self.budget.graphics_inspected
                    )
                    or self.hits_truncated != (len(self.hits) < self.hits_total)
                    or [hit.rank for hit in self.hits] != list(range(len(self.hits)))
                    or (self.hits_total == 0 and self.first_hit_targets_handler is not False)
                    or (
                        not self.hits
                        and self.hits_total
                        and self.first_hit_targets_handler is not None
                    )
                ):
                    raise UnsupportedRaycastResponse()
            case "unknown" | "failed":
                if (
                    self.reason is None
                    or self.hits
                    or self.hits_total is not None
                    or self.hits_truncated
                    or self.first_hit_targets_handler is not None
                    or (self.status == "failed" and not self.policy.native_query_attempted)
                    or (self.reason == "native_query_threw" and self.status != "failed")
                ):
                    raise UnsupportedRaycastResponse()
                if self.reason == "custom_filters_not_allowed" and (
                    self.policy.allow_custom_filters
                    or self.policy.custom_filters_present is not True
                    or self.policy.native_query_attempted
                ):
                    raise UnsupportedRaycastResponse()
            case unreachable:
                assert_never(unreachable)
        return self


class RaycastReply(Closed):
    success: StrictBool
    message: Literal["raycast_evidence", "raycast_query_failed", "invalid_raycast_request"]
    data: RaycastEvidence | None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.data is None:
            if self.success or self.message != "invalid_raycast_request":
                raise UnsupportedRaycastResponse()
        elif self.success != (self.data.status != "failed") or self.message != (
            "raycast_query_failed" if self.data.status == "failed" else "raycast_evidence"
        ):
            raise UnsupportedRaycastResponse()
        return self


def validate_ugui_raycast_response(data: JsonValue) -> dict[str, JsonValue]:
    """Validate the full native envelope so failure-attempt evidence is not discarded."""
    try:
        if type(data) is not dict:
            raise UnsupportedRaycastResponse()
        native = data.get("data")
        if native is not None and (
            type(native) is not dict or type(native.get("schema_version")) is not int
        ):
            raise UnsupportedRaycastResponse()
        if response_size(data, max_bytes=2 * 1024 * 1024, max_depth=12, max_nodes=100000) is None:
            raise UnsupportedRaycastResponse()
        model = RaycastReply.model_validate_json(
            json.dumps(data, ensure_ascii=True, allow_nan=False)
        )
        return model.model_dump(mode="json", exclude_unset=True)
    except (ValueError, TypeError, OverflowError, UnicodeError):
        raise UnsupportedRaycastResponse() from None
