"""MCP protocol capabilities and internal resource request context."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from fastmcp import Context, FastMCP
from fastmcp.server.middleware import MiddlewareContext
from mcp_types.version import MODERN_PROTOCOL_VERSIONS


_catalog_server: ContextVar[tuple[FastMCP, tuple[str, ...]] | None] = ContextVar(
    "unity_mcp_catalog_server", default=None
)
_resource_read: ContextVar[tuple[FastMCP, str, str] | None] = ContextVar(
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
    return internal_catalog(server, "resources/list", "resources/templates/list")


def is_internal_catalog_list(context: MiddlewareContext) -> bool:
    scope = _catalog_server.get()
    return (
        scope is not None
        and context.method in scope[1]
        and context.fastmcp_context is not None
        and context.fastmcp_context.fastmcp is scope[0]
    )


def is_internal_resource_list(context: MiddlewareContext) -> bool:
    return context.method in {
        "resources/list",
        "resources/templates/list",
    } and is_internal_catalog_list(context)


@contextmanager
def internal_resource_read(server: FastMCP, uri: str, instance: str) -> Iterator[None]:
    """Run the resource policy inside the tool's selected target and response."""
    token = _resource_read.set((server, uri, instance))
    try:
        yield
    finally:
        _resource_read.reset(token)


def internal_resource_read_target(ctx: Context | None, uri: str) -> str | None:
    """Resolve an internal read only for its originating server and resource URI."""
    scope = _resource_read.get()
    if scope is None or ctx is None:
        return None
    server, expected_uri, instance = scope
    return instance if ctx.fastmcp is server and uri == expected_uri else None


def is_sessionless(ctx: Context) -> bool:
    request_context = ctx.request_context
    return (
        request_context is not None and request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
    )
