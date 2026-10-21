"""Intrinsic admission for opt-in, bounded, read-only reference diagnostics."""

import re
from typing import Any

from services.tools.pagination import page_integer


ASSET_REFERENCE_ACTIONS = frozenset(
    {"get_dependencies", "get_reverse_references", "preview_move", "preview_delete"}
)


def reference_path(value: str | None, name: str, *, root: bool = False) -> str:
    """Require an explicit lexical Assets scope; native code checks physical containment."""
    if (
        type(value) is not str
        or len(value) > 2048
        or value != value.strip()
        or "\\" in value
        or any(ord(char) < 32 for char in value)
        or not (value == "Assets" or value.startswith("Assets/"))
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or (not root and value == "Assets")
    ):
        raise ValueError(f"{name} requires a contained Assets path (maximum 2048 characters).")
    return value


def reference_page(
    page_size: int | str | None,
    page_number: int | str | None,
    max_work: int | str | None,
    reference_revision: str | None,
    *,
    scene: bool = False,
    reference_snapshot: bool | None = None,
    reference_cursor: str | None = None,
) -> dict[str, Any]:
    """Parse finite work/page budgets and an optional observation recheck."""
    params = {
        "pageSize": page_integer(page_size, 50, 1, 100, "page_size"),
        "pageNumber": page_integer(page_number, 1, 1, 2_147_483_647, "page_number"),
        "maxWork": page_integer(max_work, 100000 if scene else 10000, 1, 4000000, "max_work"),
    }
    if reference_revision is not None:
        if (
            type(reference_revision) is not str
            or re.fullmatch(r"mcp-ref-v1_[0-9a-f]{64}", reference_revision) is None
        ):
            raise ValueError("reference_revision must be an observed diagnostic revision.")
        params["referenceRevision"] = reference_revision
    if reference_snapshot is not None:
        if not scene or type(reference_snapshot) is not bool:
            raise ValueError("reference_snapshot must be a boolean for scene diagnostics.")
        params["referenceSnapshot"] = reference_snapshot
    if reference_cursor is not None:
        if (
            not scene
            or type(reference_cursor) is not str
            or re.fullmatch(r"mcp-ref-snapshot-v1_[0-9a-f]{32}", reference_cursor) is None
            or reference_revision is not None
            or reference_snapshot is True
        ):
            raise ValueError(
                "reference_cursor requires a captured cursor without a live revision or new snapshot."
            )
        params["referenceCursor"] = reference_cursor
    return params
