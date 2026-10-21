"""Bounded preview/apply of native serialized scene properties."""

import json
from typing import Annotated, Literal, assert_never

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr
from pydantic import TypeAdapter, ValidationError

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class SceneEdit(BaseModel):
    """One existing component field, with optional optimistic concurrency value."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    target: StrictStr | StrictInt
    search_method: Literal["by_id", "by_name", "by_path"] = "by_id"
    component_type: Literal[
        "Transform",
        "BoxCollider",
        "SphereCollider",
        "Rigidbody",
        "CapsuleCollider",
        "UnityEngine.Transform",
        "UnityEngine.BoxCollider",
        "UnityEngine.SphereCollider",
        "UnityEngine.Rigidbody",
        "UnityEngine.CapsuleCollider",
    ]
    component_index: Annotated[StrictInt, Field(ge=0)] | None = None
    property: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    value: JsonValue
    expected_old: JsonValue = None


class StructureCreate(BaseModel):
    """Create a plain child; references may use earlier temporary IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    op: Literal["create"]
    temp_id: Annotated[StrictStr, Field(pattern=r"^\$[A-Za-z][A-Za-z0-9_]{0,31}$")]
    name: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    parent: StrictInt | StrictStr


class StructureReparent(BaseModel):
    """Move a plain subtree, preserving local transform values."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    op: Literal["reparent"]
    target: StrictInt | StrictStr
    parent: StrictInt | StrictStr


class StructureDelete(BaseModel):
    """Delete a plain subtree within the preview scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    op: Literal["delete"]
    target: StrictInt | StrictStr


StructureOperation = Annotated[
    StructureCreate | StructureReparent | StructureDelete, Field(discriminator="op")
]
_OPERATIONS = TypeAdapter(list[StructureOperation])
_EDITS = TypeAdapter(list[SceneEdit])


@mcp_for_unity_tool(
    description=(
        "Preview or apply 1-1000 allowlisted native Transform, BoxCollider, SphereCollider, "
        "Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene. Preview returns "
        "before/after values and a one-attempt token valid for 10 minutes. Apply accepts "
        "preview_id, or edits with expected_old for every field. Rejects drift before mutation; "
        "one named Undo restores supported recorded component values. No arbitrary transaction "
        "or filesystem, build, package, custom callback, dirty-flag rollback is promised. "
        "preview_structure takes an integer scene-root target scope and 1-256 ordered create/delete/reparent "
        "operations. Only ordinary Transform-only subtrees are supported; prefab instances and "
        "custom/native component subtrees are refused. create assigns a $temporary ID that later "
        "operations may reference. reparent preserves local transforms. apply_structure requires "
        "the preview token and rejects hierarchy/state drift before its one Undo group."
    ),
    annotations=ToolAnnotations(
        title="Preview and Apply Scene Edits",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
    ),
)
async def manage_scene_edits(
    ctx: Context,
    action: Literal["preview", "apply", "preview_structure", "apply_structure"],
    edits: list[SceneEdit] | None = None,
    preview_id: Annotated[str | None, Field(min_length=1, max_length=64)] = None,
    undo_name: Annotated[str, Field(min_length=1, max_length=80)] = "MCP Scene Edits",
    target: StrictInt | None = None,
    operations: list[StructureOperation] | None = None,
):
    """Validate request shape locally; Unity owns resolution, drift and Undo validation."""
    if action not in {"preview", "apply", "preview_structure", "apply_structure"}:
        return {"success": False, "message": "action must be preview or apply."}
    if not isinstance(undo_name, str) or not undo_name.strip() or len(undo_name) > 80:
        return {"success": False, "message": "undo_name must contain 1-80 characters."}
    if action == "preview_structure":
        if preview_id is not None or edits is not None or type(target) is not int:
            return {
                "success": False,
                "message": "Structural preview needs integer target and operations only.",
            }
        try:
            parsed_operations = _OPERATIONS.validate_python(operations)
            if not 1 <= len(parsed_operations) <= 256:
                return {"success": False, "message": "operations must contain 1-256 entries."}
            known = set()
            for operation in parsed_operations:
                for reference in (
                    getattr(operation, "target", None),
                    getattr(operation, "parent", None),
                ):
                    if isinstance(reference, str) and reference not in known:
                        return {
                            "success": False,
                            "message": "References must be integer IDs or earlier temporary IDs.",
                        }
                match operation:
                    case StructureCreate(temp_id=temporary):
                        if temporary in known:
                            return {"success": False, "message": "Temporary IDs must be unique."}
                        known.add(temporary)
                    case StructureReparent() | StructureDelete():
                        pass
                    case unreachable:
                        assert_never(unreachable)
            params = {
                "action": action,
                "target": target,
                "operations": [item.model_dump() for item in parsed_operations],
            }
            if len(json.dumps(params, ensure_ascii=True)) > 4 * 1024 * 1024:
                return {"success": False, "message": "Structure request exceeds 4 Mi characters."}
        except ValidationError as error:
            return {"success": False, "message": f"Invalid structure operations: {error}"}
    elif preview_id is not None:
        if (
            action not in {"apply", "apply_structure"}
            or edits is not None
            or operations is not None
            or target is not None
        ):
            return {"success": False, "message": "preview_id requires apply without edits."}
        if not isinstance(preview_id, str) or not preview_id.strip() or len(preview_id) > 64:
            return {"success": False, "message": "preview_id must contain 1-64 characters."}
        params = {"action": action, "preview_id": preview_id, "undo_name": undo_name}
    else:
        if action == "apply_structure" or operations is not None or target is not None:
            return {"success": False, "message": "Structural apply requires preview_id only."}
        try:
            parsed = _EDITS.validate_python(edits)
            if not 1 <= len(parsed) <= 1000:
                return {"success": False, "message": "edits must contain 1-1000 entries."}
            if action == "apply" and any(
                "expected_old" not in item.model_fields_set for item in parsed
            ):
                return {
                    "success": False,
                    "message": "Direct apply requires expected_old for every edit.",
                }
            params = {
                "action": action,
                "edits": [item.model_dump(exclude_unset=True) for item in parsed],
                "undo_name": undo_name,
            }
            if len(json.dumps(params, allow_nan=False, ensure_ascii=True)) > 4 * 1024 * 1024:
                return {"success": False, "message": "Scene edit request exceeds 4 Mi characters."}
        except (ValidationError, ValueError) as error:
            return {"success": False, "message": f"Invalid scene edits: {error}"}

    unity_instance = await get_unity_instance_from_context(ctx)
    gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=False)
    if gate is not None:
        return gate.model_dump()
    return await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_scene_edits",
        params,
    )
