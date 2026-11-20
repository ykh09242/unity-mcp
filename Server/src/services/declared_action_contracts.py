"""Shared wire and discovery contracts for asset moves and prefab checkpoints."""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ActionContract:
    required: tuple[str, ...]
    read_only: bool
    permissions: tuple[str, ...]
    purpose: str
    forbidden: tuple[str, ...] = ()
    result: tuple[tuple[str, str], ...] = ()
    alias_of: str | None = None
    wire_aliases: tuple[tuple[str, str], ...] = ()
    exact_shape: bool = False

    @property
    def native_required(self) -> tuple[str, ...]:
        aliases = dict(self.wire_aliases)
        return tuple(aliases.get(field, field) for field in self.required)


ASSET_REFERENCE_FIELDS: Final = (
    ("path", "path"),
    ("destination", "destination"),
    ("scan_root", "scanRoot"),
    ("recursive", "recursive"),
    ("max_assets", "maxAssets"),
    ("max_work", "maxWork"),
    ("page_size", "pageSize"),
    ("page_number", "pageNumber"),
    ("reference_revision", "referenceRevision"),
    ("reference_snapshot", "referenceSnapshot"),
    ("reference_cursor", "referenceCursor"),
)
ASSET_REFERENCE_WIRE: Final = dict(ASSET_REFERENCE_FIELDS)
_MOVE_FIELDS: Final = ("path", "destination")
_MOVE_RESULT: Final = (("path", "string"), ("verification", "object"))
ASSET_MOVE_CONTRACTS: Final = {
    "move": ActionContract(
        _MOVE_FIELDS,
        False,
        ("edit", "run"),
        "Move path to destination through the AssetDatabase and preserve its GUID. Supply reference_revision from preview_move "
        "to recheck the same scan options, require complete scan coverage and reject reference drift before mutation.",
        result=_MOVE_RESULT,
        wire_aliases=ASSET_REFERENCE_FIELDS,
    ),
    "rename": ActionContract(
        _MOVE_FIELDS,
        False,
        ("edit", "run"),
        "Rename or relocate an asset using its full destination path through the AssetDatabase and preserve its GUID.",
        forbidden=("reference_revision",),
        result=_MOVE_RESULT,
        alias_of="move",
        wire_aliases=ASSET_REFERENCE_FIELDS,
    ),
    "preview_move": ActionContract(
        _MOVE_FIELDS,
        True,
        ("read",),
        "Inspect bounded references and destination risks for a proposed asset move. "
        "Use referenceRevision as reference_revision for a guarded move and check scan coverage before relying on it.",
        result=(
            ("path", "string"),
            ("guid", "string"),
            ("destination", "string"),
            ("referenceRevision", "string"),
            ("scanComplete", "boolean"),
        ),
        wire_aliases=ASSET_REFERENCE_FIELDS,
    ),
}

OPERATION_ID_PATTERN: Final = r"[0-9a-f]{32}"
EXPECTED_OBSERVATION_PATTERN: Final = r"[0-9a-f]{64}"
PREFAB_FIELD_PATTERNS: Final = {
    "operation_id": OPERATION_ID_PATTERN,
    "previous_operation_id": OPERATION_ID_PATTERN,
    "expected_observation": EXPECTED_OBSERVATION_PATTERN,
}
PREFAB_PAGING_DEFAULTS: Final = {"page_number": 1, "page_size": 50}
_CHECKPOINT: Final = (("operation_id", "checkpoint"),)


def _prefab(
    purpose: str,
    *extra_fields: str,
    read_only: bool = False,
    wire_aliases: tuple[tuple[str, str], ...] = (),
) -> ActionContract:
    return ActionContract(
        ("operation_id", *extra_fields),
        read_only,
        ("read",) if read_only else ("edit",),
        purpose,
        wire_aliases=(*_CHECKPOINT, *wire_aliases),
        exact_shape=True,
    )


PREFAB_CHECKPOINT_CONTRACTS: Final = {
    "preview_prefab": _prefab(
        "Prepare a receipt-scoped prefab copy and registration.",
        "settings_path",
        "group_guid",
        "source_prefab",
        "prefab_path",
    ),
    "create_prefab": _prefab("Create the previewed prefab and checkpoint its actual GUID."),
    "register_prefab": _prefab("Register the checkpointed prefab in its previewed group."),
    "prefab_status": _prefab(
        "Inspect the last confirmed step and outstanding work.", read_only=True
    ),
    "cancel_prefab": _prefab("Cancel outstanding prefab workflow steps."),
    "reconcile_prefab": _prefab("Inspect evidence for an uncertain prefab step.", read_only=True),
    "replan_prefab": _prefab(
        "Prepare a replacement checkpoint using a current observation of an earlier operation.",
        "previous_operation_id",
        "expected_observation",
        wire_aliases=(("previous_operation_id", "previous_checkpoint"),),
    ),
}

ACTION_CONTRACTS: Final = {
    "asset_edit": {name: ASSET_MOVE_CONTRACTS[name] for name in ("move", "rename")},
    "asset_reference_diagnostics": {"preview_move": ASSET_MOVE_CONTRACTS["preview_move"]},
    "manage_addressables": PREFAB_CHECKPOINT_CONTRACTS,
}
