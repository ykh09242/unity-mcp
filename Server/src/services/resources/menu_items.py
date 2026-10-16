from fastmcp import Context

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


class GetMenuItemsResponse(MCPResponse):
    data: list[str] = []


@mcp_for_unity_resource(
    uri="mcpforunity://menu-items",
    name="menu_items",
    description="Lists managed MenuItem attribute declarations; this is not a complete native menu inventory or availability report.\n\nURI: mcpforunity://menu-items",
)
async def get_menu_items(ctx: Context) -> GetMenuItemsResponse | MCPResponse:
    """List managed menu declarations without claiming native inventory completeness."""
    unity_instance = await get_unity_instance_from_context(ctx)
    params = {
        "refresh": False,
        "search": "",
    }

    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_menu_items",
        params,
    )
    return parse_resource_response(response, GetMenuItemsResponse)
