"""Bounded preview/apply of native serialized scene properties."""

import json
from typing import Annotated, Literal

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
        "UnityEngine.Transform",
        "UnityEngine.BoxCollider",
        "UnityEngine.SphereCollider",
        "UnityEngine.Rigidbody",
    ]
    component_index: Annotated[StrictInt, Field(ge=0)] | None = None
    property: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    value: JsonValue
    expected_old: JsonValue = None


_EDITS = TypeAdapter(list[SceneEdit])


@mcp_for_unity_tool(
    description=(
        "Preview or apply 1-64 allowlisted native Transform, BoxCollider, SphereCollider, "
        "or Rigidbody serialized property edits in a stable Edit-mode scene. Preview returns "
        "before/after values and a one-attempt token valid for 10 minutes. Apply accepts "
        "preview_id, or edits with expected_old for every field. Rejects drift before mutation; "
        "one named Undo restores supported recorded component values. No arbitrary transaction "
        "or filesystem, build, package, custom callback, dirty-flag rollback is promised."
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
    action: Literal["preview", "apply"],
    edits: list[SceneEdit] | None = None,
    preview_id: Annotated[str | None, Field(min_length=1, max_length=64)] = None,
    undo_name: Annotated[str, Field(min_length=1, max_length=80)] = "MCP Scene Edits",
):
    """Validate request shape locally; Unity owns resolution, drift and Undo validation."""
    if action not in {"preview", "apply"}:
        return {"success": False, "message": "action must be preview or apply."}
    if not isinstance(undo_name, str) or not undo_name.strip() or len(undo_name) > 80:
        return {"success": False, "message": "undo_name must contain 1-80 characters."}
    if preview_id is not None:
        if action != "apply" or edits is not None:
            return {"success": False, "message": "preview_id requires apply without edits."}
        if not isinstance(preview_id, str) or not preview_id.strip() or len(preview_id) > 64:
            return {"success": False, "message": "preview_id must contain 1-64 characters."}
        params = {"action": action, "preview_id": preview_id, "undo_name": undo_name}
    else:
        try:
            parsed = _EDITS.validate_python(edits)
            if not 1 <= len(parsed) <= 64:
                return {"success": False, "message": "edits must contain 1-64 entries."}
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
            if len(json.dumps(params, allow_nan=False, ensure_ascii=True)) > 65536:
                return {"success": False, "message": "Scene edit request exceeds 65536 characters."}
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
