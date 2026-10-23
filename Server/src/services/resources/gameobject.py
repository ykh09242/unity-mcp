"""
MCP Resources for reading GameObject data from Unity scenes.

These resources provide read-only access to:
- Single GameObject data (mcpforunity://scene/gameobject/{id})
- All components on a GameObject (mcpforunity://scene/gameobject/{id}/components)
- Single component on a GameObject (mcpforunity://scene/gameobject/{id}/component/{name})
"""

from typing import Annotated, Any
import re
from pydantic import BaseModel
from fastmcp import Context

from models import MCPResponse
from services.registry import mcp_for_unity_resource
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


def _normalize_response(response: dict | Any) -> MCPResponse:
    """Normalize Unity transport response to MCPResponse."""
    if isinstance(response, dict):
        return MCPResponse(**response)
    return response


def _validate_instance_id(instance_id: str) -> tuple[int | None, MCPResponse | None]:
    """
    Validate and convert instance_id string to int.
    Returns (id_int, None) on success or (None, error_response) on failure.
    """
    try:
        return int(instance_id), None
    except ValueError:
        return None, MCPResponse(success=False, error=f"Invalid instance ID: {instance_id}")


# =============================================================================
# Static Helper Resource (shows in UI)
# =============================================================================


@mcp_for_unity_resource(
    uri="mcpforunity://scene/gameobject-api",
    name="gameobject_api",
    description="Documentation for GameObject resources. Use find_gameobjects tool to get instance IDs, then access resources below.\n\nURI: mcpforunity://scene/gameobject-api",
)
async def get_gameobject_api_docs(_ctx: Context) -> MCPResponse:
    """
    Returns documentation for the GameObject resource API.

    This is a helper resource that explains how to use the parameterized
    GameObject resources which require an instance ID.
    """
    docs = {
        "overview": "GameObject resources provide read-only access to Unity scene objects.",
        "workflow": [
            "1. Use find_gameobjects tool to search for GameObjects and get instance IDs",
            "2. Use the instance ID to access detailed data via resources below",
        ],
        "best_practices": [
            "⚡ Use batch_execute for multiple operations: Combine create/modify/component calls into one batch_execute call for 10-100x better performance",
            "Example: Creating 5 cubes → 1 batch_execute with 5 manage_gameobject commands instead of 5 separate calls",
            "Example: Adding components to 3 objects → 1 batch_execute with 3 manage_components commands",
        ],
        "resources": {
            "mcpforunity://scene/gameobject-by-handle/{handle}": {
                "description": "Read an original loaded GameObject through a v2 session handle from manage_object_handles. Never loads assets/scenes or falls back to a live ID.",
                "note": "Session identity plus bounded GameObject data. Fixed 600-second lifetime, earlier eviction, scene/domain/Play/Undo expiry. Child links retain their live-ID/cursor lifetime. Old peers fail closed.",
            },
            "mcpforunity://scene/gameobject/{instance_id}": {
                "description": "Get basic GameObject data (name, tag, layer, transform, component type list)",
                "example": "mcpforunity://scene/gameobject/-81840",
                "returns": [
                    "instanceID",
                    "name",
                    "tag",
                    "layer",
                    "transform",
                    "componentTypes",
                    "path",
                    "parent",
                    "children",
                ],
            },
            "mcpforunity://scene/gameobject/{instance_id}/children{?page_size,cursor}": {
                "description": "Bounded direct child IDs in sibling order, plus object metadata. Default page size 50, maximum 500.",
                "example": "mcpforunity://scene/gameobject/-81840/children?page_size=25",
                "consistency": "Live indices: stable sources page exactly. Restart after any hierarchy edit; bounded observed-change checks cannot detect every same-frame mutation.",
                "cursor": "Opaque editor-session cursor, expires after 30 seconds. Keep target/page_size unchanged; stale cursors require restart.",
            },
            "mcpforunity://scene/gameobject/{instance_id}/components{?page_size,cursor,include_properties,evaluate_getters}": {
                "description": "Get all components with full property serialization (paginated)",
                "example": "mcpforunity://scene/gameobject/-81840/components?page_size=10&cursor=0&include_properties=false",
                "parameters": {
                    "page_size": "Number of components per page (default: 25)",
                    "cursor": "Pagination offset (default: 0)",
                    "include_properties": "Include fields and safe native properties (default: true)",
                    "evaluate_getters": "Explicitly execute user getters and serialization hooks (default: false)",
                },
            },
            "mcpforunity://scene/gameobject/{instance_id}/component/{component_name}{?component_index,component_instance_id,strict_selection,evaluate_getters}": {
                "description": "Read one live component. Bare URIs preserve legacy case-insensitive first match; selectors or strict_selection=true require a compatible Editor and never fall back.",
                "example": "mcpforunity://scene/gameobject/-81840/component/UnityEngine.AudioSource?component_index=1",
                "parameters": {
                    "component_index": "Zero-based index within GetComponents(requested type), including derived types; not the mixed list index.",
                    "component_instance_id": "Nonzero live component instanceID from /components. Mutually exclusive with component_index.",
                    "strict_selection": "true requires exactly one component when no selector is supplied; default false.",
                },
                "note": "Selected reads use canonical, case-sensitive type names, matching manage_components. IDs and indices are ephemeral; rediscover after reload, removal or reordering.",
            },
        },
        "related_tools": {
            "find_gameobjects": "Search for GameObjects by name, tag, layer, component, or path",
            "manage_components": "Add, remove, or modify components on GameObjects",
            "manage_gameobject": "Create, modify, or delete GameObjects",
        },
    }
    return MCPResponse(success=True, data=docs)


class TransformData(BaseModel):
    """Transform component data."""

    position: dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}
    localPosition: dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}
    rotation: dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}
    localRotation: dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}
    scale: dict[str, float] = {"x": 1.0, "y": 1.0, "z": 1.0}
    lossyScale: dict[str, float] = {"x": 1.0, "y": 1.0, "z": 1.0}


class GameObjectData(BaseModel):
    """Data for a single GameObject (without full component serialization)."""

    instanceID: int
    name: str
    tag: str = "Untagged"
    layer: int = 0
    layerName: str = "Default"
    active: bool = True
    activeInHierarchy: bool = True
    isStatic: bool = False
    transform: TransformData = TransformData()
    parent: int | None = None
    children: list[int] = []
    childrenTotal: int = 0
    childrenPageSize: int = 50
    childrenNextCursor: str | None = None
    childrenTruncated: bool = False
    childrenConsistency: str = "live_indices"
    childrenRestartAdvice: str = "Restart after any hierarchy edit."
    componentTypes: list[str] = []
    path: str = ""


# TODO: Use these typed response classes for better type safety once
# we update the endpoints to validate response structure more strictly.
class GameObjectResponse(MCPResponse):
    """Response containing GameObject data."""

    data: GameObjectData | None = None


@mcp_for_unity_resource(
    uri="mcpforunity://scene/gameobject/{instance_id}",
    name="gameobject",
    description="Get bounded GameObject metadata by instance ID: scalar transforms, parent, first 50 child IDs and up to 64 component type names. Follow childrenNextCursor via /children?page_size=50&cursor=... . Children use live sibling indices; restart after any hierarchy edit.\n\nURI: mcpforunity://scene/gameobject/{instance_id}",
)
async def get_gameobject(ctx: Context, instance_id: str) -> MCPResponse:
    """Get GameObject data by instance ID."""
    unity_instance = await get_unity_instance_from_context(ctx)

    id_int, error = _validate_instance_id(instance_id)
    if error:
        return error

    response = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "get_gameobject", {"instanceID": id_int}
    )

    return _normalize_response(response)


@mcp_for_unity_resource(
    uri="mcpforunity://scene/gameobject/{instance_id}/children{?page_size,cursor}",
    name="gameobject_children",
    description="Read bounded direct child IDs in sibling order plus object metadata. page_size defaults to 50 (1-500). Reuse childrenNextCursor with identical target/page_size within 30 seconds. Stable sources page exactly; live-index cursors perform bounded observed-change checks, so restart after any hierarchy edit. Cursors expire on reload or eviction.",
)
async def get_gameobject_children(
    ctx: Context,
    instance_id: Annotated[str, "Scene GameObject instance ID returned by find_gameobjects."],
    page_size: Annotated[int, "Direct child IDs per page, from 1 to 500; default 50."] = 50,
    cursor: Annotated[
        str | None,
        "Opaque childrenNextCursor from the preceding page; omit to start. Keep the same target and page_size.",
    ] = None,
) -> MCPResponse:
    """Continue the bounded child-ID page returned by the ordinary object resource."""
    id_int, error = _validate_instance_id(instance_id)
    if error:
        return error
    if type(page_size) is not int or not 1 <= page_size <= 500:
        return MCPResponse(success=False, error="page_size must be an integer between 1 and 500.")
    if cursor is not None and (
        type(cursor) is not str
        or len(cursor) != 32
        or any(c not in "0123456789abcdef" for c in cursor)
    ):
        return MCPResponse(
            success=False,
            error="stale_cursor",
            message="Use an opaque child cursor or restart the read.",
        )
    params: dict[str, Any] = {"instanceID": id_int, "children_page_size": page_size}
    if cursor is not None:
        params["children_cursor"] = cursor
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "get_gameobject", params
    )
    return _normalize_response(response)


class ComponentsData(BaseModel):
    """Data for components on a GameObject."""

    gameObjectID: int
    gameObjectName: str
    components: list[Any] = []
    cursor: int = 0
    pageSize: int = 25
    nextCursor: int | None = None
    totalCount: int = 0
    hasMore: bool = False
    includeProperties: bool = True


class ComponentsResponse(MCPResponse):
    """Response containing components data."""

    data: ComponentsData | None = None


def _inspection_evaluation(value: str | bool) -> tuple[bool, MCPResponse | None]:
    if type(value) is bool:
        return value, None
    if type(value) is str and value in {"true", "false"}:
        return value == "true", None
    return False, MCPResponse(
        success=False,
        error="invalid_inspection",
        message="evaluate_getters must be true or false.",
    )


@mcp_for_unity_resource(
    uri="mcpforunity://scene/gameobject/{instance_id}/components{?page_size,cursor,include_properties,evaluate_getters}",
    name="gameobject_components",
    description="Get components on a GameObject. Optional URI query parameters: page_size (default 25), cursor (default 0), include_properties (default true), and evaluate_getters (default false). Default reads use fields and safe native properties. Explicit getter evaluation can run user code and block the Editor.\n\nURI: mcpforunity://scene/gameobject/{instance_id}/components{?page_size,cursor,include_properties,evaluate_getters}",
)
async def get_gameobject_components(
    ctx: Context,
    instance_id: str,
    page_size: int = 25,
    cursor: int = 0,
    include_properties: bool = True,
    evaluate_getters: str | bool = False,
) -> MCPResponse:
    """Read component fields; executing arbitrary user getters requires explicit opt-in."""
    evaluate, error = _inspection_evaluation(evaluate_getters)
    if error:
        return error
    unity_instance = await get_unity_instance_from_context(ctx)

    id_int, error = _validate_instance_id(instance_id)
    if error:
        return error

    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_gameobject_components",
        {
            "instanceID": id_int,
            "pageSize": page_size,
            "cursor": cursor,
            "includeProperties": include_properties,
            **({"evaluateGetters": True} if evaluate else {}),
        },
    )

    return _normalize_response(response)


class SingleComponentData(BaseModel):
    """Data for a single component."""

    gameObjectID: int
    gameObjectName: str
    component: Any = None


class SingleComponentResponse(MCPResponse):
    """Response containing single component data."""

    data: SingleComponentData | None = None


def _component_selector_integer(
    value: str | None, name: str, *, allow_negative: bool
) -> tuple[int | None, MCPResponse | None]:
    """Parse URI decimal selectors without bool/float coercion or silent fallback."""
    if value is None:
        return None, None
    if type(value) is not str or re.fullmatch(r"[+-]?[0-9]+", value.strip()) is None:
        return None, MCPResponse(
            success=False,
            error="invalid_selection",
            message=f"{name} must be a decimal Int32 integer.",
        )
    # Bound significant digits before conversion; leading zeroes do not change Int32 value.
    text = value.strip()
    digits = text.lstrip("+-").lstrip("0") or "0"
    if len(digits) > 10:
        return None, MCPResponse(
            success=False, error="invalid_selection", message=f"{name} is outside the Int32 range."
        )
    number = int(digits) * (-1 if text.startswith("-") else 1)
    if (
        not -2147483648 <= number <= 2147483647
        or (not allow_negative and number < 0)
        or (allow_negative and number == 0)
    ):
        return None, MCPResponse(
            success=False,
            error="invalid_selection",
            message=f"{name} is outside its supported range.",
        )
    return number, None


@mcp_for_unity_resource(
    uri="mcpforunity://scene/gameobject/{instance_id}/component/{component_name}{?component_index,component_instance_id,strict_selection,evaluate_getters}",
    name="gameobject_component",
    description="Get a component on a GameObject. The bare URI preserves legacy case-insensitive first-match reads. Optional component_index or component_instance_id selects one live component; strict_selection=true requires uniqueness. Selected reads use the same canonical, case-sensitive component type as manage_components. Indices are zero-based within GetComponents(requested type), not the mixed component list. Prefer the component instanceID from /components; IDs and indices are ephemeral. Unsupported Editors fail without falling back. Default inspection reads fields and safe native properties. evaluate_getters=true explicitly runs user getters and serialization hooks, which can block the Editor.",
)
async def get_gameobject_component(
    ctx: Context,
    instance_id: str,
    component_name: str,
    component_index: str | None = None,
    component_instance_id: str | None = None,
    strict_selection: str | bool = False,
    evaluate_getters: str | bool = False,
) -> MCPResponse:
    """Read an exact live component when selectors are supplied; preserve the bare URI."""
    evaluate, error = _inspection_evaluation(evaluate_getters)
    if error:
        return error
    if type(strict_selection) is str:
        if strict_selection not in {"true", "false"}:
            return MCPResponse(
                success=False,
                error="invalid_selection",
                message="strict_selection must be true or false.",
            )
        strict = strict_selection == "true"
    elif type(strict_selection) is bool:
        strict = strict_selection
    else:
        return MCPResponse(
            success=False,
            error="invalid_selection",
            message="strict_selection must be true or false.",
        )
    index, error = _component_selector_integer(
        component_index, "component_index", allow_negative=False
    )
    if error:
        return error
    component_id, error = _component_selector_integer(
        component_instance_id, "component_instance_id", allow_negative=True
    )
    if error:
        return error
    if index is not None and component_id is not None:
        return MCPResponse(
            success=False,
            error="invalid_selection",
            message="Use either component_index or component_instance_id, not both.",
        )
    selected = strict or index is not None or component_id is not None
    id_int, error = _validate_instance_id(instance_id)
    if error:
        return error
    if selected and (id_int == 0 or not -2147483648 <= id_int <= 2147483647):
        return MCPResponse(
            success=False,
            error="invalid_target",
            message="instance_id must be a nonzero Int32 integer.",
        )
    params: dict[str, Any] = {"instanceID": id_int, "componentName": component_name}
    if evaluate:
        params["evaluateGetters"] = True
    if index is not None:
        params["componentIndex"] = index
    if component_id is not None:
        params["componentInstanceID"] = component_id
    if strict:
        params["strictSelection"] = True
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_gameobject_component_selected" if selected else "get_gameobject_component",
        params,
        retry_on_reload=not selected,
    )

    return _normalize_response(response)


@mcp_for_unity_resource(
    uri="mcpforunity://scene/gameobject-by-handle/{handle}",
    name="gameobject_by_handle",
    description="Read the original loaded GameObject and bounded metadata from a v2 session handle. Handles expire on scene reopen, domain reload, Play transitions, Undo/Redo, fixed 600-second lifetime or earlier eviction. Never loads scenes/assets; respects the original GameObject resource policy. Requires v2 Editor support.",
)
async def get_gameobject_by_handle(handle: str, ctx: Context) -> dict[str, Any]:
    from services.tools.manage_object_handles import validate_handle

    if not validate_handle(handle):
        return {
            "success": False,
            "code": "invalid_handle",
            "error": "invalid_handle",
            "data": {"reason": "invalid_handle"},
        }
    unity_instance = await get_unity_instance_from_context(ctx)
    return await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_gameobject_by_handle_v2",
        {"handle": handle},
        retry_on_reload=False,
    )
