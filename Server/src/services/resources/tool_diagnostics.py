"""Cached diagnostics are observations, never an alternate callable schema registry."""

from fastmcp import Context

from models.tool_diagnostics import ToolDiagnosticsResponse
from services.registry import mcp_for_unity_resource
from services.tool_diagnostics import query_tool_diagnostics


@mcp_for_unity_resource(
    uri="mcpforunity://tool-diagnostics",
    name="tool_diagnostics",
    description="Bounded current discovery and registration diagnostics; unknown when unobserved.",
)
async def get_tool_diagnostics(ctx: Context) -> ToolDiagnosticsResponse:
    return await query_tool_diagnostics(ctx)


@mcp_for_unity_resource(
    uri="mcpforunity://tool-diagnostics/{tool_name}",
    name="named_tool_diagnostics",
    description="Read bounded diagnostics for one tool; schemas remain authoritative in tools/list.",
)
async def get_named_tool_diagnostics(ctx: Context, tool_name: str) -> ToolDiagnosticsResponse:
    return await query_tool_diagnostics(ctx, tool_name)
