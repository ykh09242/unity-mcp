"""Tool-only access to the native scene camera inventory."""

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.registry.action_catalog import OBSERVATION_META
from services.registry.capability_catalog import CAMERA_INVENTORY_TOOL, CAMERA_INVENTORY_URI
from services.registry.editor_action_guidance import CAMERA_INVENTORY_OBSERVATION
from services.resource_inventory import read_inventory


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
    return await read_inventory(ctx, CAMERA_INVENTORY_URI, "scene camera inventory")
