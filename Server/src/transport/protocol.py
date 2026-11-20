"""MCP protocol capabilities and internal catalog request context."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from fastmcp import Context, FastMCP
from fastmcp.server.middleware import MiddlewareContext
from mcp_types.version import MODERN_PROTOCOL_VERSIONS


_catalog_server: ContextVar[FastMCP | None] = ContextVar("unity_mcp_catalog_server", default=None)


@contextmanager
def internal_resource_catalog(server: FastMCP) -> Iterator[None]:
    """Run listing policies inside the tool request that already owns routing and delivery."""
    token = _catalog_server.set(server)
    try:
        yield
    finally:
        _catalog_server.reset(token)


def is_internal_resource_list(context: MiddlewareContext) -> bool:
    server = _catalog_server.get()
    return (
        server is not None
        and context.method in {"resources/list", "resources/templates/list"}
        and context.fastmcp_context is not None
        and context.fastmcp_context.fastmcp is server
    )


def is_sessionless(ctx: Context) -> bool:
    request_context = ctx.request_context
    return (
        request_context is not None and request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
    )
