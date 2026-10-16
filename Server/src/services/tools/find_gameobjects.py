"""Legacy lookup and opt-in bounded, scene-scoped GameObject search."""

from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry
from services.tools.utils import coerce_bool, coerce_int
from services.tools.preflight import preflight


_SEARCH_METHODS = {"by_name", "by_tag", "by_layer", "by_component", "by_path", "by_id"}


def _scoped_options(search_term, search_method, conditions, page_size, cursor, max_depth):
    """Validate before preflight; never coerce an opaque cursor or truncate a predicate."""
    page_size = 50 if page_size is None else page_size
    max_depth = 64 if max_depth is None else max_depth
    if type(page_size) is not int or not 1 <= page_size <= 500:
        raise ValueError("page_size must be an integer between 1 and 500.")
    if type(max_depth) is not int or not 0 <= max_depth <= 128:
        raise ValueError("max_depth must be an integer between 0 and 128.")
    if cursor is not None and (
        type(cursor) is not str
        or len(cursor) != 32
        or any(char not in "0123456789abcdefABCDEF" for char in cursor)
    ):
        raise ValueError(
            "cursor must be an opaque 32-hex Editor-session token; restart the search."
        )
    if conditions is not None and (type(conditions) is not list or len(conditions) > 8):
        raise ValueError("conditions must be an array of at most eight predicates.")
    if search_term is not None and type(search_term) is not str:
        raise ValueError("search_term must be a string when supplied.")
    predicates = []
    if search_term:
        predicates.append({"search_method": search_method, "search_term": search_term})
    predicates.extend(conditions or [])
    if not 1 <= len(predicates) <= 8:
        raise ValueError(
            "Scoped searches require one to eight predicates including the top-level search."
        )
    for predicate in predicates:
        if type(predicate) is not dict or set(predicate) != {"search_method", "search_term"}:
            raise ValueError("Each condition must contain exactly search_method and search_term.")
        method, term = predicate["search_method"], predicate["search_term"]
        if type(method) is not str or method not in _SEARCH_METHODS:
            raise ValueError("Unknown scoped search_method.")
        if (
            type(term) is not str
            or not term
            or len(term.encode("utf-16-le", errors="surrogatepass")) > 1024
        ):
            raise ValueError("Every term must contain 1 to 512 UTF-16 units.")
    return page_size, cursor, max_depth


@mcp_for_unity_tool(
    description=(
        "Search GameObjects by name, tag, layer, component, path or ID; returns IDs only. "
        "scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. "
        "active uses SceneManager's reported active scene; all_loaded covers loaded ordinary scenes, "
        "excluding preview/prefab stages. Scoped conditions are ANDed (at most eight predicates total). "
        "Scoped pages use opaque cursors and require identical options and unchanged scenes. "
        "Empty continuation pages are valid: hasMore means remaining scan work, not more matches. "
        "totalCount is unknown until complete and then exact only through max_depth (default64, max128). "
        "Limits: 32 scene entries, 4096 roots/scene, one root enumeration/page, 4096 managed traversal "
        "steps/page, 500 IDs and 16384 output bytes. Root preparation is accounted separately; "
        "native Unity calls are synchronous and not time-bounded. Restart after edits, expiry or reload. "
        "Use mcpforunity://scene/gameobject/{id} for details or manage_gameobject for edits."
    ),
    annotations=ToolAnnotations(
        title="Find GameObjects",
        # Preflight may refresh assets and trigger a domain reload, invalidating scoped cursors.
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def find_gameobjects(
    ctx: Context,
    search_term: Annotated[
        str | None, Field(description="Search value; optional for scoped conditions-only queries")
    ] = None,
    search_method: Annotated[
        Literal["by_name", "by_tag", "by_layer", "by_component", "by_path", "by_id"],
        Field(default="by_name", description="How to search for GameObjects"),
    ] = "by_name",
    include_inactive: Annotated[
        bool | str | None,
        Field(default=None, description="Include inactive objects across the selected scope"),
    ] = None,
    page_size: Annotated[
        int | str | None,
        Field(
            default=None,
            description="Default50; scoped mode requires integer1..500; legacy keeps coercion/clamping",
        ),
    ] = None,
    cursor: Annotated[
        int | str | None,
        Field(
            default=None,
            description="Legacy numeric offset or scoped opaque32-hex continuation token",
        ),
    ] = None,
    scene_scope: Annotated[
        Literal["legacy", "active", "all_loaded"],
        Field(
            description="Explicit scene selection; legacy preserves existing method-dependent scope"
        ),
    ] = "legacy",
    conditions: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description="AND predicates with exactly search_method and search_term; max8 including top-level search"
        ),
    ] = None,
    max_depth: Annotated[
        int | None, Field(description="Scoped inclusive depth: root0, default64, maximum128")
    ] = None,
) -> dict[str, Any]:
    """Preserve legacy payloads; validate scoped options before refresh-capable preflight."""
    if scene_scope not in ("legacy", "active", "all_loaded"):
        return {"success": False, "message": "scene_scope must be legacy, active or all_loaded."}
    if scene_scope == "legacy":
        if conditions is not None or max_depth is not None:
            return {
                "success": False,
                "message": "conditions and max_depth require scene_scope active or all_loaded.",
            }
        if not search_term:
            return {
                "success": False,
                "message": "Missing required parameter 'search_term'. Specify what to search for.",
            }
    try:
        include_inactive = coerce_bool(include_inactive, default=False)
    except ValueError as exc:
        return {"success": False, "message": f"Invalid 'include_inactive': {exc}"}
    if scene_scope == "legacy":
        try:
            page_size = coerce_int(page_size, default=50)
        except ValueError as exc:
            return {"success": False, "message": f"Invalid 'page_size': {exc}"}
        try:
            cursor = coerce_int(cursor, default=0)
        except ValueError as exc:
            return {"success": False, "message": f"Invalid 'cursor': {exc}"}
    else:
        try:
            page_size, cursor, max_depth = _scoped_options(
                search_term, search_method, conditions, page_size, cursor, max_depth
            )
        except ValueError as exc:
            return {"success": False, "message": str(exc)}

    unity_instance = await get_unity_instance_from_context(ctx)
    gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=True)
    if gate is not None:
        return gate.model_dump()

    try:
        params = {
            "searchMethod": search_method,
            "searchTerm": search_term,
            "includeInactive": include_inactive,
            "pageSize": page_size,
            "cursor": cursor,
        }
        if scene_scope != "legacy":
            params.update(scene_scope=scene_scope, conditions=conditions, max_depth=max_depth)
        params = {key: value for key, value in params.items() if value is not None}
        response = await send_with_unity_instance(
            async_send_command_with_retry, unity_instance, "find_gameobjects", params
        )
        if isinstance(response, dict) and response.get("success"):
            return {
                "success": True,
                "message": response.get("message", "Search completed."),
                "data": response.get("data"),
            }
        return (
            response if isinstance(response, dict) else {"success": False, "message": str(response)}
        )
    except Exception as exc:
        return {"success": False, "message": f"Error searching GameObjects: {exc!s}"}
