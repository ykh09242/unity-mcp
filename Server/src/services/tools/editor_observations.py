"""Read-only projections of two fixed actions, admitted through their existing parents."""

from typing import Annotated

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from services.registry import mcp_for_unity_tool
from services.registry.action_catalog import (
    GAME_VIEW_OBSERVATION,
    GRAPH_FILE_OBSERVATION,
    OBSERVATION_META,
)
from services.registry.operation_invocation import wrap_tool
from services.registry.tool_registry import FIXED_OBSERVATION_ACTIONS
from services.tools.manage_editor import manage_editor
from services.tools.manage_shader import manage_shader
from services.tools.pagination import MAX_POSITION

_EDITOR, _GAME_VIEW = FIXED_OBSERVATION_ACTIONS["get_game_view_size"]
_SHADER, _GRAPH = FIXED_OBSERVATION_ACTIONS["inspect_shader_graph"]
# A direct admitted callback preserves parent selection and grants on both surfaces
# without issuing a nested MCP request with the same response owner.
_editor = wrap_tool(manage_editor, _EDITOR, _EDITOR)
_shader = wrap_tool(manage_shader, _SHADER, _SHADER)


@mcp_for_unity_tool(
    group="core",
    unity_target=_EDITOR,
    description=(
        "Read the current Game View render size in pixels, selected preset and available presets "
        "from an existing window in the selected Unity Editor. Use render_size.width and "
        "render_size.height for screen coordinates. Requires manage_editor."
    ),
    meta={OBSERVATION_META: GAME_VIEW_OBSERVATION},
    annotations=ToolAnnotations(
        title="Read Game View Size", readOnlyHint=True, destructiveHint=False, openWorldHint=False
    ),
)
async def get_game_view_size(ctx: Context) -> dict:
    return await _editor(ctx, action=_GAME_VIEW)


@mcp_for_unity_tool(
    group="vfx",
    unity_target=_SHADER,
    description=(
        "Read a saved Shader Graph file through the selected Unity Editor. Returns total nodeCount "
        "and edgeCount plus paged nodes, edges, properties and keywords. Unsaved graph-window edits "
        "and property values are unavailable. Supports project-owned MultiJSON GraphData v0-3 "
        "files up to 16 MiB. Requires manage_shader."
    ),
    meta={OBSERVATION_META: GRAPH_FILE_OBSERVATION},
    annotations=ToolAnnotations(
        title="Inspect Saved Shader Graph",
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    ),
)
async def inspect_shader_graph(
    ctx: Context,
    path: Annotated[
        str, Field(min_length=1, description="Full project-owned Assets/*.shadergraph path.")
    ],
    page_size: Annotated[
        int, Field(ge=1, le=100, description="Entries per collection on each page.")
    ] = 50,
    page_number: Annotated[
        int, Field(ge=1, le=MAX_POSITION, description="One-based page number.")
    ] = 1,
) -> dict:
    return await _shader(
        ctx, action=_GRAPH, path=path, page_size=page_size, page_number=page_number
    )
