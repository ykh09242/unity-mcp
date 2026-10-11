"""Strict bounded contracts for semantic UI snapshots."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator


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
