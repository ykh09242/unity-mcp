from fastmcp import Context

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


class LayersResponse(MCPResponse):
    """Dictionary of layer indices to layer names."""

    data: dict[int, str] = {}


@mcp_for_unity_resource(
    uri="mcpforunity://project/layers",
    name="project_layers",
    description=(
        "List the name and number of every named layer in the project. "
        "The result maps layer indices (0-31) to names and omits unused slots."
    ),
)
async def get_layers(ctx: Context) -> LayersResponse | MCPResponse:
    """Get all project layers with their indices."""
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "get_layers", {}
    )
    return parse_resource_response(response, LayersResponse)
