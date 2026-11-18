"""Read fixed Editor observations through their existing admission and native handlers."""

from fastmcp import Context

from services.registry import mcp_for_unity_resource
from services.registry.operation_invocation import wrap_tool
from services.tools.manage_editor import manage_editor
from services.tools.manage_shader import manage_shader
from services.tools.pagination import MAX_POSITION, page_integer


# Use the admitted built-in callbacks directly. Nested MCP requests would acquire
# a second response owner for the same request ID and resolve the target again.
_editor = wrap_tool(manage_editor, "manage_editor", "manage_editor")
_shader = wrap_tool(manage_shader, "manage_shader", "manage_shader")


@mcp_for_unity_resource(
    uri="mcpforunity://editor/game-view-size",
    name="game_view_size",
    description=(
        "Read the selected preset, rendered dimensions and available presets of an open Game View. "
        "Requires manage_editor on the selected Editor."
    ),
)
async def get_game_view_size(ctx: Context) -> dict:
    return await _editor(ctx, action="get_game_view_size")


@mcp_for_unity_resource(
    uri="mcpforunity://shader/graph/{encoded_path}{?page_size,page_number}",
    name="shader_graph_structure",
    description=(
        "Read serialized nodes, edges, properties and keywords from a project-owned Shader Graph. "
        "URL-encode the complete Assets/*.shadergraph path as one segment. Page numbers start at "
        "1; page_size defaults to 50 and accepts 1-100. Requires manage_shader on the selected Editor."
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
    return await _shader(
        ctx, action="inspect_graph", path=encoded_path, page_size=size, page_number=page
    )
