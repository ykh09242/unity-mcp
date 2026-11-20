"""Read counts from an existing Shader Graph window in the selected Editor."""

from typing import Annotated

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from services.registry import mcp_for_unity_tool
from services.registry.action_catalog import OBSERVATION_META
from services.registry.open_shader_graph_observation import OPEN_GRAPH_OBSERVATION
from services.registry.operation_invocation import wrap_tool
from services.tools.manage_shader import manage_shader

_read_shader = wrap_tool(manage_shader, "manage_shader", "manage_shader")


@mcp_for_unity_tool(
    group="vfx",
    unity_target="manage_shader",
    description=(
        "Read nodeCount and edgeCount, including unsaved edits, from one already-open Shader "
        "Graph window matching a project-owned Assets/*.shadergraph path. Check status for "
        "open_model, no_matching_window, open_model_unavailable or window_lookup_unavailable. "
        "Requires manage_shader. Leaves the graph, its window and the asset file untouched."
    ),
    meta={OBSERVATION_META: OPEN_GRAPH_OBSERVATION},
    annotations=ToolAnnotations(
        title="Inspect Open Shader Graph",
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    ),
)
async def inspect_open_shader_graph(
    ctx: Context,
    path: Annotated[
        str, Field(min_length=1, description="Full project-owned Assets/*.shadergraph path.")
    ],
) -> dict:
    return await _read_shader(ctx, action="inspect_open_graph", path=path)
