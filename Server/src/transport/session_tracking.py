"""Observe handshake-era MCP connections for Unity catalog notifications."""

from typing import TypeVar
from weakref import WeakSet

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.server.connection import Connection
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

MessageT = TypeVar("MessageT")
ResultT = TypeVar("ResultT")


class SessionTrackingMiddleware(Middleware):
    """Keep live connections weakly referenced until their SDK teardown."""

    def __init__(self, connections: WeakSet[Connection]) -> None:
        self._connections = connections

    async def on_message(
        self,
        context: MiddlewareContext[MessageT],
        call_next: CallNext[MessageT, ResultT],
    ) -> ResultT:
        ctx = context.fastmcp_context
        if ctx is not None and ctx.request_context is not None:
            session = ctx.request_context.session
            if session.protocol_version in HANDSHAKE_PROTOCOL_VERSIONS:
                # SDK v2 exposes only a per-request session through FastMCP.
                # Its underlying Connection owns the public notification API
                # and exit_stack; keep this single SDK seam isolated here.
                connection = session._connection
                if connection not in self._connections:
                    self._connections.add(connection)
                    connection.exit_stack.callback(self._connections.discard, connection)
        return await call_next(context)
