from fastmcp import Context
from fastmcp.exceptions import AuthorizationError

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.registry.action_catalog import OBSERVATION_META, SEARCH_TERMS_META
from services.registry.capability_catalog import MENU_DECLARATIONS_URI
from services.registry.integration_action_guidance import MENU_INVENTORY_OBSERVATION
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.protocol import internal_resource_read_target


class GetMenuItemsResponse(MCPResponse):
    data: list[str] = []


@mcp_for_unity_resource(
    uri=MENU_DECLARATIONS_URI,
    name="menu_items",
    meta={
        OBSERVATION_META: MENU_INVENTORY_OBSERVATION,
        SEARCH_TERMS_META: {
            "anchor": "menu",
            "phrase": "find registered editor menu command path by name; locate full declared menu paths",
        },
    },
    description="Lists managed MenuItem attribute declarations; this is not a complete native menu inventory or availability report.\n\nURI: mcpforunity://menu-items",
)
async def get_menu_items(ctx: Context) -> GetMenuItemsResponse | MCPResponse:
    """List managed menu declarations without claiming native inventory completeness."""
    unity_instance = await get_unity_instance_from_context(ctx)
    projected = internal_resource_read_target(ctx, MENU_DECLARATIONS_URI)
    if projected is not None and projected != unity_instance:
        raise AuthorizationError("Menu declaration inventory target changed")
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
