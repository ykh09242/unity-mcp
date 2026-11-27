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
from services.tools.preflight import RefreshIfDirty, preflight


_SEARCH_METHODS = {"by_name", "by_tag", "by_layer", "by_component", "by_path", "by_id"}


def validate_exact_scene_path(path: str | None) -> None:
    """Require the canonical scene asset path used by the Editor's exact selector."""
    if (
        type(path) is not str
        or len(path.encode("utf-16-le", errors="surrogatepass")) > 1024
        or not path.startswith(("Assets/", "Packages/"))
        or not path.endswith(".unity")
        or "\\" in path
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in path)
        or any(part in ("", ".", "..") for part in path.split("/"))
    ):
        raise ValueError(
            "scene_path must be a canonical Assets/ or Packages/ .unity path of at most 512 UTF-16 units."
        )


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
    if not predicates and (search_term is not None or search_method != "by_name"):
        raise ValueError("Supply a nonempty search_term for the selected search_method.")
    if len(predicates) > 8:
        raise ValueError(
            "Scoped searches allow at most eight predicates including the top-level search."
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
        "Search GameObjects by name, tag, layer, component, path or ID; defaults to IDs only. "
        "Optional projection fields name/active/transform/components/handle describe the same returned IDs, "
        "capped at 64 rows, 16 KiB and 16 component names per row; projection_verification reports incomplete coverage. "
        "scene_scope=legacy (default) preserves method-dependent scene selection and numeric offsets. "
        "active uses SceneManager's reported active scene; all_loaded covers loaded ordinary scenes. "
        "exact requires scene_path and reports resultScene. Scoped modes exclude preview/prefab stages. "
        "Omit filters to list objects in an explicit scene scope. Supplied filters are ANDed (at most eight predicates total). "
        "Scoped pages use opaque cursors and require identical options and unchanged scenes. "
        "Empty continuation pages are valid: hasMore means remaining scan work, not more matches. "
        "totalCount is unknown until complete and then exact only through max_depth (default64, max128). "
        "Limits: 256 scene entries, 250000 roots/scene, one root enumeration/page, 4096 managed traversal "
        "steps/page, 500 IDs and 16384 output bytes. Root preparation is accounted separately; "
        "native Unity calls are synchronous and not time-bounded. Restart after edits, expiry or reload. "
        "Use mcpforunity://scene/gameobject/{id} for details or gameobject_edit for edits."
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
        str | None,
        Field(
            description="Search value. Required in legacy mode. Omit filters to list an explicit scene scope."
        ),
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
        Literal["legacy", "active", "all_loaded", "exact"],
        Field(
            description="Explicit scene selection; legacy preserves existing method-dependent scope"
        ),
    ] = "legacy",
    scene_path: Annotated[
        str | None,
        Field(description="Exact loaded Assets/ or Packages/ .unity path for scene_scope=exact"),
    ] = None,
    conditions: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description="AND predicates with exactly search_method and search_term; max8 including top-level search"
        ),
    ] = None,
    max_depth: Annotated[
        int | None, Field(description="Scoped inclusive depth: root0, default64, maximum128")
    ] = None,
    projection: Annotated[
        list[Literal["name", "active", "transform", "components", "handle"]] | None,
        Field(
            max_length=5,
            description="Optional same-ID projection; handles use existing session-scoped lifetime",
        ),
    ] = None,
    refresh_if_dirty: RefreshIfDirty = True,
) -> dict[str, Any]:
    """Preserve legacy payloads; validate scoped options before refresh-capable preflight."""
    if type(refresh_if_dirty) is not bool:
        return {"success": False, "message": "refresh_if_dirty must be a boolean."}
    if projection is not None and (
        not isinstance(projection, list)
        or len(projection) > 5
        or any(not isinstance(field, str) for field in projection)
        or len(set(projection)) != len(projection)
        or any(
            field not in {"name", "active", "transform", "components", "handle"}
            for field in projection
        )
    ):
        return {
            "success": False,
            "message": "projection requires up to five unique supported fields.",
        }
    if scene_scope not in ("legacy", "active", "all_loaded", "exact"):
        return {
            "success": False,
            "message": "scene_scope must be legacy, active, all_loaded or exact.",
        }
    if scene_scope == "exact":
        try:
            validate_exact_scene_path(scene_path)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}
    elif scene_path is not None:
        return {"success": False, "message": "scene_path requires scene_scope exact."}
    if scene_scope == "legacy":
        if conditions is not None or max_depth is not None:
            return {
                "success": False,
                "message": "conditions and max_depth require a non-legacy scene_scope.",
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
    gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=refresh_if_dirty)
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
        if scene_path is not None:
            params["scene_path"] = scene_path
        if projection is not None:
            params["projection"] = projection
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
