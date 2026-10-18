"""Closed observation contract for already-generated native TMP layout."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from models.response_limits import response_size

Finite = Annotated[float, Field(allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, le=200000)]
Rect = Annotated[list[Finite], Field(min_length=4, max_length=4)]
Reason = Literal[
    "max_nodes",
    "max_depth",
    "max_evaluations",
    "max_characters",
    "per_string_limit",
    "invalid_metadata",
]
Unknown = Literal[
    "unsupported_type",
    "dirty_layout",
    "uninitialized_layout",
    "inconsistent_cache",
    "cache_character_limit",
    "cache_line_limit",
    "cache_font_limit",
    "unsupported_geometry",
    "unsupported_overflow_mode",
    "max_evaluations",
]
METRICS = (
    "native_overflow",
    "first_overflow_index",
    "native_truncated",
    "character_count",
    "line_count",
    "base_font_size",
    "autosize",
    "min_point_size",
    "max_point_size",
    "fallback_characters",
    "font_count",
    "ink_bounds",
    "text_rect",
    "margins",
)


class Closed(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)


class Evidence(Closed):
    observation: Literal["existing_generated_layout"]
    freshness: Literal["cached_not_marked_dirty"]
    rasterization: Literal["not_observed"]
    profile: None


class Node(Closed):
    instance_id: Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]
    diagnostic_path: Annotated[
        str, Field(max_length=1024, pattern=r"^(?:/(?:0|[1-9][0-9]{0,9}))*$")
    ]
    name: Annotated[str, Field(max_length=256)]
    status: Literal["observed", "unknown", "redacted"]
    reason: Unknown | Literal["input_owned"] | None
    native_overflow: StrictBool | None
    first_overflow_index: Annotated[int, Field(ge=-1, le=2**31 - 1)] | None
    native_truncated: StrictBool | None
    character_count: Annotated[int, Field(ge=0, le=4096)] | None
    line_count: Annotated[int, Field(ge=0, le=512)] | None
    base_font_size: Annotated[Finite, Field(gt=0)] | None
    autosize: StrictBool | None
    min_point_size: Annotated[Finite, Field(ge=0)] | None
    max_point_size: Annotated[Finite, Field(ge=0)] | None
    fallback_characters: Annotated[int, Field(ge=0, le=4096)] | None
    font_count: Annotated[int, Field(ge=0, le=8)] | None
    ink_bounds: Rect | None
    text_rect: Rect | None
    margins: Rect | None

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.instance_id == 0
            or len(self.name.encode("utf-16-le")) // 2 > 256
            or any(int(part) > 2**31 - 1 for part in self.diagnostic_path.split("/")[1:])
        ):
            raise ValueError("invalid native identity or metadata")
        if self.status != "observed":
            if (
                self.reason is None
                or (self.status == "redacted") != (self.reason == "input_owned")
                or any(getattr(self, name) is not None for name in METRICS)
            ):
                raise ValueError("invalid unavailable metrics")
        else:
            required = set(METRICS) - {"ink_bounds", "min_point_size", "max_point_size"}
            if self.reason is not None or any(getattr(self, name) is None for name in required):
                raise ValueError("missing observed metrics")
            if (
                self.native_overflow != (self.first_overflow_index != -1)
                or self.fallback_characters > self.character_count
            ):
                raise ValueError("contradictory native evidence")
            if (self.character_count == 0) != (
                self.min_point_size is None and self.max_point_size is None
            ):
                raise ValueError("invalid point sizes")
            if self.character_count and (
                self.min_point_size is None
                or self.max_point_size is None
                or self.min_point_size > self.max_point_size
            ):
                raise ValueError("invalid point range")
            if any(
                rect is not None and (rect[2] < 0 or rect[3] < 0)
                for rect in (self.ink_bounds, self.text_rect)
            ):
                raise ValueError("invalid rectangle")
        return self


class Traversal(Closed):
    max_nodes: Annotated[int, Field(ge=1, le=1000)]
    max_depth: Annotated[int, Field(ge=0, le=24)]
    max_evaluations: Annotated[int, Field(ge=1, le=200000)]
    max_characters: Annotated[int, Field(ge=1, le=262144)]
    nodes_visited: Annotated[int, Field(ge=0, le=1000)]
    nodes_emitted: Annotated[int, Field(ge=0, le=1000)]
    evaluations: Count
    characters: Annotated[int, Field(ge=0, le=262144)]
    cached_characters_scanned: Annotated[int, Field(ge=0, le=16384)]
    cached_lines_scanned: Annotated[int, Field(ge=0, le=2048)]
    truncated: StrictBool
    reasons: Annotated[list[Reason], Field(max_length=6)]


class TmpLayout(Closed):
    kind: Literal["ugui_tmp_layout_diagnostics"]
    schema_version: Literal[1]
    framework: Literal["ugui_tmp"]
    mode: Literal["edit", "play"]
    frame: Annotated[int, Field(ge=0, le=2**31 - 1)]
    target_id: Annotated[int, Field(ge=-(2**31), le=2**31 - 1)]
    status: Literal["complete", "partial"]
    evidence: Evidence
    nodes: Annotated[list[Node], Field(max_length=1000)]
    traversal: Traversal
    limitations: Annotated[list[Annotated[str, Field(max_length=512)]], Field(max_length=8)]

    @model_validator(mode="after")
    def consistent(self):
        t = self.traversal
        if self.target_id == 0 or any(
            n.diagnostic_path == "" and n.instance_id != self.target_id for n in self.nodes
        ):
            raise ValueError("invalid root identity")
        if (
            t.nodes_emitted != len(self.nodes)
            or t.nodes_emitted > t.nodes_visited
            or t.nodes_visited > t.max_nodes
            or t.evaluations > t.max_evaluations
            or t.characters > t.max_characters
        ):
            raise ValueError("budget exceeded")
        if len({n.instance_id for n in self.nodes}) != len(self.nodes) or len(
            {n.diagnostic_path for n in self.nodes}
        ) != len(self.nodes):
            raise ValueError("duplicate identity")
        if len(t.reasons) != len(set(t.reasons)) or t.truncated != bool(t.reasons):
            raise ValueError("invalid truncation")
        characters = sum(
            len(value.encode("utf-16-le")) // 2
            for n in self.nodes
            for value in (n.name, n.diagnostic_path)
        )
        if characters != t.characters or any(
            n.diagnostic_path.count("/") > t.max_depth for n in self.nodes
        ):
            raise ValueError("invalid metadata accounting")
        partial = t.truncated or any(n.status == "unknown" for n in self.nodes)
        if (self.status == "partial") != partial:
            raise ValueError("incorrect completeness")
        if (
            sum(n.character_count or 0 for n in self.nodes) > t.cached_characters_scanned
            or sum(n.line_count or 0 for n in self.nodes) > t.cached_lines_scanned
        ):
            raise ValueError("missing scan evidence")
        return self


def validate_ugui_tmp_layout_diagnostics(data: object) -> dict:
    """Refuse malformed peer data with one fixed, non-echoing error."""
    try:
        if (
            type(data) is not dict
            or type(data.get("schema_version")) is not int
            or response_size(data, max_bytes=2 * 1024 * 1024, max_depth=12, max_nodes=100000)
            is None
        ):
            raise ValueError("unsupported")
        model = TmpLayout.model_validate_json(json.dumps(data, ensure_ascii=True, allow_nan=False))
        return model.model_dump(mode="json")
    except (ValueError, TypeError, OverflowError, UnicodeError) as error:
        raise ValueError("unsupported_text_response") from error
