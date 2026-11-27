"""Read fixed Editor observations through their existing admission and native handlers."""

from fastmcp import Context

from services.registry import mcp_for_unity_resource
from services.registry.action_catalog import (
    GAME_VIEW_OBSERVATION,
    GRAPH_FILE_OBSERVATION,
    OBSERVATION_META,
)
from services.tools.editor_observations import (
    get_game_view_size as read_game_view_size,
    inspect_shader_graph,
)
from services.tools.pagination import MAX_POSITION, page_integer


@mcp_for_unity_resource(
    uri="mcpforunity://editor/game-view-size",
    name="game_view_size",
    catalog_observation=True,
    meta={OBSERVATION_META: GAME_VIEW_OBSERVATION},
    description=(
        "Read render_size in pixels, the selected preset and available presets from an open "
        "Game View. Equivalent read-only tool: get_game_view_size (manage_editor.get_game_view_size)."
    ),
)
async def get_game_view_size(ctx: Context) -> dict:
    return await read_game_view_size(ctx)


@mcp_for_unity_resource(
    uri="mcpforunity://shader/graph/{encoded_path}{?page_size,page_number}",
    name="shader_graph_structure",
    catalog_observation=True,
    meta={OBSERVATION_META: GRAPH_FILE_OBSERVATION},
    description=(
        "Read node and edge counts and paged structure from the saved Shader Graph file through "
        "the selected Editor. Unsaved graph-window edits and property values are unavailable. "
        "URL-encode the full Assets/*.shadergraph path as one segment. Pages start at 1 and "
        "page_size accepts 1-100 (default 50). Equivalent read-only tool: inspect_shader_graph "
        "(manage_shader.inspect_graph)."
    ),
)
async def get_shader_graph_structure(
    ctx: Context, encoded_path: str, page_size: str | None = None, page_number: str | None = None
) -> dict:
    try:
        size = page_integer(page_size, 50, 1, 100, "page_size")
        page = page_integer(page_number, 1, 1, MAX_POSITION, "page_number")
    except ValueError as error:
        return {"success": False, "error": str(error)}
    return await inspect_shader_graph(ctx, path=encoded_path, page_size=size, page_number=page)
