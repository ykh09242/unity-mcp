"""MCP protocol capabilities used by session-specific tools."""

from fastmcp import Context
from mcp_types.version import MODERN_PROTOCOL_VERSIONS


def is_sessionless(ctx: Context) -> bool:
    request_context = ctx.request_context
    return request_context is not None and request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
