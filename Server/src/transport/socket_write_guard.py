"""Carry one command's final admission to its outermost authenticated ASGI send."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, replace

from starlette.types import Message
from starlette.websockets import WebSocket

SOCKET_SEND_BOUNDARY_STATE = "unity_mcp_socket_send_boundary"


class SocketSendBoundary:
    """Opaque identity for the last application-owned credential-renewing sender."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class SocketWriteGuard:
    task: asyncio.Task
    websocket: WebSocket
    authorize: Callable[[], Awaitable[bool]]
    dispatch: Callable[[], None]
    frame: Message | None = None

    def bind_frame(self, websocket: WebSocket, frame: Message) -> "SocketWriteGuard | None":
        if (
            self.task is asyncio.current_task()
            and self.websocket is websocket
            and frame["type"] == "websocket.send"
        ):
            return replace(self, frame=frame)
        return None

    def matches(self, boundary: SocketSendBoundary, frame: Message) -> bool:
        return (
            self.task is asyncio.current_task()
            and self.frame is frame
            and self.websocket.scope.get("state", {}).get(SOCKET_SEND_BOUNDARY_STATE) is boundary
        )


socket_write_guard: ContextVar[SocketWriteGuard | None] = ContextVar(
    "unity_socket_write_guard", default=None
)
