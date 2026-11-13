from typing import Annotated, Literal

from pydantic import BaseModel, Field
from fastmcp import Context

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


class SelectionObjectInfo(BaseModel):
    """Information about a selected object."""

    name: str | None = None
    type: str | None = None
    instanceID: int | None = None


class SelectionGameObjectInfo(BaseModel):
    """Information about a selected GameObject."""

    name: str | None = None
    instanceID: int | None = None


class SelectionData(BaseModel):
    """Selection data fields."""

    activeObject: str | None = None
    activeGameObject: str | None = None
    activeTransform: str | None = None
    activeInstanceID: int = 0
    count: int = 0
    objects: list[SelectionObjectInfo] = []
    gameObjects: list[SelectionGameObjectInfo] = []
    assetGUIDs: list[str] = []


class SelectionResponse(MCPResponse):
    """Detailed information about the current editor selection."""

    data: SelectionData = SelectionData()


@mcp_for_unity_resource(
    uri="mcpforunity://editor/selection",
    name="editor_selection",
    description="Detailed information about currently selected objects in the editor, including GameObjects, assets, and their properties.\n\nURI: mcpforunity://editor/selection",
)
async def get_selection(ctx: Context) -> SelectionResponse | MCPResponse:
    """Get detailed editor selection information."""
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "get_selection", {}
    )
    return parse_resource_response(response, SelectionResponse)


@mcp_for_unity_resource(
    uri="mcpforunity://editor/selection/page{?page_size,cursor,fields}",
    name="editor_selection_page",
    description=(
        "Bounded Editor selection with active metadata and object/GameObject/asset GUID counts. "
        "page_size defaults to 50 (1-500). fields selects objects (default), gameObjects, "
        "assetGUIDs, all, or none for active metadata/counts only. Each requested collection "
        "returns at most page_size entries; names are capped at 256 UTF-16 code units. "
        "Only page entries require object descriptions; Unity still allocates selection arrays. "
        "Follow nextCursor using identical fields/page_size within 30 seconds. Selection "
        "changes, reload, expiry or cursor eviction require restarting. "
        "The legacy mcpforunity://editor/selection URI remains available."
    ),
)
async def get_selection_page(
    ctx: Context,
    page_size: Annotated[int, Field(ge=1, le=500)] = 50,
    cursor: Annotated[str | None, Field(max_length=32)] = None,
    fields: Literal["objects", "gameObjects", "assetGUIDs", "all", "none"] = "objects",
) -> MCPResponse:
    """Read a bounded projection without materializing every object's description."""
    if type(page_size) is not int or not 1 <= page_size <= 500:
        return MCPResponse(success=False, error="invalid_selection_page")
    if fields not in {"objects", "gameObjects", "assetGUIDs", "all", "none"}:
        return MCPResponse(success=False, error="invalid_selection_fields")
    if cursor is not None and (
        type(cursor) is not str
        or len(cursor) != 32
        or any(character not in "0123456789abcdef" for character in cursor)
    ):
        return MCPResponse(success=False, error="stale_cursor")
    params = {"paged": True, "page_size": page_size, "fields": fields}
    if cursor is not None:
        params["cursor"] = cursor
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "get_selection", params
    )
    return parse_resource_response(response, MCPResponse)
