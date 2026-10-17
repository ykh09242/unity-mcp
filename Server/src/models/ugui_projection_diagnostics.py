"""Closed companion contract for current mono uGUI camera projection."""

import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from models.response_limits import response_size

Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Int32 = Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]
Count = Annotated[int, Field(ge=0, le=200000)]
Small = Annotated[str, Field(max_length=256)]
Vec2 = Annotated[list[Finite], Field(min_length=2, max_length=2)]
Vec3 = Annotated[list[Finite], Field(min_length=3, max_length=3)]
Rect = Annotated[list[Finite], Field(min_length=4, max_length=4)]
Matrix = Annotated[list[Finite], Field(min_length=16, max_length=16)]
Plane = Literal["left", "right", "bottom", "top", "near", "far"]
Reason = Literal["max_nodes", "max_depth", "max_evaluations", "max_characters"]


class Closed(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class Evidence(Closed):
    observation: Literal["current_camera_properties"]
    geometry_space: Literal["camera_viewport_normalized"]
    pixel_origin: Literal["bottom_left"]
    quad_source: Literal["current_world_corners"]
    layout_method: Literal["current_unforced"]
    matrix_order: Literal["column_major"]
    rasterization: Literal["not_observed"]
    mask_clipping: Literal["not_evaluated"]
    occlusion: Literal["not_evaluated"]
    interaction: Literal["not_evaluated"]
    profile: None


class EvaluationCounts(Closed):
    camera_observations: Annotated[int, Field(ge=0, le=1)]
    ancestor_steps: Annotated[int, Field(ge=0, le=128)]
    node_admissions: Annotated[int, Field(ge=0, le=1000)]
    child_fetches: Count
    world_corners: Count
    clip_distances: Count
    intersections: Count
    projected_vertices: Count


class Traversal(Closed):
    max_nodes: Annotated[int, Field(ge=1, le=1000)]
    max_depth: Annotated[int, Field(ge=0, le=24)]
    max_evaluations: Annotated[int, Field(ge=1, le=200000)]
    max_characters: Annotated[int, Field(ge=1, le=262144)]
    nodes_visited: Annotated[int, Field(ge=0, le=1000)]
    nodes_emitted: Annotated[int, Field(ge=0, le=1000)]
    evaluations: Count
    evaluation_counts: EvaluationCounts
    characters: Annotated[int, Field(ge=0, le=262144)]
    truncated: StrictBool
    reasons: list[Reason] = Field(max_length=4)


class Output(Closed):
    kind: Literal["display", "render_texture"]
    route_basis: Literal["camera_target_properties"]
    target_display: Annotated[int, Field(ge=0, le=7)]
    width: Annotated[int, Field(ge=0, le=2**31 - 1)] | None
    height: Annotated[int, Field(ge=0, le=2**31 - 1)] | None
    viewport_pixels: Rect | None
    camera_rect_ignored: StrictBool | None
    texture_id: Int32 | None
    active: StrictBool
    dimension_source: Literal[
        "unavailable", "attached_render_texture", "current_screen", "active_display_rendering"
    ]
    pixel_status: Literal["available", "unavailable"]
    texture_dimension: (
        Literal["Unknown", "None", "Any", "Tex2D", "Tex3D", "Cube", "Tex2DArray", "CubeArray"]
        | None
    )
    volume_depth: Annotated[int, Field(ge=0, le=2**31 - 1)] | None
    dynamic_scale: StrictBool | None


class Context(Closed):
    target_id: Int32
    canvas_id: Int32
    root_canvas_id: Int32
    render_mode: Literal["world_space", "screen_space_camera"]
    camera_id: Int32
    assigned_camera_id: Int32 | None
    camera_selection: Literal["explicit_world_view", "assigned_screen_camera"]
    camera_enabled: StrictBool
    camera_active: StrictBool
    canvas_enabled: StrictBool
    culling_mask: Int32
    orthographic: StrictBool
    stereo_enabled: StrictBool
    allow_dynamic_resolution: StrictBool
    non_jittered_projection_mismatch: StrictBool
    near_clip: Finite
    far_clip: Finite
    scale_factor: Finite
    reference_pixels_per_unit: Finite
    view_matrix: Matrix
    projection_matrix: Matrix
    camera_rect: Rect | None
    camera_pixel_rect: Rect | None
    pixel_width: Annotated[int, Field(ge=0, le=2**31 - 1)]
    pixel_height: Annotated[int, Field(ge=0, le=2**31 - 1)]
    scaled_pixel_width: Annotated[int, Field(ge=0, le=2**31 - 1)]
    scaled_pixel_height: Annotated[int, Field(ge=0, le=2**31 - 1)]
    target_name: Small
    canvas_name: Small
    camera_name: Small
    metadata_truncated_fields: list[Literal["target_name", "canvas_name", "camera_name"]] = Field(
        max_length=3
    )
    output: Output


class Node(Closed):
    diagnostic_path: Annotated[str, Field(max_length=512)]
    parent_index: Annotated[int, Field(ge=0, le=999)] | None
    sibling_index: Annotated[int, Field(ge=0, le=2**31 - 1)]
    instance_id: Int32
    name: Small
    truncated_fields: list[Literal["name"]] = Field(max_length=1)
    active: StrictBool
    layer_in_culling_mask: StrictBool
    world_corners: Annotated[list[Vec3], Field(min_length=4, max_length=4)] | None
    depth_range: Vec2 | None
    camera_plane_relation: Literal["unknown", "in_front", "behind", "crossing"]
    frustum_relation: Literal["unknown", "inside", "partial", "outside", "degenerate"]
    cut_planes: list[Plane] = Field(max_length=6)
    rejected_by: Plane | None
    clipped_viewport_polygon: list[Vec2] = Field(max_length=10)
    unclipped_viewport_bounds: Rect | None
    clipped_target_pixel_bounds: Rect | None
    pixel_status: Literal["available", "not_applicable", "unavailable"]
    boundary_clamped: StrictBool
    reasons: list[
        Literal[
            "canvas_context_changed",
            "budget_incomplete",
            "nonfinite_geometry",
            "unsupported_view_matrix",
            "indeterminate_intersection",
            "unsupported_clip_geometry",
            "unsafe_projection",
        ]
    ] = Field(max_length=1)


def _units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _matrix_valid(value: list[float], row: tuple[int, int, int, int]) -> bool:
    if any(abs(value[index] - expected) > 1e-6 for index, expected in zip((3, 7, 11, 15), row)):
        return False
    rows = [[value[r + c * 4] for c in range(4)] for r in range(4)]
    determinant = 1.0
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r, column=col: abs(rows[r][column]))
        if rows[pivot][col] == 0:
            return False
        if pivot != col:
            rows[col], rows[pivot] = rows[pivot], rows[col]
            determinant = -determinant
        divisor = rows[col][col]
        determinant *= divisor
        for r in range(col + 1, 4):
            ratio = rows[r][col] / divisor
            for c in range(col + 1, 4):
                rows[r][c] -= ratio * rows[col][c]
    return math.isfinite(determinant) and abs(determinant) > 1e-12


def _bounds(points: list[list[float]]) -> list[float]:
    x, y = min(p[0] for p in points), min(p[1] for p in points)
    return [x, y, max(p[0] for p in points) - x, max(p[1] for p in points) - y]


def _same_rect(left: list[float], right: list[float]) -> bool:
    return all(math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-6) for a, b in zip(left, right))


class UguiProjectionDiagnosticsV1(Closed):
    """Current observed geometry, independent of semantic snapshot versions."""

    kind: Literal["ugui_projection_diagnostics"]
    schema_version: Annotated[int, Field(ge=1, le=1)]
    framework: Literal["ugui"]
    mode: Literal["edit", "play"]
    frame: Int32
    status: Literal["complete", "partial", "unavailable"]
    unavailable_reason: (
        Literal["canvas_unavailable", "camera_unassigned", "unsupported_camera_projection"] | None
    )
    evidence: Evidence
    context: Context | None
    nodes: list[Node] = Field(max_length=1000)
    traversal: Traversal
    limitations: list[Annotated[str, Field(max_length=512)]] = Field(max_length=8)

    @model_validator(mode="after")
    def consistent(self) -> "UguiProjectionDiagnosticsV1":
        t, ctx = self.traversal, self.context
        counts = t.evaluation_counts
        if (
            len(self.nodes) != t.nodes_emitted
            or t.nodes_emitted > t.nodes_visited
            or t.nodes_visited > t.max_nodes
            or counts.node_admissions != t.nodes_visited
            or t.evaluations != sum(counts.model_dump().values())
            or t.evaluations > t.max_evaluations
            or counts.child_fetches < max(0, t.nodes_visited - 1)
            or counts.world_corners % 4
            or t.characters > t.max_characters
            or len(set(t.reasons)) != len(t.reasons)
        ):
            raise ValueError("inconsistent work")
        if self.status == "unavailable":
            if ctx is not None or self.nodes or self.unavailable_reason is None or t.truncated:
                raise ValueError("invalid unavailable evidence")
        elif self.unavailable_reason is not None or (
            ctx is None and (self.status != "partial" or not t.truncated or self.nodes)
        ):
            raise ValueError("invalid context")
        characters = 0
        fields_truncated = False
        if ctx is not None:
            if (
                ctx.stereo_enabled
                or ctx.canvas_id != ctx.root_canvas_id
                or not ctx.canvas_enabled
                or ctx.near_clip <= 0
                or ctx.far_clip <= ctx.near_clip
                or ctx.scale_factor <= 0
                or ctx.reference_pixels_per_unit <= 0
                or not _matrix_valid(ctx.view_matrix, (0, 0, 0, 1))
                or not _matrix_valid(
                    ctx.projection_matrix,
                    (0, 0, 0 if ctx.orthographic else -1, 1 if ctx.orthographic else 0),
                )
                or (
                    ctx.render_mode == "screen_space_camera"
                    and (
                        ctx.camera_selection != "assigned_screen_camera"
                        or ctx.assigned_camera_id != ctx.camera_id
                    )
                )
                or (
                    ctx.render_mode == "world_space"
                    and ctx.camera_selection != "explicit_world_view"
                )
            ):
                raise ValueError("invalid camera context")
            if len(set(ctx.metadata_truncated_fields)) != len(ctx.metadata_truncated_fields):
                raise ValueError("duplicate fields")
            for value in (ctx.target_name, ctx.canvas_name, ctx.camera_name):
                length = _units(value)
                if length > 256:
                    raise ValueError("long metadata")
                characters += length
            fields_truncated |= bool(ctx.metadata_truncated_fields)
            o = ctx.output
            if o.kind == "render_texture":
                if (
                    o.camera_rect_ignored is not (True if ctx.camera_rect == [0, 0, 1, 1] else None)
                    or o.texture_id is None
                    or o.texture_dimension is None
                    or o.volume_depth is None
                    or o.dynamic_scale is None
                    or o.dimension_source != "attached_render_texture"
                ):
                    raise ValueError("invalid texture route")
            elif (
                o.camera_rect_ignored is not False
                or o.texture_id is not None
                or o.texture_dimension is not None
                or o.volume_depth is not None
                or o.dynamic_scale is not None
            ):
                raise ValueError("invalid display route")
            if o.pixel_status == "available":
                if (
                    not o.active
                    or o.width is None
                    or o.height is None
                    or not 1 <= o.width <= 16384
                    or not 1 <= o.height <= 16384
                    or o.viewport_pixels is None
                    or o.viewport_pixels[2] <= 0
                    or o.viewport_pixels[3] <= 0
                    or o.viewport_pixels[0] < 0
                    or o.viewport_pixels[1] < 0
                    or o.viewport_pixels[0] + o.viewport_pixels[2] > o.width
                    or o.viewport_pixels[1] + o.viewport_pixels[3] > o.height
                    or ctx.allow_dynamic_resolution
                    or ctx.non_jittered_projection_mismatch
                    or ctx.pixel_width != ctx.scaled_pixel_width
                    or ctx.pixel_height != ctx.scaled_pixel_height
                ):
                    raise ValueError("unsafe pixel route")
                if o.kind == "render_texture" and (
                    ctx.camera_rect != [0, 0, 1, 1]
                    or o.camera_rect_ignored is not True
                    or o.texture_dimension != "Tex2D"
                    or o.volume_depth != 1
                    or o.dynamic_scale
                    or o.viewport_pixels != [0, 0, o.width, o.height]
                ):
                    raise ValueError("unsafe texture mapping")
                if o.kind == "display" and (
                    o.dimension_source
                    != ("current_screen" if o.target_display == 0 else "active_display_rendering")
                    or o.viewport_pixels != ctx.camera_pixel_rect
                ):
                    raise ValueError("unsafe display mapping")
            elif o.viewport_pixels is not None:
                raise ValueError("unavailable pixels")
            if self.status == "complete" and o.pixel_status != "available":
                raise ValueError("unknown complete route")
        paths, identities = {}, set()
        observed = 0
        for index, n in enumerate(self.nodes):
            path = n.diagnostic_path
            if (
                not re.fullmatch(r"(?:/(?:0|[1-9][0-9]{0,9}))*", path)
                or any(int(part) > 2**31 - 1 for part in path.split("/")[1:])
                or path.count("/") > t.max_depth
                or path in paths
                or n.instance_id in identities
                or (
                    index == 0
                    and (path != "" or n.parent_index is not None or n.sibling_index != 0)
                )
                or (
                    index > 0
                    and (
                        n.parent_index is None
                        or n.parent_index >= index
                        or not path.startswith(self.nodes[n.parent_index].diagnostic_path + "/")
                        or n.sibling_index != int(path.rsplit("/", 1)[1])
                    )
                )
            ):
                raise ValueError("invalid hierarchy")
            if index == 0:
                if ctx is None or n.instance_id != ctx.target_id:
                    raise ValueError("root target mismatch")
            else:
                ancestor = path.rsplit("/", 1)[0]
                while ancestor not in paths:
                    ancestor = ancestor.rsplit("/", 1)[0]
                if n.parent_index != paths[ancestor]:
                    raise ValueError("nearest emitted parent mismatch")
            paths[path] = index
            identities.add(n.instance_id)
            length = _units(n.name)
            if length > 256:
                raise ValueError("long name")
            characters += _units(path) + length
            fields_truncated |= bool(n.truncated_fields)
            if len(set(n.cut_planes)) != len(n.cut_planes):
                raise ValueError("duplicate planes")
            if any(
                r is not None and (r[2] < 0 or r[3] < 0)
                for r in (n.unclipped_viewport_bounds, n.clipped_target_pixel_bounds)
            ):
                raise ValueError("invalid bounds")
            if n.world_corners is None:
                if (
                    n.depth_range is not None
                    or n.camera_plane_relation != "unknown"
                    or n.frustum_relation != "unknown"
                ):
                    raise ValueError("missing world geometry")
            else:
                observed += 1
                if n.depth_range is None or n.depth_range[0] > n.depth_range[1]:
                    raise ValueError("invalid depths")
                relation = (
                    "behind"
                    if n.depth_range[1] <= 0
                    else "in_front"
                    if n.depth_range[0] > 0
                    else "crossing"
                )
                if n.camera_plane_relation != relation:
                    raise ValueError("invalid camera side")
            if n.camera_plane_relation != "in_front" and n.unclipped_viewport_bounds is not None:
                raise ValueError("mirrored bounds")
            polygon = n.clipped_viewport_polygon
            if n.frustum_relation in ("inside", "partial"):
                if n.camera_plane_relation == "behind":
                    raise ValueError("behind camera survivor")
                if (
                    not 3 <= len(polygon) <= 10
                    or any(not 0 <= v <= 1 for point in polygon for v in point)
                    or n.rejected_by is not None
                    or len({tuple(p) for p in polygon}) != len(polygon)
                    or (n.frustum_relation == "inside" and (n.cut_planes or len(polygon) != 4))
                    or (n.frustum_relation == "partial" and not n.cut_planes)
                ):
                    raise ValueError("invalid surviving polygon")
                area, turns = 0.0, set()
                for i, a in enumerate(polygon):
                    b, c = polygon[(i + 1) % len(polygon)], polygon[(i + 2) % len(polygon)]
                    area += a[0] * b[1] - b[0] * a[1]
                    turn = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
                    if abs(turn) > 1e-12:
                        turns.add(turn > 0)
                if abs(area) * 0.5 <= 1e-12 or len(turns) > 1:
                    raise ValueError("nonconvex polygon")
                if ctx is None:
                    raise ValueError("missing camera")
                if ctx.output.pixel_status == "available":
                    viewport = ctx.output.viewport_pixels
                    expected = _bounds(
                        [
                            [viewport[0] + v[0] * viewport[2], viewport[1] + v[1] * viewport[3]]
                            for v in polygon
                        ]
                    )
                    if (
                        n.pixel_status != "available"
                        or n.clipped_target_pixel_bounds is None
                        or not _same_rect(expected, n.clipped_target_pixel_bounds)
                    ):
                        raise ValueError("inconsistent pixels")
                elif n.pixel_status != "unavailable" or n.clipped_target_pixel_bounds is not None:
                    raise ValueError("unknown target pixels")
            else:
                if (
                    polygon
                    or n.clipped_target_pixel_bounds is not None
                    or n.pixel_status == "available"
                ):
                    raise ValueError("invalid empty geometry")
                if n.frustum_relation == "outside":
                    if n.rejected_by is None or n.rejected_by not in n.cut_planes:
                        raise ValueError("missing rejection")
                elif n.rejected_by is not None:
                    raise ValueError("spurious rejection")
                if n.frustum_relation == "unknown" and (
                    not n.reasons or n.pixel_status != "unavailable"
                ):
                    raise ValueError("missing unknown cause")
            if self.status == "complete" and (
                n.frustum_relation == "unknown"
                or n.camera_plane_relation == "unknown"
                or n.pixel_status == "unavailable"
                or n.reasons
            ):
                raise ValueError("unknown complete node")
        if (
            observed * 4 > counts.world_corners
            or characters != t.characters
            or t.truncated != bool(t.reasons or fields_truncated)
            or (self.status == "complete" and (t.truncated or not self.nodes))
        ):
            raise ValueError("inconsistent retained evidence")
        return self


def validate_ugui_projection_diagnostics(data: object) -> dict:
    """Bound and validate without exposing rejected peer fields or error locations."""
    try:
        if (
            type(data) is not dict
            or response_size(data, max_bytes=2 * 1024 * 1024, max_depth=16, max_nodes=100000)
            is None
        ):
            raise ValueError("unsupported")
        model = UguiProjectionDiagnosticsV1.model_validate_json(
            json.dumps(data, ensure_ascii=True, allow_nan=False)
        )
        return model.model_dump(mode="json")
    except (ValueError, TypeError, OverflowError, UnicodeError) as error:
        raise ValueError("unsupported_projection_response") from error
