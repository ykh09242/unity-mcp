"""MCP protocol capabilities and internal resource request context."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from fastmcp import Context, FastMCP
from fastmcp.server.middleware import MiddlewareContext
from mcp_types.version import MODERN_PROTOCOL_VERSIONS


_RESOURCE_LIST_METHODS = ("resources/list", "resources/templates/list")
_catalog_server: ContextVar[tuple[FastMCP, tuple[str, ...]] | None] = ContextVar(
    "unity_mcp_catalog_server", default=None
)
_resource_read: ContextVar[tuple[FastMCP, str, str | None] | None] = ContextVar(
    "unity_mcp_resource_read", default=None
)


@contextmanager
def internal_catalog(server: FastMCP, *methods: str) -> Iterator[None]:
    """Run listing policies inside the tool request that already owns routing and delivery."""
    token = _catalog_server.set((server, methods))
    try:
        yield
    finally:
        _catalog_server.reset(token)


def internal_resource_catalog(server: FastMCP):
    return internal_catalog(server, *_RESOURCE_LIST_METHODS)


def is_internal_catalog_list(context: MiddlewareContext) -> bool:
    scope = _catalog_server.get()
    if scope is None or getattr(context, "method", None) not in scope[1]:
        return False
    request_context = getattr(context, "fastmcp_context", None)
    return getattr(request_context, "fastmcp", None) is scope[0]


def is_internal_resource_list(context: MiddlewareContext) -> bool:
    return is_internal_catalog_list(context) and context.method in _RESOURCE_LIST_METHODS


@contextmanager
def internal_resource_read(server: FastMCP, uri: str, instance: str | None) -> Iterator[None]:
    """Run a fixed resource's middleware within the calling tool's response."""
    token = _resource_read.set((server, uri, instance))
    try:
        yield
    finally:
        _resource_read.reset(token)


def internal_resource_read_scope(
    ctx: Context | None, uri: str
) -> tuple[FastMCP, str, str | None] | None:
    """Return the enclosing tool read only for its originating server and URI."""
    scope = _resource_read.get()
    if scope is not None and ctx is not None and ctx.fastmcp is scope[0] and uri == scope[1]:
        return scope
    return None


def internal_resource_read_target(ctx: Context | None, uri: str) -> str | None:
    """A connection-wide inventory has no selected project."""
    scope = internal_resource_read_scope(ctx, uri)
    return scope[2] if scope is not None else None


def is_sessionless(ctx: Context) -> bool:
    request_context = ctx.request_context
    return (
        request_context is not None and request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
    )
