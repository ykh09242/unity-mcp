"""Final socket-write admission and result observation for one admitted command.

The hub retains pending entries, worker pins, cancellation and cleanup. This
helper borrows their exact owners and never reserves or releases capacity.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from starlette.websockets import WebSocket, WebSocketState

from core import phase_diagnostics
from services.credential_lease import CredentialLease, CredentialLeaseError
from services.execution_receipt_context import execution_owner
from services.operation_permissions import authorize_operation
from transport.command_budget import _CommandBudget
from transport.command_resume import CommandResumeInvocation
from transport.local_project_context import LocalProjectContext
from transport.plugin_registry import PluginRegistry, PluginSession
from transport.socket_write_guard import SocketWriteGuard, socket_write_guard


class CommandNotDispatchedError(RuntimeError):
    """A command was refused before a socket write began."""


class CatalogDispatchGuard(Protocol):
    def check_http(self, session_id: str, user_id: str | None) -> bool: ...


@dataclass(frozen=True, slots=True)
class CommandDispatch:
    """One writer's admitted handles plus narrowly bound live ownership checks."""

    websocket: WebSocket
    registry: PluginRegistry | None
    session: PluginSession | None
    session_id: str
    generation: str | None
    user_id: str | None
    command_id: str
    payload: dict[str, Any]
    encoded: str
    future: asyncio.Future
    budget: _CommandBudget
    response_deadline: float
    requester: CredentialLease | None
    plugin: CredentialLease | None
    context: LocalProjectContext | None
    catalog_guard: CatalogDispatchGuard | None
    resume: CommandResumeInvocation | None
    authorized_lock: Callable[[], AbstractAsyncContextManager]
    target_is_current: Callable[[], bool]
    read_is_current: Callable[[], bool] | None
    read_invalidated: Callable[[], dict[str, Any]]
    socket_lease: Callable[[WebSocket], CredentialLease | None]

    async def send(self) -> None:
        for lease in (self.requester, self.plugin):
            if lease is not None:
                await lease.check()
        for lease in (self.requester, self.plugin):
            if lease is not None:
                lease.check_now()
                # Recheck the serialized canonical operation immediately
                # before socket I/O, after all yielding authorization checks.
                authorize_operation(
                    lease.permissions,
                    self.payload["name"],
                    self.payload["params"],
                    custom=self.catalog_guard is not None,
                    native=True,
                )
        receipt = execution_owner.get()
        if receipt is not None:
            await receipt.native_async(
                self.payload["name"],
                self.payload["params"],
                custom=self.catalog_guard is not None,
                kind="http",
                attempt_id=self.command_id,
                session_id=self.session_id,
                generation=self.generation,
                project_hash=self.session.project_hash if self.session is not None else None,
                resume=self.resume,
            )
        writer = SocketWriteGuard(
            asyncio.current_task(), self.websocket, self.authorize_write, self.dispatch_write
        )
        writer_token = socket_write_guard.set(writer)
        try:
            if getattr(self.websocket.state, "plugin_guarded_send", False):
                # Carry final admission through both Hub and optional
                # remote-auth send wrappers, immediately before I/O.
                await self.websocket.send_text(self.encoded)
            elif await writer.authorize():
                writer.dispatch()
                await self.websocket.send_text(self.encoded)
        finally:
            socket_write_guard.reset(writer_token)
        for lease in (self.requester, self.plugin):
            if lease is not None:
                await lease.check()

    async def authorize_write(self) -> bool:
        # Scheduling, credential renewal and optional journal I/O
        # can all yield after admission. Every writer must recheck
        # its exact target/catalog after those yields, before I/O.
        async with self.authorized_lock():
            current_session = (
                await self.registry.get_session(self.session_id)
                if self.registry is not None
                else None
            )
            if self.future.done():
                return False
            if (
                not self.target_is_current()
                or self.websocket.client_state != WebSocketState.CONNECTED
                or getattr(self.websocket.state, "plugin_generation", None) != self.generation
                or self.session is None
                or current_session is None
                or current_session.project_hash != self.session.project_hash
                or current_session.user_id != self.user_id
            ):
                raise CommandNotDispatchedError("Plugin target generation changed")
            if self.context is not None and (
                self.context.selection is None
                or not self.context.selection.matches(
                    self.registry, self.session_id, self.websocket, current_session
                )
            ):
                raise CommandNotDispatchedError("Plugin project context changed")
            if self.read_is_current is not None and not self.read_is_current():
                self.future.set_result(self.read_invalidated())
                return False
            if self.catalog_guard is not None and not self.catalog_guard.check_http(
                self.session_id, self.user_id
            ):
                from services.custom_tool_service import custom_catalog_changed

                self.future.set_result(custom_catalog_changed().model_dump())
                return False
            if self.socket_lease(self.websocket) is not self.plugin:
                raise CredentialLeaseError()
        # Classification can inspect a batch. The exact socket and immutable
        # grants are fenced above; no await separates this check from return.
        for lease in (self.requester, self.plugin):
            if lease is not None:
                lease.check_now()
                authorize_operation(
                    lease.permissions,
                    self.payload["name"],
                    self.payload["params"],
                    custom=self.catalog_guard is not None,
                    native=True,
                )
        return True

    def dispatch_write(self) -> None:
        # Serialization and synchronous admission can outlast the
        # event loop's pending timeout callback. Fence the actual
        # write before publishing any dispatch evidence.
        if self.budget.remaining() <= 0:
            raise TimeoutError
        if self.resume is not None:
            self.resume.dispatch()
        receipt = execution_owner.get()
        if receipt is not None:
            receipt.dispatch_started(
                kind="http",
                attempt_id=self.command_id,
                session_id=self.session_id,
                generation=self.generation,
            )
        self.budget.dispatched = True

    async def observe(
        self, send_task: asyncio.Task, *, wait_for: Callable[..., Awaitable[Any]]
    ) -> dict[str, Any]:
        done, _ = await asyncio.wait(
            {send_task, self.future},
            timeout=self.budget.remaining(),
            return_when=asyncio.FIRST_COMPLETED,
        )
        # A disconnect must also interrupt a blocked socket write.
        if self.future in done:
            for lease in (self.requester, self.socket_lease(self.websocket)):
                if lease is not None:
                    await lease.check()
            return self.future.result()
        if send_task not in done:
            raise asyncio.TimeoutError
        send_task.result()
        phase_diagnostics.step(self.payload["name"], "response")
        self.budget.phase = "response"
        remaining = max(0.0, self.response_deadline - asyncio.get_running_loop().time())
        result = await wait_for(self.future, timeout=remaining)
        for lease in (self.requester, self.socket_lease(self.websocket)):
            if lease is not None:
                await lease.check()
        return result
