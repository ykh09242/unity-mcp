"""Bounded preview/apply of native serialized scene properties."""

import json
import re
import unicodedata
from typing import Annotated, Literal, assert_never

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr
from pydantic import TypeAdapter, ValidationError

from services.registry import mcp_for_unity_tool
from services.registry.preview_continuation import (
    PreviewApply,
    has_field_edits,
    native_plan_id,
    same_serialized_value,
    matches_serialized_edit,
)
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class SceneEdit(BaseModel):
    """One requested write to an existing component field."""

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
    property: Annotated[
        StrictStr,
        Field(
            min_length=1,
            max_length=128,
            description="Exact allowlisted serialized path or its unique Unity field label.",
        ),
    ]
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


def _same_structure_plan(params, data):
    requested, reported = params.get("operations"), data.get("operations")
    if (
        type(data.get("scope_id")) is not int
        or data["scope_id"] != params.get("target")
        or not isinstance(requested, list)
        or not requested
        or not isinstance(reported, list)
        or len(requested) != len(reported)
        or data.get("impact", reported) != reported
    ):
        return False
    for index, (op, row) in enumerate(zip(requested, reported)):
        if (
            not isinstance(row, dict)
            or not isinstance(op, dict)
            or set(row) - {"index", "affected_ids"} != op.keys()
        ):
            return False
        if any(type(row[key]) is not type(value) or row[key] != value for key, value in op.items()):
            return False
        if "index" in row and (type(row["index"]) is not int or row["index"] != index):
            return False
        if "affected_ids" in row:
            ids = row["affected_ids"]
            target = str(op.get("temp_id", op.get("target")))
            if (
                not isinstance(ids, list)
                or not all(isinstance(item, str) and item for item in ids)
                or target not in ids
            ):
                return False
            if op["op"] == "create" and ids != [target]:
                return False
    return True


def _scene_field_key(spelling):
    # Only endorse labels independently recognizable from the native serialized path.
    return unicodedata.normalize("NFC", spelling).replace(" ", "").lower().removeprefix("m_")


def _same_scene_fields(requested, rows):
    if not isinstance(requested, list) or not has_field_edits(rows) or len(requested) != len(rows):
        return False
    # Native plans sort by scene, target, component and property. Match each
    # row exactly once against the original selector, independent of that order.
    lookup = {}
    paths = {}
    by_leaf = {}
    for index, row in enumerate(rows):
        if (
            type(row.get("target_id")) is not int
            or not isinstance(path := row.get("target_path"), str)
            or not path
            or type(row.get("component_index")) is not int
        ):
            return False
        path_name = row["property"]
        if not isinstance(row.get("component_type"), str) or not re.fullmatch(
            r"m_[A-Za-z][A-Za-z0-9]*", path_name
        ):
            return False
        component = row["component_type"], row["component_index"], _scene_field_key(path_name)
        target_id = str(row["target_id"])
        if paths.setdefault(target_id, path) != path:
            return False
        by_leaf.setdefault(path.rsplit("/", 1)[-1], set()).add(target_id)
        key = (target_id, *component)
        if key in lookup:
            return False
        lookup[key] = index
    matched = set()
    for edit in requested:
        method = edit.get("search_method", "by_id")
        target = str(edit["target"])
        if method != "by_id":
            # Unity resolves the GameObject before choosing its component. Only a
            # rooted path must match the entire hierarchy; other paths match a
            # slash-delimited suffix. An ambiguous observed selector proves no target.
            candidates = by_leaf.get(target.rsplit("/", 1)[-1], ())
            if method == "by_path":
                if target.startswith("/"):
                    candidates = [key for key in candidates if paths[key] == target[1:]]
                else:
                    candidates = [
                        key
                        for key in candidates
                        if paths[key] == target or paths[key].endswith("/" + target)
                    ]
            elif method != "by_name" or "/" in target:
                return False
            if len(candidates) != 1:
                return False
            target = next(iter(candidates))
        name = edit["component_type"].removeprefix("UnityEngine.")
        prop = edit["property"]
        index = lookup.get(
            (
                target,
                "UnityEngine." + name,
                edit.get("component_index") or 0,
                _scene_field_key(prop),
            )
        )
        if index is None or index in matched:
            return False
        row = rows[index]
        if "requested_property" in row:
            if row["requested_property"] != prop:
                return False
        elif prop != row["property"] and not (
            name == "Transform"
            and prop in {"localPosition", "localRotation", "localScale"}
            and row["property"] == "m_L" + prop[1:]
        ):
            # Older Editors reported only canonical paths and accepted these three aliases.
            return False
        if not matches_serialized_edit(edit["value"], row):
            return False
        if edit.get("expected_old") is not None and not same_serialized_value(
            edit["expected_old"], row["before"]
        ):
            return False
        matched.add(index)
    return True


def _scene_plan_apply(params, data):
    token = native_plan_id(data)
    if not token:
        return None
    if params["action"] == "preview_structure":
        if not _same_structure_plan(params, data):
            return None
        action, check = (
            "apply_structure",
            "Confirm the applied operations, temporary IDs and Undo group.",
        )
    else:
        if not _same_scene_fields(params.get("edits"), data.get("edits")):
            return None
        action, check = (
            "apply",
            "Confirm the target fields in the applied edits and the Undo group.",
        )
    arguments = {"action": action, "preview_id": token}
    if params.get("undo_name") and params["undo_name"] != "MCP Scene Edits":
        arguments["undo_name"] = params["undo_name"]
    return PreviewApply(arguments, {"check": check}, editor_executes=True)


@mcp_for_unity_tool(
    preview_contracts={"preview": _scene_plan_apply, "preview_structure": _scene_plan_apply},
    description=(
        "Preview or apply 1-1000 allowlisted native Transform, BoxCollider, SphereCollider, "
        "Rigidbody, or CapsuleCollider serialized property edits in a stable Edit-mode scene. "
        "Include only fields the user asked to change. Verify preserved values with a separate readback when needed. Preview returns "
        "before/after values and a one-attempt token valid for 10 minutes. Apply accepts "
        "preview_id, or edits with expected_old for every field. Rejects drift before mutation; "
        "apply returns receipt_id. Use undo with this receipt to restore unchanged applied fields "
        "and report conflicts with before, applied and current values. Also accepts a receipt from "
        "prefab_contents apply_preview. No arbitrary transaction "
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
    action: Literal["preview", "apply", "undo", "preview_structure", "apply_structure"],
    edits: list[SceneEdit] | None = None,
    preview_id: Annotated[str | None, Field(min_length=1, max_length=64)] = None,
    undo_name: Annotated[str, Field(min_length=1, max_length=80)] = "MCP Scene Edits",
    target: StrictInt | None = None,
    operations: list[StructureOperation] | None = None,
    receipt_id: Annotated[str | None, Field(min_length=1, max_length=64)] = None,
):
    """Validate request shape locally; Unity owns resolution, drift and Undo validation."""
    if action not in {"preview", "apply", "undo", "preview_structure", "apply_structure"}:
        return {"success": False, "message": "Unknown scene edit action."}
    if receipt_id is not None and action != "undo":
        return {"success": False, "message": "receipt_id requires undo."}
    if not isinstance(undo_name, str) or not undo_name.strip() or len(undo_name) > 80:
        return {"success": False, "message": "undo_name must contain 1-80 characters."}
    if action == "undo":
        if (
            type(receipt_id) is not str
            or not receipt_id.strip()
            or len(receipt_id) > 64
            or any(value is not None for value in (edits, preview_id, target, operations))
        ):
            return {"success": False, "message": "Property undo requires only receipt_id."}
        params = {"action": action, "receipt_id": receipt_id}
    elif action == "preview_structure":
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
