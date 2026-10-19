import json
from typing import Annotated, Any, Literal

from pydantic import JsonValue

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import normalize_properties
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

ANIMATOR_ACTIONS = [
    "animator_get_info",
    "animator_get_parameter",
    "animator_play",
    "animator_crossfade",
    "animator_set_parameter",
    "animator_set_speed",
    "animator_set_enabled",
]

CONTROLLER_ACTIONS = [
    "controller_get_graph",
    "controller_modify_state",
    "controller_delete_state",
    "controller_modify_transition",
    "controller_delete_transition",
    "controller_create",
    "controller_add_state",
    "controller_add_transition",
    "controller_add_parameter",
    "controller_get_info",
    "controller_assign",
    "controller_add_layer",
    "controller_remove_layer",
    "controller_set_layer_weight",
    "controller_create_blend_tree_1d",
    "controller_create_blend_tree_2d",
    "controller_add_blend_tree_child",
]

CLIP_ACTIONS = [
    "clip_get_curves",
    "clip_get_curve",
    "clip_replace_curve",
    "clip_delete_curve",
    "clip_create",
    "clip_get_info",
    "clip_add_curve",
    "clip_set_curve",
    "clip_set_vector_curve",
    "clip_create_preset",
    "clip_assign",
    "clip_add_event",
    "clip_remove_event",
]

ALL_ACTIONS = (
    ANIMATOR_ACTIONS + CONTROLLER_ACTIONS + CLIP_ACTIONS
)  # Not loaded in the MCP context, but will return this in the error response (1 Shot)


_ROUND_TRIPS = {
    "clip_get_curves",
    "clip_get_curve",
    "clip_replace_curve",
    "clip_delete_curve",
    "controller_get_graph",
    "controller_modify_state",
    "controller_delete_state",
    "controller_modify_transition",
    "controller_delete_transition",
}


def _round_trip_error(action: str, properties: dict[str, JsonValue] | None) -> str | None:
    """Check bounded request envelopes; Unity owns exact asset and field validation."""
    if action not in _ROUND_TRIPS:
        return None
    values = properties or {}
    try:
        if len(json.dumps(values, allow_nan=False, ensure_ascii=True)) > 1_048_576:
            return "Animation round-trip request exceeds 1 MiB."
    except (ValueError, TypeError):
        return "Animation round trips require finite JSON-compatible values."
    offset = values.get("offset", 0)
    size = values.get("page_size", values.get("pageSize", 50))
    if type(offset) is not int or not 0 <= offset <= 16384:
        return "offset must be a nonnegative integer at most 16384."
    if type(size) is not int or not 1 <= size <= 100:
        return "page_size must be an integer between 1 and 100."
    revision = values.get("expected_revision", values.get("expectedRevision"))
    write = action in {
        "clip_replace_curve",
        "clip_delete_curve",
        "controller_modify_state",
        "controller_delete_state",
        "controller_modify_transition",
        "controller_delete_transition",
    }
    if (write or offset > 0) and (
        type(revision) is not str or not revision or len(revision) > 1024
    ):
        return "Writes and continuation pages require expected_revision from a fresh read."
    selectors = {
        "clip_get_curve": ("binding_id", "bindingId"),
        "clip_replace_curve": ("binding_id", "bindingId"),
        "clip_delete_curve": ("binding_id", "bindingId"),
        "controller_modify_state": ("state_id", "stateId"),
        "controller_delete_state": ("state_id", "stateId"),
        "controller_modify_transition": ("transition_id", "transitionId"),
        "controller_delete_transition": ("transition_id", "transitionId"),
    }
    if action in selectors:
        snake, camel = selectors[action]
        identity = values.get(snake, values.get(camel))
        if type(identity) is not str or not identity or len(identity) > 1024:
            return f"{snake} must be an exact identity string from a fresh read; names are not selectors."
        if snake in values and camel in values and values[snake] != values[camel]:
            return f"Conflicting aliases for {snake}."
    if action == "clip_replace_curve":
        keys = values.get("keys")
        if type(keys) is not list or not 1 <= len(keys) <= 1024:
            return "keys must contain 1-1024 replacements; use clip_delete_curve to remove a curve."
    if action in {"controller_modify_state", "controller_modify_transition"}:
        changes = values.get("changes")
        if type(changes) is not dict or not 1 <= len(changes) <= 16:
            return "changes must contain 1-16 supported fields."
    return None


@mcp_for_unity_tool(
    group="animation",
    description=(
        "Manage Unity animation: Animator control and AnimationClip creation. "
        "Action prefixes: animator_* (play, crossfade, set parameters, get info), "
        "controller_* (create AnimatorControllers, add states/transitions/parameters), "
        "clip_* (create clips, add keyframe curves, assign to GameObjects). "
        "Action-specific parameters go in `properties` (keys match ManageAnimation.cs). "
        "Round trips: clip_get_curves lists binding_id/kind/path/property/type; clip_get_curve returns "
        "paged full float keys (tangents, weights, tangent modes) or exact asset object-reference keys "
        "{guid,file_id}. clip_replace_curve replaces one existing binding with 1-1024 ordered keys; "
        "clip_delete_curve removes that binding. Both require binding_id and expected_revision. "
        "clip_file_id selects an exact clip subasset; imported clips are read-only. "
        "controller_get_graph pages nested state machines, states and transition identities. "
        "controller_modify_state/delete_state require state_id; controller_modify_transition/delete_transition "
        "require transition_id. Writes require expected_revision and modify uses changes. State changes "
        "support name/speed/exact motion refs; transition changes support duration/exit_time/offset/flags, "
        "conditions and exact destination_state_id. State deletion refuses default or referenced states. "
        "Pages use offset/page_size (1-100), with expected_revision required after offset zero. "
        "New actions are bounded to contained Assets sources, support native Undo and preserve GUID/file IDs; "
        "no arbitrary callback/filesystem transaction is promised."
    ),
    annotations=ToolAnnotations(
        title="Manage Animation",
        destructiveHint=True,
    ),
)
async def manage_animation(
    ctx: Context,
    action: Annotated[str, "Action to perform (prefix: animator_, controller_, clip_)."],
    target: Annotated[str | None, "Target GameObject (name/path/id)."] = None,
    search_method: Annotated[
        Literal["by_id", "by_name", "by_path", "by_tag", "by_layer"] | None,
        "How to find the target GameObject.",
    ] = None,
    clip_path: Annotated[
        str | None, "Asset path for AnimationClip (e.g. 'Assets/Animations/Walk.anim')."
    ] = None,
    controller_path: Annotated[
        str | None, "Asset path for AnimatorController (e.g. 'Assets/Animators/Player.controller')."
    ] = None,
    properties: Annotated[
        dict[str, Any] | str | None,
        "Action-specific parameters (dict or JSON object string).",
    ] = None,
) -> dict[str, Any]:
    """Unified animation management tool."""

    action_normalized = action.lower()

    if action_normalized not in ALL_ACTIONS:
        prefix = action_normalized.split("_")[0] + "_" if "_" in action_normalized else ""
        available_by_prefix = {
            "animator_": ANIMATOR_ACTIONS,
            "controller_": CONTROLLER_ACTIONS,
            "clip_": CLIP_ACTIONS,
        }
        suggestions = available_by_prefix.get(prefix, [])
        if suggestions:
            return {
                "success": False,
                "message": f"Unknown action '{action}'. Available {prefix}* actions: {', '.join(suggestions)}",
            }
        else:
            return {
                "success": False,
                "message": (
                    f"Unknown action '{action}'. Use prefixes: "
                    "animator_* (Animator control), controller_* (AnimatorController CRUD), "
                    "clip_* (AnimationClip operations)."
                ),
            }

    properties, properties_error = normalize_properties(properties)
    if properties_error:
        return {"success": False, "message": properties_error}

    round_trip_error = _round_trip_error(action_normalized, properties)
    if round_trip_error:
        return {"success": False, "message": round_trip_error}

    unity_instance = await get_unity_instance_from_context(ctx)

    params_dict: dict[str, Any] = {"action": action_normalized}
    if properties is not None:
        params_dict["properties"] = properties
    if target is not None:
        params_dict["target"] = target
    if search_method is not None:
        params_dict["searchMethod"] = search_method
    if clip_path is not None:
        params_dict["clipPath"] = clip_path
    if controller_path is not None:
        params_dict["controllerPath"] = controller_path

    params_dict = {k: v for k, v in params_dict.items() if v is not None}

    result = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_animation",
        params_dict,
    )

    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
