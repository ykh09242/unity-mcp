"""Bounded session handles to original references; no persistent identity or rediscovery."""

from typing import Annotated, Any, Literal
import re

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field, StrictBool, StrictInt, StrictStr
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

_OMITTED = object()
_DEFAULT = Field(default_factory=lambda: _OMITTED)


def _omitted(value: Any) -> bool:
    return value is _OMITTED or value is _DEFAULT


def validate_handle(handle: Any) -> bool:
    """Validate v2 transport shape; Unity owns the opaque reference registry."""
    return (
        type(handle) is str
        and len(handle) <= 1024
        and re.fullmatch(r"mcp-oh-v2\.[0-9a-f]{32}_[0-9a-f]{32}", handle, flags=re.ASCII)
        is not None
    )


@mcp_for_unity_tool(
    unity_target="manage_object_handles_v2",
    description=(
        "Capture or resolve the original loaded GameObject/Component reference for this Editor "
        "session. Capture accepts only a nonzero live instance_id; resolve accepts only handle. "
        "All handles, including clean saved scenes, expire after 600 seconds or earlier eviction "
        "from a 512-entry weak cache. Scene close/open/new-scene events, domain reload/Editor "
        "restart, every Play transition and Undo/Redo expire all handles. Direct rename, property "
        "edits, same-scene reparenting and save retain the original reference. Resolution requires "
        "its original loaded owner/scene/prefab-stage context; observed context mismatch is final. "
        "An unobserved scene move and return may retain the same original reference. Current "
        "prefab-stage and runtime scene objects are supported; persistent asset objects are not. "
        "Never loads assets/scenes, saves, scans or falls back to serialized IDs, names or paths. "
        "require_persistent and allow_asset_load are unsupported, including false: omit both. "
        "On stale_handle rediscover explicitly and capture again. Requires v2 Editor commands; "
        "older Editors fail closed. Returned instance IDs remain ephemeral; no reload retry."
    ),
    annotations=ToolAnnotations(
        title="Manage Object Handles", readOnlyHint=True, destructiveHint=False, openWorldHint=False
    ),
)
async def manage_object_handles(
    ctx: Context,
    action: Literal["capture", "resolve"],
    instance_id: Annotated[StrictInt, Field(ge=-2147483648, le=2147483647)] = _DEFAULT,
    handle: Annotated[StrictStr, Field(max_length=1024)] = _DEFAULT,
    require_persistent: Annotated[
        StrictBool,
        Field(
            description="Unsupported legacy field: omit entirely. Any presence, including false, fails."
        ),
    ] = _DEFAULT,
    allow_asset_load: Annotated[
        StrictBool,
        Field(
            description="Unsupported legacy field: omit entirely. Any presence, including false, fails."
        ),
    ] = _DEFAULT,
) -> dict[str, Any]:
    def invalid(message):
        return {
            "success": False,
            "code": "invalid_parameters",
            "error": "invalid_parameters",
            "data": {"reason": "invalid_parameters", "message": message},
        }

    if not _omitted(require_persistent) or not _omitted(allow_asset_load):
        return invalid("require_persistent and allow_asset_load are unsupported; omit both fields.")
    if action == "capture":
        if not _omitted(handle):
            return invalid("capture accepts only instance_id.")
        if (
            type(instance_id) is not int
            or instance_id == 0
            or not -2147483648 <= instance_id <= 2147483647
        ):
            return invalid("instance_id must be a nonzero Int32 integer.")
        params = {"action": action, "instance_id": instance_id}
    elif action == "resolve":
        if not _omitted(instance_id):
            return invalid("resolve accepts only handle.")
        if not validate_handle(handle):
            return invalid("handle must be a bounded opaque object-handle string.")
        params = {"action": action, "handle": handle}
    else:
        return invalid("Use capture or resolve.")
    instance = await get_unity_instance_from_context(ctx)
    gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=False)
    if gate is not None:
        return gate.model_dump()
    return await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "manage_object_handles_v2",
        params,
        retry_on_reload=False,
    )
