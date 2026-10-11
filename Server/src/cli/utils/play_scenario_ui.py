"""Offline comparison of two explicitly selected semantic UI snapshots."""

from dataclasses import dataclass
import json
import math
from pathlib import Path

from pydantic import JsonValue

from cli.utils.play_scenario_analysis_io import AnalysisError, read_json
from models.ui_snapshots import UiGeometry, UiSnapshot


@dataclass(frozen=True, slots=True)
class UiComparisonPolicy:
    """Absolute tolerance in the snapshot's declared coordinate space."""

    geometry_tolerance: float = 0.0
    require_occlusion: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.geometry_tolerance) or not 0 <= self.geometry_tolerance <= 10000:
            raise AnalysisError("Geometry tolerance must be finite and within 0..10000")


def load_ui_snapshot(path: Path) -> UiSnapshot:
    """Parse one bounded saved snapshot; reject duplicate JSON keys before modeling."""
    # read_json shares duplicate-key and numeric checks with native scenario reports.
    raw = read_json(path)
    # JSON mode accepts fixed-size arrays for tuples without weakening scalar strictness.
    return UiSnapshot.model_validate_json(json.dumps(raw, allow_nan=False))


def _geometry_changes(before: UiGeometry, after: UiGeometry, tolerance: float) -> list[str]:
    fields: list[str] = []
    for field in UiGeometry.model_fields.keys():
        left = getattr(before, field)
        right = getattr(after, field)
        if left == right:
            continue
        if (
            left is None
            or right is None
            or any(abs(a - b) > tolerance for a, b in zip(left, right, strict=True))
        ):
            fields.append("geometry." + field)
    return fields


def compare_ui_snapshots(
    baseline: Path, candidate: Path, policy: UiComparisonPolicy = UiComparisonPolicy()
) -> dict[str, JsonValue]:  # noqa: DICT_OK - CLI JSON envelope
    """Compare compatible complete trees, never matching by instance ID or fuzzy name."""
    before, after = load_ui_snapshot(baseline), load_ui_snapshot(candidate)
    for field in ("framework", "mode", "resolution", "target", "evidence"):
        if getattr(before, field) != getattr(after, field):
            raise AnalysisError(f"UI snapshots have incompatible {field}")
    if sorted(before.scenes) != sorted(after.scenes):
        raise AnalysisError("UI snapshots have incompatible loaded scenes")
    if policy.require_occlusion:
        raise AnalysisError(
            "UI snapshots cannot establish actual occlusion; renderer cull state is partial evidence"
        )
    left = {node.id: node for node in before.nodes}
    right = {node.id: node for node in after.nodes}
    added = sorted(right.keys() - left.keys())
    removed = sorted(left.keys() - right.keys())
    changed: list[JsonValue] = []
    for identity in sorted(left.keys() & right.keys()):
        old, new = left[identity], right[identity]
        fields = [
            field
            for field in type(old).model_fields
            if field not in ("id", "geometry") and getattr(old, field) != getattr(new, field)
        ]
        fields.extend(_geometry_changes(old.geometry, new.geometry, policy.geometry_tolerance))
        if fields:
            changed.append({"id": identity, "fields": fields})
    return {
        "schema_version": 1,
        "status": "changed" if added or removed or changed else "same",
        "compared": True,
        "framework": before.framework,
        "geometry_tolerance": policy.geometry_tolerance,
        "added": added,
        "removed": removed,
        "changed": changed,
        "limitations": sorted(set(before.limitations + after.limitations)),
        "occlusion_status": before.evidence.occlusion_status,
    }
