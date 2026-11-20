"""Shared discovery metadata for reviewed native operations."""

from dataclasses import dataclass, field
from typing import Literal, NotRequired, TypedDict

from services.registry.capability_catalog import CapabilityEffects
from services.registry.observation_predicates import EvidencePredicates


ActionIntent = Literal["observe", "interact", "edit"]
ObservationSource = Literal["editor_live", "asset_file", "imported_asset", "compile_job"]
ObservationValue = Literal[
    "unityCameras",
    "cinemachineCameras",
    "cinemachineInstalled",
    "render_size.width",
    "render_size.height",
    "current_size",
    "view_instance_id",
    "presets",
    "rendered_image",
    "pointer_hit_order",
    "active_scene.name",
    "active_scene.path",
    "compilation.is_compiling",
    "compile_job.outcome",
    "nodeCount",
    "edgeCount",
    "nodes",
    "edges",
    "properties",
    "keywords",
    "unsaved_graph_structure",
    "property_values",
    "shader_source",
    "contents",
    "encodedContents",
    "nextOffset",
    "shader_graph_structure",
    "unsaved_source",
    "compilation",
    "passes",
    "subshader_count",
]


class Observation(TypedDict):
    """Data origin and selected result fields that a successful read can supply."""

    source: ObservationSource
    values: list[ObservationValue]
    unavailable: list[ObservationValue]
    # Stable value IDs normally use the same wire path. Explicit mappings describe
    # equivalent facts with different response shapes and conditional evidence.
    wire_paths: NotRequired[dict[str, str]]
    conditions: NotRequired[str]
    predicates: NotRequired[EvidencePredicates]
    required_parameters: NotRequired[list[str]]
    related_values: NotRequired[dict[str, list[ObservationValue]]]
    fixed_arguments: NotRequired[dict[str, bool]]
    mutually_exclusive_values: NotRequired[list[list[ObservationValue]]]


@dataclass(frozen=True, slots=True)
class ActionGuidance:
    purpose: str
    required: tuple[str, ...] = ()
    effects: CapabilityEffects | None = None
    evidence_limit: str = ""
    intent: ActionIntent | None = None
    observation: Observation | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolGuidance:
    skills: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    actions: dict[str, ActionGuidance] = field(default_factory=dict)
