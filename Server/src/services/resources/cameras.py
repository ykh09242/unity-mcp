from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from fastmcp import Context, FastMCP
from fastmcp.exceptions import AuthorizationError

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.registry.action_catalog import OBSERVATION_META
from services.registry.capability_catalog import CAMERA_INVENTORY_URI
from services.registry.editor_action_guidance import CAMERA_INVENTORY_OBSERVATION
from services.tools import get_unity_instance_from_context
from transport.protocol import internal_resource_catalog
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


_projected_camera_target: ContextVar[tuple[FastMCP, str] | None] = ContextVar(
    "projected_camera_target", default=None
)


@contextmanager
def preserve_camera_inventory_target(server: FastMCP, instance: str) -> Iterator[None]:
    """Keep the tool request's resolved target through the nested resource read."""
    token = _projected_camera_target.set((server, instance))
    try:
        yield
    finally:
        _projected_camera_target.reset(token)


def projected_camera_target(ctx: Context) -> str | None:
    scoped = _projected_camera_target.get()
    if scoped is not None and ctx.fastmcp is scoped[0]:
        return scoped[1]
    return None


async def camera_inventory_available(ctx: Context) -> bool:
    """Apply the resource list and URI-specific read policies to the fixed tool."""
    server = ctx.fastmcp
    try:
        with internal_resource_catalog(server):
            listed = await server.list_resources()
        if not any(str(resource.uri) == CAMERA_INVENTORY_URI for resource in listed):
            return False
        if await server.get_resource(CAMERA_INVENTORY_URI) is None:
            return False
        await server._check_resource_access(CAMERA_INVENTORY_URI)
    except AuthorizationError:
        return False
    return True


@mcp_for_unity_resource(
    uri=CAMERA_INVENTORY_URI,
    name="cameras",
    meta={OBSERVATION_META: CAMERA_INVENTORY_OBSERVATION},
    description=(
        "Read the current scene camera inventory. Count the unityCameras and cinemachineCameras "
        "entries and read their names. Includes brain state, Cinemachine priorities, pipeline "
        "components, follow/lookAt targets and Unity Camera info."
    ),
)
async def get_cameras(ctx: Context) -> MCPResponse:
    """Get all cameras in the scene."""
    unity_instance = await get_unity_instance_from_context(ctx)
    projected = projected_camera_target(ctx)
    if projected is not None and projected != unity_instance:
        raise AuthorizationError("Scene camera inventory target changed")
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_cameras",
        {},
    )
    return parse_resource_response(response, MCPResponse)
