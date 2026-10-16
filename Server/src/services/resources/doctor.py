"""Read-only connection diagnostics for this actual MCP request path."""

from fastmcp import Context

from models.doctor import DoctorResourceResponse
from services.doctor import query_doctor
from services.registry import mcp_for_unity_resource


@mcp_for_unity_resource(
    uri="mcpforunity://doctor",
    name="connection_doctor",
    description="Read current bounded connection/native-read/source evidence for this MCP request. Does not attest another client application's connection or change configuration.",
)
async def get_doctor(ctx: Context) -> DoctorResourceResponse:
    """Reuse the capability capture, preserving its five-probe budget and ownership checks."""
    return DoctorResourceResponse(data=await query_doctor(ctx))
