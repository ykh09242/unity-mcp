"""Fixed read-only observations using the existing resource and native handlers."""

from typing import Annotated

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field, StrictInt

from services.registry import mcp_for_unity_tool
from services.registry.action_catalog import (
    GAME_VIEW_OBSERVATION,
    GRAPH_FILE_OBSERVATION,
    OBSERVATION_META,
    EDITOR_STATE_OBSERVATION,
    POINTER_ORDER_OBSERVATION,
)
from services.registry.operation_invocation import wrap_tool
from services.registry.tool_registry import FIXED_OBSERVATION_ACTIONS
from services.tools.manage_editor import manage_editor
from services.tools.manage_shader import manage_shader
from services.tools.manage_ugui import ScreenPosition, manage_ugui
from services.tools.pagination import MAX_POSITION

_EDITOR, _GAME_VIEW = FIXED_OBSERVATION_ACTIONS["get_game_view_size"]
_SHADER, _GRAPH = FIXED_OBSERVATION_ACTIONS["inspect_shader_graph"]
# A direct admitted callback preserves parent selection and grants on both surfaces
# without issuing a nested MCP request with the same response owner.
_editor = wrap_tool(manage_editor, _EDITOR, _EDITOR)
_shader = wrap_tool(manage_shader, _SHADER, _SHADER)
_ugui = wrap_tool(manage_ugui, "manage_ugui", "manage_ugui")


@mcp_for_unity_tool(
    group="core",
    unity_target=None,
    description="Read the selected Editor snapshot including active scene and current compiling flag. Check staleness and field presence. Equivalent to mcpforunity://editor/state.",
    meta={OBSERVATION_META: EDITOR_STATE_OBSERVATION},
    annotations=ToolAnnotations(
        title="Inspect Editor State", readOnlyHint=True, destructiveHint=False, openWorldHint=False
    ),
)
async def inspect_editor_state(ctx: Context) -> dict:
    from services.resources.editor_state import get_editor_state

    return (await get_editor_state(ctx)).model_dump()


@mcp_for_unity_tool(
    group="ui",
    unity_target="manage_ugui",
    description="Read ordered native uGUI hits for a scene RectTransform and current bottom-left screen pixel coordinates. Refuses unsupported queries before dispatch; never sends input, refreshes layout or opts into project callbacks. Requires manage_ugui.",
    meta={OBSERVATION_META: POINTER_ORDER_OBSERVATION},
    annotations=ToolAnnotations(
        title="Inspect uGUI Hit Order",
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    ),
)
async def inspect_ugui_hit_order(
    ctx: Context,
    target: Annotated[str | StrictInt, "Scene RectTransform name, path or instance ID."],
    screen_position: Annotated[
        ScreenPosition, "Current Game View pixel coordinates [x,y] from bottom left."
    ],
) -> dict:
    return await _ugui(
        ctx,
        action="diagnose_raycast",
        target=target,
        screen_position=screen_position,
        allow_custom_filters=False,
        strict_read=True,
    )


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
