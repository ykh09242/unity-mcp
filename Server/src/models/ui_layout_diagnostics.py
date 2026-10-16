"""Closed producer contract for current-panel UI Toolkit diagnostics."""

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from models.response_limits import response_size


Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, le=20000)]
Rect = Annotated[list[Finite], Field(min_length=4, max_length=4)]
Small = Annotated[str, Field(max_length=256)]
Reason = Literal[
    "max_depth",
    "ancestor_depth",
    "max_nodes",
    "max_evaluations",
    "max_characters",
    "max_pairs",
    "max_findings",
    "per_string_limit",
]


class Closed(BaseModel):
    """Reject coercion and undeclared evidence fields."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class Evidence(Closed):
    path_domain: Literal["physical_indices"]
    layout_method: Literal["current_observation"]
    clip_method: Literal["rectangular_engine_style_v1"]
    overlap_method: Literal["sibling_aabb_candidates"]
    flat_panel: StrictBool | None


class Traversal(Closed):
    max_depth: Annotated[int, Field(ge=0, le=24)]
    max_nodes: Annotated[int, Field(ge=1, le=1000)]
    max_evaluations: Annotated[int, Field(ge=1, le=20000)]
    max_characters: Annotated[int, Field(ge=1, le=262144)]
    max_pairs: Annotated[int, Field(ge=0, le=20000)]
    max_findings: Annotated[int, Field(ge=1, le=2000)]
    returned_nodes: Annotated[int, Field(ge=0, le=1000)]
    evaluations: Count
    characters: Annotated[int, Field(ge=0, le=262144)]
    ancestor_inspections: Annotated[int, Field(ge=0, le=127)]
    node_admissions: Annotated[int, Field(ge=0, le=1000)]
    child_fetches: Count
    style_probes: Count
    pairs_considered: Count
    findings_retained: Annotated[int, Field(ge=0, le=2000)]
    complete: StrictBool
    truncated: StrictBool
    reasons: list[Reason] = Field(max_length=8)


class Stages(Closed):
    clipping: Literal["complete", "partial", "unavailable"]
    overlap: Literal["complete", "truncated", "disabled", "unavailable"]


class Node(Closed):
    diagnostic_path: Annotated[str, Field(max_length=512)]
    parent_index: Annotated[int, Field(ge=0, le=999)] | None
    sibling_index: Annotated[int, Field(ge=0, le=2147483647)]
    name: Small
    type: Small
    truncated_fields: list[Literal["name", "type"]] = Field(max_length=2)
    layout_status: Literal["current", "empty", "not_applicable_display_none", "unknown"]
    bounds: Rect | None
    displayed: StrictBool | None
    visibility: Literal["visible", "hidden", "unknown"]
    effective_opacity: Annotated[Finite, Field(ge=0, le=1)] | None
    suppression: list[
        Literal["display_none", "visibility_hidden", "opacity_zero", "empty_geometry"]
    ] = Field(max_length=4)
    overflow: Literal["visible", "hidden", "scroll", "unknown"]
    clips_children: StrictBool | None
    clipping: Literal["not_clipped", "partial", "full", "unknown", "not_applicable"]
    effective_clip: Rect | None


class Finding(Closed):
    code: Literal["outside_parent_content", "clipped_partial", "clipped_full", "overlap_candidate"]
    severity: Literal["candidate"]
    node_index: Annotated[int, Field(ge=0, le=999)]
    other_index: Annotated[int, Field(ge=0, le=999)] | None
    intersection: Rect | None
    clipping_unknown: StrictBool


def _units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


class UiLayoutDiagnosticsV1(Closed):
    """A diagnostic scan is separate from versioned semantic snapshots."""

    kind: Literal["uitoolkit_layout_diagnostics"]
    schema_version: Annotated[int, Field(ge=1, le=1)]
    framework: Literal["uitoolkit"]
    mode: Literal["edit", "play"]
    component_type: Literal["ui_document", "panel_renderer"]
    component_instance_id: Annotated[int, Field(ge=-2147483648, le=2147483647)]
    target_instance_id: Annotated[int, Field(ge=-2147483648, le=2147483647)]
    game_object: Small
    metadata_truncated_fields: list[Literal["game_object"]] = Field(max_length=1)
    geometry_space: Literal["panel_aabb"]
    evidence: Evidence
    layout_status: Literal[
        "current",
        "not_initialized",
        "panel_unavailable",
        "component_inactive",
        "layout_unavailable",
    ]
    status: Literal["complete", "partial", "unavailable"]
    nodes: list[Node] = Field(max_length=1000)
    findings: list[Finding] = Field(max_length=2000)
    traversal: Traversal
    stages: Stages
    limitations: list[Annotated[str, Field(max_length=512)]] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def consistent(self) -> "UiLayoutDiagnosticsV1":
        """Counters and references cannot overstate bounded producer evidence."""
        t = self.traversal
        if (
            t.returned_nodes != len(self.nodes)
            or t.node_admissions != len(self.nodes)
            or len(self.nodes) > t.max_nodes
            or t.findings_retained != len(self.findings)
            or len(self.findings) > t.max_findings
            or t.evaluations > t.max_evaluations
            or t.pairs_considered > t.max_pairs
            or t.characters > t.max_characters
            or t.evaluations
            != t.ancestor_inspections
            + t.node_admissions
            + t.child_fetches
            + t.style_probes
            + t.pairs_considered
            or len(set(t.reasons)) != len(t.reasons)
            or t.truncated != any(r != "per_string_limit" for r in t.reasons)
        ):
            raise ValueError("inconsistent scan")
        unavailable = self.layout_status != "current"
        if (self.status == "unavailable") != unavailable:
            raise ValueError("inconsistent readiness")
        if unavailable and (
            self.nodes
            or self.findings
            or t.complete
            or self.stages.clipping != "unavailable"
            or self.stages.overlap != "unavailable"
        ):
            raise ValueError("unavailable scan")
        if t.complete != (not unavailable and not t.reasons):
            raise ValueError("inconsistent completeness")
        spatial_complete = (
            t.complete and self.stages.clipping == "complete" and self.stages.overlap == "complete"
        )
        if not unavailable and ((self.status == "complete") != spatial_complete):
            raise ValueError("unsupported complete assertion")
        if self.stages.overlap == "disabled" and t.max_pairs != 0:
            raise ValueError("inconsistent disabled stage")
        if self.status == "complete" and (
            self.evidence.flat_panel is not True
            or any(
                n.clipping == "unknown"
                or n.layout_status == "unknown"
                or n.displayed is None
                or n.visibility == "unknown"
                or n.effective_opacity is None
                or n.overflow == "unknown"
                or n.clips_children is None
                or (n.displayed and n.bounds is None)
                for n in self.nodes
            )
        ):
            raise ValueError("unknown spatial evidence")
        if _units(self.game_object) > 256:
            raise ValueError("unbounded metadata")
        if self.status == "complete" and not self.nodes:
            raise ValueError("missing complete root")
        characters = _units(self.game_object)
        slots = set()
        for index, node in enumerate(self.nodes):
            characters += sum(_units(v) for v in (node.diagnostic_path, node.name, node.type))
            if _units(node.name) > 256 or _units(node.type) > 256:
                raise ValueError("unbounded metadata")
            if index == 0:
                if (
                    node.parent_index is not None
                    or node.diagnostic_path != ""
                    or node.sibling_index != 0
                ):
                    raise ValueError("invalid root")
            else:
                if node.parent_index is None or node.parent_index >= index:
                    raise ValueError("invalid parent")
                parent = self.nodes[node.parent_index]
                if node.diagnostic_path != parent.diagnostic_path + "/" + str(node.sibling_index):
                    raise ValueError("invalid path")
            if (
                not re.fullmatch(r"(?:/(?:0|[1-9][0-9]{0,9}))*", node.diagnostic_path)
                or node.diagnostic_path.count("/") > t.max_depth
            ):
                raise ValueError("invalid physical path")
            slot = (node.parent_index, node.sibling_index)
            if slot in slots:
                raise ValueError("duplicate sibling")
            slots.add(slot)
            if any(
                rect is not None and (rect[2] < 0 or rect[3] < 0)
                for rect in (node.bounds, node.effective_clip)
            ):
                raise ValueError("invalid rectangle")
        if characters != t.characters:
            raise ValueError("inconsistent character accounting")
        for finding in self.findings:
            if finding.node_index >= len(self.nodes) or (
                finding.other_index is not None and finding.other_index >= len(self.nodes)
            ):
                raise ValueError("invalid finding reference")
            if finding.intersection is not None and (
                finding.intersection[2] < 0 or finding.intersection[3] < 0
            ):
                raise ValueError("invalid finding geometry")
            if finding.code == "overlap_candidate":
                if (
                    finding.other_index is None
                    or finding.other_index <= finding.node_index
                    or self.nodes[finding.node_index].parent_index
                    != self.nodes[finding.other_index].parent_index
                    or finding.intersection is None
                ):
                    raise ValueError("invalid sibling pair")
        return self


def validate_ui_layout_diagnostics(data: object) -> dict:
    """Reject unsupported/malformed peers without returning their content or error locations."""
    try:
        if (
            type(data) is not dict
            or response_size(data, max_bytes=2 * 1024 * 1024, max_depth=16, max_nodes=100000)
            is None
        ):
            raise ValueError("unsupported")
        model = UiLayoutDiagnosticsV1.model_validate_json(
            json.dumps(data, ensure_ascii=True, allow_nan=False)
        )
        return model.model_dump(mode="json")
    except (ValueError, TypeError, OverflowError, UnicodeError) as error:
        raise ValueError("unsupported_diagnostics_response") from error
