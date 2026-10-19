"""Strict bounded contracts for semantic UI snapshots."""

import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    model_serializer,
    model_validator,
)


class UiSnapshotError(ValueError):
    """Snapshot evidence cannot establish a complete unambiguous tree."""


Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Identity = Annotated[str, Field(strict=True, min_length=1, max_length=4096)]


class SnapshotModel(BaseModel):
    """Snapshots reject unknown fields and coercion at the file boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class UiResolution(SnapshotModel):
    width: Annotated[int, Field(ge=1, le=16384)]
    height: Annotated[int, Field(ge=1, le=16384)]


class UiEvidence(SnapshotModel):
    geometry_space: Literal["world_aabb", "panel_points"]
    visibility_method: Literal["render_eligibility"]
    interaction_method: Literal["component_state"]
    occlusion_status: Literal["unavailable", "renderer_cull_only"]
    layout_status: Literal["current"]


class UiGeometry(SnapshotModel):
    bounds: tuple[Finite, Finite, Finite, Finite]
    local_rect: tuple[Finite, Finite, Finite, Finite] | None = None
    anchor_min: tuple[Finite, Finite] | None = None
    anchor_max: tuple[Finite, Finite] | None = None
    pivot: tuple[Finite, Finite] | None = None
    anchored_position: tuple[Finite, Finite] | None = None
    local_position: tuple[Finite, Finite, Finite] | None = None
    local_scale: tuple[Finite, Finite, Finite] | None = None
    local_rotation: tuple[Finite, Finite, Finite] | None = None

    @model_validator(mode="after")
    def bounded_rect(self) -> "UiGeometry":
        """Axis-aligned bound sizes cannot carry negative evidence."""
        if self.bounds[2] < 0 or self.bounds[3] < 0:
            raise UiSnapshotError("Snapshot bounds have negative dimensions")
        return self


class UiNode(SnapshotModel):
    id: Identity
    parent: Identity | None
    sibling_index: Annotated[int, Field(ge=0, le=999)]
    type: Identity
    components: list[Identity] = Field(max_length=64)
    classes: list[Identity] = Field(max_length=64)
    text: Annotated[str, Field(max_length=8192)] | None
    active: StrictBool
    visible: StrictBool
    interactable: StrictBool | None
    renderer_culled: StrictBool | None
    alpha: Annotated[Finite, Field(ge=0, le=1)]
    geometry: UiGeometry


class UiSnapshot(SnapshotModel):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    framework: Literal["ugui", "uitoolkit"]
    mode: Literal["edit", "play"]
    scenes: list[Identity] = Field(min_length=1, max_length=32)
    resolution: UiResolution
    target: Identity
    evidence: UiEvidence
    complete: StrictBool
    max_nodes: Annotated[int, Field(ge=1, le=1000)]
    nodes: list[UiNode] = Field(min_length=1, max_length=1000)
    limitations: list[StrictStr] = Field(max_length=16)

    @model_validator(mode="after")
    def consistent_tree(self) -> "UiSnapshot":
        """Reject duplicate identities, orphan/cyclic ordering, and overflow."""
        if not self.complete:
            raise UiSnapshotError("Snapshot evidence is incomplete")
        if len(self.nodes) > self.max_nodes or len(set(self.scenes)) != len(self.scenes):
            raise UiSnapshotError("Snapshot has overflow or ambiguous scene identities")
        seen: set[str] = set()
        sibling_slots: set[tuple[str | None, int]] = set()
        for index, node in enumerate(self.nodes):
            if node.id in seen:
                raise UiSnapshotError("Snapshot has ambiguous duplicate node identities")
            slot = (node.parent, node.sibling_index)
            if slot in sibling_slots:
                raise UiSnapshotError("Snapshot has ambiguous duplicate sibling positions")
            sibling_slots.add(slot)
            if index == 0:
                if node.id != self.target or node.parent is not None:
                    raise UiSnapshotError("Snapshot root does not match its explicit target")
            elif node.parent not in seen:
                raise UiSnapshotError("Snapshot has an orphan or non-parent-first node")
            elif not node.id.startswith(node.parent + "/"):
                raise UiSnapshotError("Snapshot node identity disagrees with its semantic parent")
            seen.add(node.id)
        expected_space = "world_aabb" if self.framework == "ugui" else "panel_points"
        if self.evidence.geometry_space != expected_space:
            raise UiSnapshotError("Snapshot framework and geometry evidence disagree")
        return self


Int32 = Annotated[int, Field(ge=-2147483648, le=2147483647)]
Index = Annotated[int, Field(ge=0, le=2147483647)]


class UiToggleState(SnapshotModel):
    value: StrictBool


class UiFloatSliderState(SnapshotModel):
    kind: Literal["float"]
    value: Finite
    low: Finite
    high: Finite
    whole_numbers: StrictBool


class UiIntegerSliderState(SnapshotModel):
    kind: Literal["integer"]
    value: Int32
    low: Int32
    high: Int32


class UiIndexSelection(SnapshotModel):
    kind: Literal["index"]
    index: Annotated[int, Field(ge=-1, le=2147483647)]


class UiIndicesSelection(SnapshotModel):
    kind: Literal["indices"]
    selection_type: Literal["none", "single", "multiple"]
    indices: list[Index] = Field(max_length=64)

    @model_validator(mode="after")
    def ordered_indices(self):
        if self.indices != sorted(set(self.indices)):
            raise UiSnapshotError("Selection indices must be sorted and unique")
        return self


class UiInputState(SnapshotModel):
    redacted: StrictBool
    read_only: StrictBool
    password: StrictBool

    @model_validator(mode="after")
    def always_redacted(self):
        if self.redacted is not True:
            raise UiSnapshotError("Input state must be redacted")
        return self


class UiScrollState(SnapshotModel):
    kind: Literal["normalized", "panel_points"]
    position: tuple[Finite, Finite]


class UiControlState(SnapshotModel):
    toggle: UiToggleState | None = None
    slider: (
        Annotated[UiFloatSliderState | UiIntegerSliderState, Field(discriminator="kind")] | None
    ) = None
    selection: (
        Annotated[UiIndexSelection | UiIndicesSelection, Field(discriminator="kind")] | None
    ) = None
    input: UiInputState | None = None
    scroll: UiScrollState | None = None

    @model_validator(mode="after")
    def provided_sections_are_typed(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise UiSnapshotError("Provided control state sections cannot be null")
        return self

    @model_serializer(mode="wrap")
    def sparse_sections(self, serialize):
        return {key: value for key, value in serialize(self).items() if value is not None}


class UiNodeV2(UiNode):
    state: UiControlState

    @model_validator(mode="after")
    def input_text_is_never_retained(self):
        if self.state.input is not None and self.text is not None:
            raise UiSnapshotError("Input nodes require null text")
        return self


class UiEvidenceV2(UiEvidence):
    control_state_method: Literal["typed_allowlist_v1"]
    input_privacy: Literal["recognized_input_ownership_v1"]
    component_type: Literal["ui_document", "panel_renderer"] = None

    @model_serializer(mode="wrap")
    def sparse_component_evidence(self, serialize):
        value = serialize(self)
        if self.component_type is None:
            value.pop("component_type", None)
        return value


class UiSnapshotV2(UiSnapshot):
    schema_version: Annotated[int, Field(ge=2, le=2)]
    nodes: list[UiNodeV2] = Field(min_length=1, max_length=1000)
    evidence: UiEvidenceV2

    @model_validator(mode="after")
    def framework_scroll_units(self):
        if self.framework == "ugui" and self.evidence.component_type is not None:
            raise UiSnapshotError("Component evidence requires UI Toolkit")
        expected = "normalized" if self.framework == "ugui" else "panel_points"
        if any(
            node.state.scroll is not None and node.state.scroll.kind != expected
            for node in self.nodes
        ):
            raise UiSnapshotError("Control state coordinates disagree with the framework")
        return self


class UiEvidenceV3(UiEvidenceV2):
    identity_method: Literal["explicit_path_map_v1"]


class UiSnapshotV3(UiSnapshotV2):
    schema_version: Annotated[int, Field(ge=3, le=3)]
    evidence: UiEvidenceV3


def validate_ui_snapshot(
    value,
    *,
    expected_version: Literal[2, 3] | None = None,
    expected_component_type: Literal["auto", "ui_document", "panel_renderer"] | None = None,
) -> UiSnapshot | UiSnapshotV2 | UiSnapshotV3:
    """Dispatch exact versions without echoing native/file payloads in validation errors."""
    try:
        version = value.get("schema_version") if isinstance(value, dict) else None
        if (
            type(version) is not int
            or version not in (1, 2, 3)
            or (expected_version is not None and version != expected_version)
        ):
            raise UiSnapshotError(
                "Unsupported UI snapshot schema_version for the requested identity mode"
            )
        model = {1: UiSnapshot, 2: UiSnapshotV2, 3: UiSnapshotV3}[version]
        snapshot = model.model_validate_json(json.dumps(value, allow_nan=False))
        if expected_component_type is not None:
            if not isinstance(snapshot, UiSnapshotV2) or snapshot.evidence.component_type is None:
                raise UiSnapshotError(
                    "Explicit component selection requires native component evidence"
                )
            if (
                expected_component_type != "auto"
                and snapshot.evidence.component_type != expected_component_type
            ):
                raise UiSnapshotError(
                    "Native component evidence disagrees with the requested component"
                )
        return snapshot
    except (ValidationError, ValueError, TypeError):
        raise UiSnapshotError("UI snapshot schema/privacy/identity validation failed") from None
