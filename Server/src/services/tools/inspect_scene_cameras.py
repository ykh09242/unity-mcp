"""Tool-only access to the native scene camera inventory."""

from fastmcp import Context
from fastmcp.exceptions import AuthorizationError
from mcp.types import ToolAnnotations

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.registry.action_catalog import OBSERVATION_META
from services.registry.capability_catalog import CAMERA_INVENTORY_TOOL, CAMERA_INVENTORY_URI
from services.registry.editor_action_guidance import CAMERA_INVENTORY_OBSERVATION
from services.resources.cameras import camera_inventory_available, get_cameras


@mcp_for_unity_tool(
    name=CAMERA_INVENTORY_TOOL,
    group="core",
    unity_target=None,
    description=(
        "Read the selected Editor's scene camera inventory. Count entries and read names in "
        "unityCameras and cinemachineCameras. Includes brain state, lens information, "
        "Cinemachine priorities, pipeline components and follow/lookAt targets. "
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
async def inspect_scene_cameras(ctx: Context) -> MCPResponse:
    if not await camera_inventory_available(ctx):
        raise AuthorizationError("Scene camera inventory is unavailable")
    return await get_cameras(ctx)
