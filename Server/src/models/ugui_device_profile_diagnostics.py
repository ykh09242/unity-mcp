"""Closed device-profile request and native evidence contracts."""

import json
import math
import struct
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    TypeAdapter,
    field_validator,
    model_validator,
)

from models.ugui_projection_diagnostics import UguiProjectionDiagnosticsV1
from models.response_limits import response_size

Dimension = Annotated[int, Field(strict=True, ge=64, le=8192)]
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Positive = Annotated[Finite, Field(gt=0)]
Orientation = Literal["portrait", "portrait_upside_down", "landscape_left", "landscape_right"]
Rect = Annotated[list[Finite], Field(min_length=4, max_length=4)]


def unity_float(value: float) -> float:
    """Narrow JSON numbers before domain checks, matching Unity Single arithmetic."""
    try:
        narrowed = struct.unpack("!f", struct.pack("!f", value))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError("invalid_unity_float") from error
    if not math.isfinite(narrowed):
        raise ValueError("invalid_unity_float")
    return narrowed


def positive_unity_float(value: float) -> float:
    """Reject zero after narrowing, including positive double underflow."""
    value = unity_float(value)
    if value <= 0:
        raise ValueError("invalid_positive_unity_float")
    return value


class Closed(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class VirtualProfile(Closed):
    source: Literal["virtual"]
    width: Dimension
    height: Dimension
    dpi: Positive
    safe_area: Rect
    orientation: Orientation

    @field_validator("dpi")
    @classmethod
    def dpi_in_unity_domain(cls, value: float) -> float:
        value = positive_unity_float(value)
        positive_unity_float(25.4 / value)
        return value

    @model_validator(mode="after")
    def contained_safe_area(self) -> Self:
        x, y, width, height = [unity_float(value) for value in self.safe_area]
        xmax, ymax = unity_float(x + width), unity_float(y + height)
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("invalid_safe_area")
        if xmax > self.width or ymax > self.height:
            raise ValueError("invalid_safe_area")
        return self


class ObservedProfile(Closed):
    source: Literal["observed"]


DeviceProfile = Annotated[VirtualProfile | ObservedProfile, Field(discriminator="source")]
DeviceProfiles = Annotated[list[DeviceProfile], Field(min_length=1, max_length=8)]
DEVICE_PROFILES: Final = TypeAdapter(DeviceProfiles)


class Assessment(Closed):
    instance_id: Annotated[int, Field(strict=True)]
    pixel_bounds: Rect
    raycast_rectangle_candidate: Annotated[bool, Field(strict=True)]
    width_mm: Positive | None
    height_mm: Positive | None
    unsafe_region_area_px2: Annotated[Finite, Field(ge=0)] | None
    safe_area_relation: Literal["unknown", "possible_unsafe_region", "inside_geometric_safe_area"]
    touch_target: Literal["unknown", "below_caller_threshold", "meets_caller_threshold"]

    @field_validator("pixel_bounds")
    @classmethod
    def unity_bounds(cls, value: list[float]) -> list[float]:
        return [unity_float(coordinate) for coordinate in value]


class ProfileReport(Closed):
    source: Literal["virtual", "observed"]
    width: Annotated[int, Field(strict=True, ge=0)]
    height: Annotated[int, Field(strict=True, ge=0)]
    dpi: Positive | None
    dpi_source: Literal["request", "screen", "unavailable"]
    safe_area: Rect | None
    orientation: Orientation | None
    route: Literal["owned_overlay_preview", "current_camera_geometry"]
    status: Literal["complete", "partial", "unavailable"]
    reasons: list[
        Literal[
            "observed_profile_unavailable",
            "invalid_scaler_math",
            "budget_incomplete",
            "unsupported_layout_provider",
            "incomplete_preview",
            "camera_geometry_unavailable",
            "pixels_unavailable",
            "camera_resolution_resimulation_unsupported",
            "projection_incomplete",
            "nonfinite_geometry",
        ]
    ]
    scale_factor: Positive | None
    reference_pixels_per_unit: Positive | None
    layout_dpi_source: Literal["not_used", "request", "screen", "canvas_scaler_fallback"]
    nodes: Annotated[list[Assessment], Field(max_length=1000)]

    @field_validator("dpi", "scale_factor", "reference_pixels_per_unit")
    @classmethod
    def unity_positive(cls, value: float | None) -> float | None:
        return None if value is None else positive_unity_float(value)

    @field_validator("safe_area")
    @classmethod
    def unity_safe_area(cls, value: list[float] | None) -> list[float] | None:
        return None if value is None else [unity_float(coordinate) for coordinate in value]


class Policy(Closed):
    pixel_origin: Literal["bottom_left"]
    orientation: Literal["supplied_frame_no_rotation"]
    layout: Literal["owned_builtin_preview_or_current_camera"]
    custom_callbacks: Literal["not_invoked"]
    measurement: Literal["dpi_geometry_estimate"]
    interaction: Literal["not_observed"]
    rendering: Literal["not_observed"]


Count = Annotated[int, Field(strict=True, ge=0, le=200000)]


class Counts(Closed):
    snapshot_and_resolver: Count
    ancestors: Count
    transforms: Count
    components: Count
    profile_setups: Count
    assessment_pairs: Count
    projection: Count
    output: Count


class Budget(Closed):
    max_nodes: Annotated[int, Field(strict=True, ge=1, le=1000)]
    max_depth: Annotated[int, Field(strict=True, ge=0, le=24)]
    max_evaluations: Annotated[int, Field(strict=True, ge=1, le=200000)]
    max_characters: Annotated[int, Field(strict=True, ge=1, le=262144)]
    evaluations: Count
    characters: Annotated[int, Field(strict=True, ge=0, le=262144)]
    evaluation_counts: Counts
    reasons: list[
        Literal[
            "max_evaluations",
            "ancestor_limit",
            "max_nodes",
            "max_depth",
            "target_outside_preview",
            "projection_incomplete",
            "max_characters",
            "profiles_incomplete",
            "selector_limit",
        ]
    ]


class UnsupportedComponent(Closed):
    instance_id: Annotated[int, Field(strict=True, ge=-(2**31), le=2**31 - 1)]
    component_type: Annotated[str, Field(max_length=256)]
    path: Annotated[str, Field(max_length=256)]
    path_truncated: StrictBool


class DeviceProfileReport(Closed):
    unsupported_component: UnsupportedComponent | None = None
    kind: Literal["ugui_device_profile_diagnostics"]
    schema_version: Literal[1]
    frame: Annotated[int, Field(strict=True, ge=0)]
    status: Literal["complete", "partial"]
    minimum_touch_target_mm: Positive | None
    policy: Policy
    profiles: Annotated[list[ProfileReport], Field(max_length=8)]
    projection: UguiProjectionDiagnosticsV1 | None
    budget: Budget
    limitations: Annotated[list[str], Field(max_length=8)]

    @field_validator("minimum_touch_target_mm")
    @classmethod
    def unity_threshold(cls, value: float | None) -> float | None:
        return None if value is None else positive_unity_float(value)

    @model_validator(mode="after")
    def coherent_evidence(self) -> Self:
        """Refuse invented pass findings and inconsistent peer budget/geometry claims."""
        b = self.budget
        if b.evaluations != sum(b.evaluation_counts.model_dump().values()):
            raise ValueError("work_accounting")
        if b.evaluations > b.max_evaluations or b.characters > b.max_characters:
            raise ValueError("budget_exceeded")
        if self.status == "complete" and (b.reasons or not self.profiles):
            raise ValueError("incomplete_complete_report")
        if self.unsupported_component is not None and any(
            profile.status == "complete" or "unsupported_layout_provider" not in profile.reasons
            for profile in self.profiles
        ):
            raise ValueError("unsupported_component_pass")
        retained_nodes = sum(len(profile.nodes) for profile in self.profiles)
        if b.evaluation_counts.profile_setups < len(self.profiles):
            raise ValueError("profile_accounting")
        if b.evaluation_counts.assessment_pairs < retained_nodes:
            raise ValueError("assessment_accounting")
        if b.evaluation_counts.output != retained_nodes + int(self.projection is not None) + int(
            self.unsupported_component is not None
        ):
            raise ValueError("output_accounting")
        if self.projection is not None and (
            b.evaluation_counts.projection != self.projection.traversal.evaluations
        ):
            raise ValueError("projection_accounting")
        for profile in self.profiles:
            if self.status == "complete" and profile.status != "complete":
                raise ValueError("incomplete_complete_profile")
            if profile.status == "unavailable" and (profile.nodes or not profile.reasons):
                raise ValueError("unavailable_geometry")
            if profile.status == "complete" and (profile.reasons or not profile.nodes):
                raise ValueError("unsupported_complete_profile")
            expected_dpi_source = "request" if profile.source == "virtual" else "screen"
            if profile.dpi_source != (expected_dpi_source if profile.dpi else "unavailable"):
                raise ValueError("dpi_provenance")
            if profile.safe_area is not None:
                x, y, width, height = profile.safe_area
                if x < 0 or y < 0 or width <= 0 or height <= 0:
                    raise ValueError("invalid_safe_area")
                if (
                    unity_float(x + width) > profile.width
                    or unity_float(y + height) > profile.height
                ):
                    raise ValueError("invalid_safe_area")
            if profile.nodes and (profile.safe_area is None or profile.orientation is None):
                raise ValueError("missing_profile_coordinates")
            if profile.route == "current_camera_geometry" and profile.nodes:
                projection = self.projection
                if projection is None or projection.context is None:
                    raise ValueError("missing_projection")
                output = projection.context.output
                if output.width != profile.width or output.height != profile.height:
                    raise ValueError("camera_resolution")
                if output.pixel_status != "available":
                    raise ValueError("missing_pixels")
                if profile.source == "observed" and (
                    output.kind != "display"
                    or output.target_display != 0
                    or output.dimension_source != "current_screen"
                ):
                    raise ValueError("observed_route")
            for node in profile.nodes:
                x, y, width, height = node.pixel_bounds
                if width <= 0 or height <= 0 or not math.isfinite(x + width + y + height):
                    raise ValueError("invalid_bounds")
                if (node.width_mm is None) != (node.height_mm is None):
                    raise ValueError("incomplete_mm")
                if node.width_mm is not None:
                    if profile.dpi is None:
                        raise ValueError("missing_dpi")
                    if not math.isclose(node.width_mm, width / profile.dpi * 25.4, rel_tol=1e-6):
                        raise ValueError("incorrect_width_mm")
                    if not math.isclose(node.height_mm, height / profile.dpi * 25.4, rel_tol=1e-6):
                        raise ValueError("incorrect_height_mm")
                if "unsupported_layout_provider" in profile.reasons and (
                    node.width_mm is not None
                    or node.safe_area_relation != "unknown"
                    or node.touch_target != "unknown"
                ):
                    raise ValueError("unsupported_layout_pass")
                if node.safe_area_relation != "unknown":
                    sx, sy, sw, sh = profile.safe_area
                    # Rect endpoints and Math.Min/Max/subtraction are Single operations natively.
                    xmax, ymax = unity_float(x + width), unity_float(y + height)
                    sxmax, symax = unity_float(sx + sw), unity_float(sy + sh)
                    iw = max(0, unity_float(min(xmax, sxmax) - max(x, sx)))
                    ih = max(0, unity_float(min(ymax, symax) - max(y, sy)))
                    intersection = iw * ih
                    outside = max(0, width * height - intersection)
                    if node.unsafe_region_area_px2 is None or not math.isclose(
                        outside, node.unsafe_region_area_px2, rel_tol=1e-5, abs_tol=1e-4
                    ):
                        raise ValueError("incorrect_safe_intersection")
                    expected = (
                        "possible_unsafe_region" if outside > 0 else "inside_geometric_safe_area"
                    )
                    if node.safe_area_relation != expected:
                        raise ValueError("incorrect_safe_relation")
                elif node.unsafe_region_area_px2 is not None:
                    raise ValueError("unknown_area_claim")
                if node.touch_target != "unknown":
                    if not node.raycast_rectangle_candidate or self.minimum_touch_target_mm is None:
                        raise ValueError("missing_touch_threshold")
                    if node.width_mm is None or node.height_mm is None:
                        raise ValueError("missing_touch_dimensions")
                    below = min(node.width_mm, node.height_mm) < self.minimum_touch_target_mm
                    if (node.touch_target == "below_caller_threshold") != below:
                        raise ValueError("incorrect_touch_finding")
        return self


def validate_ugui_device_profile_diagnostics(data: JsonValue) -> dict[str, JsonValue]:
    """Bound the untrusted tree before strict parsing; expose no rejected peer fields."""
    try:
        if (
            type(data) is not dict
            or response_size(data, max_bytes=2 * 1024 * 1024, max_depth=20, max_nodes=150000)
            is None
        ):
            raise ValueError("unsupported")
        model = DeviceProfileReport.model_validate_json(
            json.dumps(data, ensure_ascii=True, allow_nan=False)
        )
        return model.model_dump(mode="json", exclude_unset=True)
    except (ValueError, TypeError, OverflowError, UnicodeError) as error:
        raise ValueError("unsupported_device_profile_response") from error
