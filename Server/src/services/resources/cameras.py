from fastmcp import Context
from fastmcp.exceptions import AuthorizationError

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.registry.action_catalog import OBSERVATION_META
from services.registry.capability_catalog import CAMERA_INVENTORY_TOOL, CAMERA_INVENTORY_URI
from services.registry.editor_action_guidance import CAMERA_INVENTORY_OBSERVATION
from services.tools import get_unity_instance_from_context
from transport.protocol import internal_resource_read_target
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


@mcp_for_unity_resource(
    uri=CAMERA_INVENTORY_URI,
    name="cameras",
    inventory_reader=CAMERA_INVENTORY_TOOL,
    meta={
        OBSERVATION_META: CAMERA_INVENTORY_OBSERVATION,
    },
    description=(
        "Read the current scene camera inventory. Count the unityCameras and cinemachineCameras "
        "entries and read their names. Includes brain state, Cinemachine priorities, pipeline "
        "components, follow/lookAt targets and Unity Camera info."
    ),
)
async def get_cameras(ctx: Context) -> MCPResponse:
    """Get all cameras in the scene."""
    unity_instance = await get_unity_instance_from_context(ctx)
    projected = internal_resource_read_target(ctx, CAMERA_INVENTORY_URI)
    if projected is not None and projected != unity_instance:
        raise AuthorizationError("Scene camera inventory target changed")
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_cameras",
        {},
    )
    return parse_resource_response(response, MCPResponse)
