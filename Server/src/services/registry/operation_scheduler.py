"""Bounded loop-local reader/writer admission for one coordinating server.

The native Editor remains the cross-process execution authority. These leases
cover a public invocation and its actual workers, not the lifetime of native jobs.
"""

from __future__ import annotations

import asyncio  # noqa: ANYIO_OK -- preserve the server's existing event-loop contract.
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import threading
from typing import Literal, TypedDict

OperationMode = Literal["control", "shared_read", "exclusive"]
_LOGGER = logging.getLogger("mcp-for-unity-server")


class OperationScopeChanged(RuntimeError):
    def __init__(self) -> None:
        super().__init__("operation_target_changed")


class OperationCapacityError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("operation_capacity")


@dataclass(frozen=True, slots=True)
class OperationScope:
    principal: str
    instance: str
    generation: str

    @property
    def key(self) -> tuple[str, str]:
        # Generations fence a queued selection; they must not split the lock.
        return self.principal, self.instance


@dataclass(frozen=True, slots=True)
class OperationAdmission:
    scope: OperationScope
    mode: OperationMode
    is_current: Callable[[], bool] = field(repr=False, compare=False)
    expires_at: float | None = None
    retained_bytes: int = 0
    observational_continuation: bool = False


class SchedulerSnapshot(TypedDict):
    instances: int
    active: int
    waiting: int
    retained_bytes: int


class _WorkerPin:
    def __init__(self, owner: OperationLease) -> None:
        self._owner: OperationLease | None = owner
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            owner, self._owner = self._owner, None
        if owner is not None:
            owner._loop.call_soon_threadsafe(owner._unpin)


class OperationLease:
    """Mutable invocation ownership retained until its real workers settle."""

    def __init__(self, scheduler: OperationScheduler, request: OperationAdmission) -> None:
        self._scheduler = scheduler
        self._loop = asyncio.get_running_loop()
        self.request = request
        self._references = 1
        self._reference_lock = threading.Lock()
        self.closed = False

    def acquire_worker(self) -> _WorkerPin:
        with self._reference_lock:
            if self.closed:
                raise OperationScopeChanged()
            self._references += 1
        return _WorkerPin(self)

    def _pin(self) -> _WorkerPin:
        with self._reference_lock:
            self._references += 1
        return _WorkerPin(self)

    def close(self) -> None:
        with self._reference_lock:
            was_closed = self.closed
            self.closed = True
        if not was_closed:
            self._unpin()

    def _unpin(self) -> None:
        with self._reference_lock:
            self._references -= 1
            settled = self._references == 0
        if settled:
            self._scheduler._release(self)


@dataclass(slots=True)
class _Ticket:
    """Mutable future and request while a caller waits for admission."""

    request: OperationAdmission
    future: asyncio.Future[OperationLease]


@dataclass(slots=True)
class _Instance:
    """Active ownership and FIFO waiters for one principal/instance scope."""

    active: set[OperationLease] = field(default_factory=set)
    waiting: deque[_Ticket] = field(default_factory=deque)


class OperationScheduler:
    """Writer-fair shared reads, independent instances and audited controls."""

    def __init__(
        self,
        *,
        max_participants: int = 1024,
        max_instances: int = 256,
        max_retained_bytes: int = 1024 * 1024 * 1024,
        max_per_instance: int = 256,
    ) -> None:
        self._states: dict[tuple[str, str], _Instance] = {}
        self._participants = 0
        self._retained_bytes = 0
        self._max_participants = max_participants
        self._max_instances = max_instances
        self._max_retained_bytes = max_retained_bytes
        self._max_per_instance = max_per_instance

    def snapshot(self) -> SchedulerSnapshot:
        return {
            "instances": len(self._states),
            "active": sum(len(state.active) for state in self._states.values()),
            "waiting": sum(len(state.waiting) for state in self._states.values()),
            "retained_bytes": self._retained_bytes,
        }

    def _check_capacity(self, request: OperationAdmission) -> None:
        state = self._states.get(request.scope.key)
        if (
            request.mode not in {"control", "shared_read", "exclusive"}
            or state is not None
            and len(state.active) + len(state.waiting) >= self._max_per_instance
            or request.retained_bytes < 0
            or self._participants >= self._max_participants
            or self._retained_bytes + request.retained_bytes > self._max_retained_bytes
            or request.scope.key not in self._states
            and len(self._states) >= self._max_instances
        ):
            raise OperationCapacityError()

    def _grant(self, state: _Instance, ticket: _Ticket) -> None:
        request = ticket.request
        if (
            request.expires_at is not None
            and request.expires_at <= asyncio.get_running_loop().time()
        ):
            ticket.future.set_exception(TimeoutError("Operation deadline expired before admission"))
            self._forget(request)
            return
        try:
            current = request.is_current()
        except Exception:
            current = False
        if not current:
            ticket.future.set_exception(OperationScopeChanged())
            self._forget(request)
            return
        owner = OperationLease(self, request)
        state.active.add(owner)
        ticket.future.set_result(owner)
        _LOGGER.debug(
            "Unity operation admitted mode=%s active=%d waiting=%d",
            request.mode,
            len(state.active),
            len(state.waiting),
        )

    def _wake(self, key: tuple[str, str], state: _Instance) -> None:
        while state.waiting:
            exclusive = any(owner.request.mode == "exclusive" for owner in state.active)
            readers = any(owner.request.mode == "shared_read" for owner in state.active)
            ticket = state.waiting[0]
            if exclusive or readers and ticket.request.mode == "exclusive":
                break
            state.waiting.popleft()
            self._grant(state, ticket)
            if ticket.request.mode == "exclusive" and not ticket.future.exception():
                break
        self._prune(key, state)

    def _forget(self, request: OperationAdmission) -> None:
        self._participants -= 1
        self._retained_bytes -= request.retained_bytes

    def _prune(self, key: tuple[str, str], state: _Instance) -> None:
        if not state.active and not state.waiting and self._states.get(key) is state:
            del self._states[key]

    def _release(self, owner: OperationLease) -> None:
        key = owner.request.scope.key
        state = self._states[key]
        state.active.remove(owner)
        self._forget(owner.request)
        self._wake(key, state)

    async def _acquire(self, request: OperationAdmission) -> OperationLease:
        loop = asyncio.get_running_loop()
        if request.expires_at is not None and request.expires_at <= loop.time():
            raise TimeoutError("Operation deadline expired before admission")
        if not request.is_current():
            raise OperationScopeChanged()
        self._check_capacity(request)
        key = request.scope.key
        state = self._states.setdefault(key, _Instance())
        ticket = _Ticket(request, loop.create_future())
        self._participants += 1
        self._retained_bytes += request.retained_bytes
        if request.mode == "control":
            self._grant(state, ticket)
            self._prune(key, state)
        else:
            state.waiting.append(ticket)
            self._wake(key, state)
            if not ticket.future.done():
                _LOGGER.debug(
                    "Unity operation queued mode=%s reason=predecessor active=%d waiting=%d",
                    request.mode,
                    len(state.active),
                    len(state.waiting),
                )
        try:
            async with asyncio.timeout_at(request.expires_at):
                return await asyncio.shield(ticket.future)
        except BaseException:
            if ticket.future.done():
                if not ticket.future.cancelled() and ticket.future.exception() is None:
                    ticket.future.result().close()
            else:
                state.waiting.remove(ticket)
                ticket.future.cancel()
                self._forget(request)
                self._wake(key, state)
            raise

    @asynccontextmanager
    async def session(self, request: OperationAdmission) -> AsyncIterator[OperationLease]:
        owner = await self._acquire(request)
        try:
            yield owner
        finally:
            owner.close()
