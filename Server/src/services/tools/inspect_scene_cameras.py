"""Tool-only access to the native scene camera inventory."""

import json

from fastmcp import Context
from fastmcp.exceptions import AuthorizationError, ToolError
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.registry.action_catalog import OBSERVATION_META
from services.registry.capability_catalog import CAMERA_INVENTORY_TOOL, CAMERA_INVENTORY_URI
from services.registry.editor_action_guidance import CAMERA_INVENTORY_OBSERVATION
from services.resources.cameras import (
    camera_inventory_available,
    preserve_camera_inventory_target,
)
from services.tools import get_unity_instance_from_context


@mcp_for_unity_tool(
    name=CAMERA_INVENTORY_TOOL,
    group="core",
    unity_target=None,
    description=(
        "Read the selected Editor's scene camera inventory. unityCameras includes each camera's "
        "name, depth, fieldOfView and brain attachment. cinemachineCameras includes names, "
        "priorities, pipeline components and follow/lookAt targets. "
        "With Cinemachine installed, reads provider methods and component getters. "
        f"Equivalent to {CAMERA_INVENTORY_URI}."
    ),
    meta={OBSERVATION_META: CAMERA_INVENTORY_OBSERVATION},
    annotations=ToolAnnotations(
        title="Inspect Scene Cameras",
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    ),
)
async def inspect_scene_cameras(ctx: Context) -> dict:
    instance = await get_unity_instance_from_context(ctx)
    if not isinstance(instance, str) or not instance:
        raise ToolError("Select a Unity instance to read the scene camera inventory")
    if not await camera_inventory_available(ctx):
        raise AuthorizationError("Scene camera inventory is unavailable")
    with preserve_camera_inventory_target(ctx.fastmcp, instance):
        result = await ctx.fastmcp.read_resource(CAMERA_INVENTORY_URI)
    message = "Scene camera inventory did not return a single JSON object"
    if len(result.contents) != 1 or not isinstance(result.contents[0].content, str):
        raise ToolError(message)
    try:
        payload = json.loads(result.contents[0].content)
    except (ValueError, RecursionError) as error:
        raise ToolError(message) from error
    if not isinstance(payload, dict):
        raise ToolError(message)
    return payload
