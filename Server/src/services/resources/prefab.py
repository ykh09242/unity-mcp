"""
MCP Resources for reading Prefab data from Unity.

These resources provide read-only access to:
- Prefab info by asset path (mcpforunity://prefab/{path})
- Prefab hierarchy by asset path (mcpforunity://prefab/{path}/hierarchy)
- Currently open prefab stage (mcpforunity://editor/prefab-stage - see prefab_stage.py)
"""

from typing import Annotated, Any
from pydantic import BaseModel
from fastmcp import Context

from models import MCPResponse
from services.registry import mcp_for_unity_resource
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


def _normalize_response(response: dict | MCPResponse | Any) -> MCPResponse:
    """Normalize Unity transport response to MCPResponse."""
    if isinstance(response, dict):
        return MCPResponse(**response)
    if isinstance(response, MCPResponse):
        return response
    # Fallback: wrap unexpected types in an error response
    return MCPResponse(success=False, error=f"Unexpected response type: {type(response).__name__}")


# =============================================================================
# Static Helper Resource (shows in UI)
# =============================================================================


@mcp_for_unity_resource(
    uri="mcpforunity://prefab-api",
    name="prefab_api",
    description="Documentation for Prefab resources. Use asset_inspect action=search filter_type=Prefab to find prefabs, then access resources below.\n\nURI: mcpforunity://prefab-api",
)
async def get_prefab_api_docs(_ctx: Context) -> MCPResponse:
    """
    Returns documentation for the Prefab resource API.

    This is a helper resource that explains how to use the parameterized
    Prefab resources which require an asset path.
    """
    docs = {
        "overview": "Prefab resources provide read-only access to Unity prefab assets.",
        "workflow": [
            "1. Use asset_inspect action=search filter_type=Prefab to find prefabs",
            "2. Use the asset path to access detailed data via resources below",
            "3. Use prefab_stage action=open_prefab_stage / save_prefab_stage / close_prefab_stage for prefab editing UI transitions",
        ],
        "path_encoding": {
            "note": "Prefab paths must be URL-encoded when used in resource URIs",
            "example": "Assets/Prefabs/MyPrefab.prefab -> Assets%2FPrefabs%2FMyPrefab.prefab",
        },
        "resources": {
            "mcpforunity://prefab/{encoded_path}": {
                "description": "Get prefab asset info (type, root name, components, variant info)",
                "example": "mcpforunity://prefab/Assets%2FPrefabs%2FPlayer.prefab",
                "returns": [
                    "assetPath",
                    "guid",
                    "prefabType",
                    "rootObjectName",
                    "rootComponentTypes",
                    "childCount",
                    "isVariant",
                    "parentPrefab",
                ],
            },
            "mcpforunity://prefab/{encoded_path}/hierarchy{?page_size,cursor,max_depth}": {
                "description": "Get one bounded prefab hierarchy page with nested prefab information",
                "example": "mcpforunity://prefab/Assets%2FPrefabs%2FPlayer.prefab/hierarchy",
                "paging": "page_size defaults to 50 (1-500), max_depth to 64 (0-128). Follow next_cursor with identical path/page_size/max_depth within 30 seconds; restart after source changes, reload or cursor expiry. No automatic aggregation.",
                "returns": [
                    "prefabPath",
                    "total (null until known), total_known",
                    "items (with name, instanceId, path, componentTypes, prefab nesting info)",
                    "next_cursor, truncated, depth_limited, max_depth, page_size",
                ],
            },
            "mcpforunity://editor/prefab-stage": {
                "description": "Get info about the currently open prefab stage (if any)",
                "returns": ["isOpen", "assetPath", "prefabRootName", "mode", "isDirty"],
            },
        },
        "related_tools": {
            "manage_editor": "Editor controls (play/pause/stop, active tool, tags/layers, package deploy/restore)",
            "prefab_stage": "Prefab stage lifecycle (open/save/close)",
            "prefab_inspect": "Inspect prefab assets and bounded hierarchy pages",
            "prefab_contents": "Preview or modify isolated prefab contents",
            "asset_inspect": "Search for prefab assets, get asset info",
            "gameobject_edit": "Modify GameObjects in open prefab stage",
            "manage_components": "Add/remove/modify components on prefab GameObjects",
        },
    }
    return MCPResponse(success=True, data=docs)


# =============================================================================
# Prefab Info Resource
# =============================================================================

# TODO: Use these typed response classes for better type safety once
# we update the endpoints to validate response structure more strictly.


class PrefabInfoData(BaseModel):
    """Data for a prefab asset."""

    assetPath: str
    guid: str = ""
    prefabType: str = "Regular"
    rootObjectName: str = ""
    rootComponentTypes: list[str] = []
    childCount: int = 0
    isVariant: bool = False
    parentPrefab: str | None = None


class PrefabInfoResponse(MCPResponse):
    """Response containing prefab info data."""

    data: PrefabInfoData | None = None


@mcp_for_unity_resource(
    uri="mcpforunity://prefab/{encoded_path}",
    name="prefab_info",
    description="Get detailed information about a prefab asset by URL-encoded path. Returns prefab type, root object name, component types, child count, and variant info.\n\nURI: mcpforunity://prefab/{encoded_path}",
)
async def get_prefab_info(ctx: Context, encoded_path: str) -> MCPResponse:
    """Get prefab asset info using the path already decoded by FastMCP."""
    unity_instance = await get_unity_instance_from_context(ctx)

    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_prefabs",
        {"action": "get_info", "prefabPath": encoded_path},
    )

    return _normalize_response(response)


# =============================================================================
# Prefab Hierarchy Resource
# =============================================================================


class PrefabHierarchyItem(BaseModel):
    """Single item in prefab hierarchy."""

    name: str
    instanceId: int
    path: str
    activeSelf: bool = True
    childCount: int = 0
    componentTypes: list[str] = []
    prefab: dict[str, Any] = {}


class PrefabHierarchyData(BaseModel):
    """Data for prefab hierarchy."""

    prefabPath: str
    total: int | None = None
    total_known: bool = False
    next_cursor: str | None = None
    truncated: bool = False
    depth_limited: bool = False
    max_depth: int = 64
    page_size: int = 50
    items: list[PrefabHierarchyItem] = []


class PrefabHierarchyResponse(MCPResponse):
    """Response containing prefab hierarchy data."""

    data: PrefabHierarchyData | None = None


@mcp_for_unity_resource(
    uri="mcpforunity://prefab/{encoded_path}/hierarchy{?page_size,cursor,max_depth}",
    name="prefab_hierarchy",
    description="Get one bounded prefab hierarchy page with components and nesting information. page_size defaults to 50 (1-500); max_depth defaults to 64 (0-128), root=0. Follow next_cursor with identical path/page_size/max_depth within 30 seconds. total may be null; inspect truncated and depth_limited. Cursors expire on source changes, reload or eviction.\n\nURI: mcpforunity://prefab/{encoded_path}/hierarchy{?page_size,cursor,max_depth}",
)
async def get_prefab_hierarchy(
    ctx: Context,
    encoded_path: str,
    page_size: Annotated[int, "Objects per page, from 1 to 500; default 50."] = 50,
    cursor: Annotated[
        str | None, "Opaque next_cursor; omit to start. Keep identical path/page_size/max_depth."
    ] = None,
    max_depth: Annotated[int, "Inclusive depth scope, root=0; from 0 to 128, default 64."] = 64,
) -> MCPResponse:
    """Read one bounded page using the path already decoded by FastMCP."""
    if type(page_size) is not int or not 1 <= page_size <= 500:
        return MCPResponse(success=False, error="page_size must be an integer between 1 and 500.")
    if type(max_depth) is not int or not 0 <= max_depth <= 128:
        return MCPResponse(success=False, error="max_depth must be an integer between 0 and 128.")
    if cursor is not None and (
        type(cursor) is not str
        or len(cursor) != 32
        or any(c not in "0123456789abcdef" for c in cursor)
    ):
        return MCPResponse(
            success=False,
            error="stale_cursor",
            message="Use an opaque hierarchy cursor or restart the read.",
        )
    unity_instance = await get_unity_instance_from_context(ctx)

    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_prefabs",
        {
            "action": "get_hierarchy",
            "prefabPath": encoded_path,
            "page_size": page_size,
            "max_depth": max_depth,
            **({"cursor": cursor} if cursor is not None else {}),
        },
    )

    return _normalize_response(response)
