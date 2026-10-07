"""Real legacy MCP connections receive catalog changes and leave no stale listeners."""

from weakref import WeakSet

import anyio
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler
from mcp.server.connection import Connection
from mcp_types import ToolListChangedNotification

from transport.plugin_hub import PluginHub
from transport.session_tracking import SessionTrackingMiddleware


class ToolChangeHandler(MessageHandler):
    def __init__(self) -> None:
        self.received = anyio.Event()

    async def on_tool_list_changed(self, message: ToolListChangedNotification) -> None:
        self.received.set()


@pytest.mark.asyncio
async def test_catalog_notification_reaches_live_connection_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: WeakSet[Connection] = WeakSet()
    monkeypatch.setattr("transport.plugin_hub._active_mcp_sessions", connections)
    server = FastMCP("catalog-notification")
    server.add_middleware(SessionTrackingMiddleware(connections))
    handler = ToolChangeHandler()

    async with Client(server, mode="legacy", message_handler=handler) as client:
        await client.list_tools()
        assert len(connections) == 1
        await PluginHub._notify_mcp_tool_list_changed()
        with anyio.fail_after(2):
            await handler.received.wait()
    assert len(connections) == 0


@pytest.mark.asyncio
async def test_sessionless_connections_are_not_retained_for_legacy_notifications() -> None:
    connections: WeakSet[Connection] = WeakSet()
    server = FastMCP("sessionless-notification")
    server.add_middleware(SessionTrackingMiddleware(connections))
    async with Client(server) as client:
        await client.list_tools()
        assert len(connections) == 0
