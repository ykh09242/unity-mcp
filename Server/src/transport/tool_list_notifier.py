"""Coalesce legacy tool refreshes without blocking the Unity socket reader."""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from weakref import ReferenceType, WeakKeyDictionary, WeakSet, ref

import anyio
from mcp.server.connection import Connection

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Delivery:
    """Mutable connection mailbox: one running send and one pending refresh."""

    connection: ReferenceType[Connection]
    task: asyncio.Task[None] | None = None
    dirty: bool = False
    closed: bool = False


class ToolListNotifier:
    """Own bounded delivery tasks until connection teardown or server shutdown.

    A tool-list notification invalidates a catalog, so repeated updates need only
    one pending refresh. A second send after an in-flight send covers changes
    made while the client was consuming the first notification.
    """

    def __init__(self) -> None:
        self._deliveries: WeakKeyDictionary[Connection, _Delivery] = WeakKeyDictionary()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed_connections: WeakSet[Connection] = WeakSet()
        self._closed = False

    def reset(self) -> None:
        """Start a new hub lifespan and cancel work from an earlier configuration."""
        self._stop()
        self._closed = False

    def publish(self, connections: Iterable[Connection]) -> None:
        """Queue catalog invalidation after its new contents have been published."""
        if self._closed:
            return
        for connection in connections:
            if connection in self._closed_connections:
                continue
            delivery = self._deliveries.get(connection)
            if delivery is None:
                delivery = _Delivery(ref(connection))
                self._deliveries[connection] = delivery
                connection.exit_stack.push_async_callback(self._close_delivery, delivery)
            delivery.dirty = True
            if delivery.task is None:
                task = asyncio.create_task(self._deliver(delivery), name="mcp-tool-list-changed")
                delivery.task = task
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _deliver(self, delivery: _Delivery) -> None:
        try:
            while delivery.dirty and not delivery.closed:
                delivery.dirty = False
                connection = delivery.connection()
                if connection is None:
                    return
                await connection.send_tool_list_changed()
        except Exception:
            # Background delivery is a transport boundary: observe failures
            # without leaking an unhandled task or failing the Unity reader.
            logger.debug("Failed to notify MCP connection of tool list change", exc_info=True)
        finally:
            delivery.task = None

    async def _close_delivery(self, delivery: _Delivery) -> None:
        """Cancel and drain this connection's send before SDK teardown finishes."""
        delivery.closed = True
        delivery.dirty = False
        connection = delivery.connection()
        if connection is not None:
            self._closed_connections.add(connection)
            if self._deliveries.get(connection) is delivery:
                del self._deliveries[connection]
        task = delivery.task
        if task is not None:
            task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(task, return_exceptions=True)

    def _stop(self) -> None:
        self._closed = True
        for delivery in self._deliveries.values():
            delivery.closed = True
            delivery.dirty = False
        self._deliveries.clear()
        for task in self._tasks:
            task.cancel()

    async def close(self) -> None:
        """Reject new work, then cancel and drain every outstanding SDK send."""
        self._stop()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*self._tasks, return_exceptions=True)
