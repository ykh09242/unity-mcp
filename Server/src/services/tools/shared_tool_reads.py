"""Bounded read sharing owned by active tool callers and their event loop."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Hashable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Generic, TypeVar
from weakref import WeakKeyDictionary, WeakValueDictionary, finalize

from models.response_limits import ResponseOwner, response_owner, response_size
from services.tools.shared_read_budget import shared_read_budget

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _CompletedRead(Generic[T]):
    value: T
    received_at: float


class SharedReadCapacityError(RuntimeError):
    """A detached response copy could not reserve its retained-byte budget."""

    def __init__(self) -> None:
        super().__init__("result_capacity")


def _release_snapshot(owner: ResponseOwner, copies: list[ResponseOwner]) -> None:
    owner.release()
    for copy_owner in copies:
        copy_owner.release()
    copies.clear()


class _ReadFlight(Generic[T]):
    """Own one snapshot until its cache reference and active fetches are gone."""

    def __init__(self, factory: Callable[[], Coroutine[None, None, T]]) -> None:
        self.owner = ResponseOwner()
        self.detached_copies: list[ResponseOwner] = []
        # A closed loop cannot run its expiry task. GC still releases aggregate
        # accounting without retaining that loop through the registry.
        self._cleanup = finalize(self, _release_snapshot, self.owner, self.detached_copies)
        self.references = 1
        self.task = asyncio.create_task(self._fetch(factory))

    async def _fetch(self, factory: Callable[[], Coroutine[None, None, T]]) -> _CompletedRead[T]:
        token = response_owner.set(self.owner)
        received = False
        try:
            value = await factory()
            if not self.owner.entries:
                # HTTP results already carry Hub reservations; stdio and small
                # transport errors need their own aggregate retained-copy budget.
                charge = response_size(value)
                if charge is None or not shared_read_budget.reserve(self.owner, charge):
                    raise SharedReadCapacityError()
            received = True
            return _CompletedRead(value, asyncio.get_running_loop().time())
        finally:
            response_owner.reset(token)
            if not received:
                self.owner.release()

    def release(self) -> None:
        self.references -= 1
        if self.references == 0:
            self._cleanup()


class SharedRead(Generic[T]):
    """Own one fetch and count the callers that may still need it."""

    def __init__(self, freshness_s: float) -> None:
        self.callers = 0
        self._freshness_s = freshness_s
        self._flight: _ReadFlight[T] | None = None
        self.expiry_task: asyncio.Task[None] | None = None

    async def fetch(self, factory: Callable[[], Coroutine[None, None, T]]) -> T:
        """Share a fresh read, returning private data for caller-side observations."""
        flight = self._flight
        if flight is not None and flight.task.done() and not flight.task.cancelled():
            if (flight.task.exception() is not None
                    or asyncio.get_running_loop().time() - flight.task.result().received_at >= self._freshness_s):
                flight.release()
                flight = None
        if flight is None or flight.task.cancelled():
            if flight is not None:
                flight.release()
            flight = _ReadFlight(factory)
            self._flight = flight
        flight.references += 1
        try:
            completed = await asyncio.shield(flight.task)
            consumer = response_owner.get()
            copy_owner = flight.owner.reserve_copy()
            if copy_owner is None:
                raise SharedReadCapacityError()
            transferred = False
            try:
                value = deepcopy(completed.value)
                if consumer is None:
                    # Direct internal callers have no MCP delivery owner; their
                    # active shared-read lifetime owns the detached allocation.
                    flight.detached_copies.append(copy_owner)
                    transferred = True
                else:
                    transferred = consumer.adopt(copy_owner)
                    if not transferred:
                        raise SharedReadCapacityError()
                return value
            finally:
                if not transferred:
                    copy_owner.release()
        finally:
            flight.release()

    async def close(self) -> None:
        """Cancel and drain an orphaned fetch when its final caller leaves."""
        if self._flight is not None:
            flight = self._flight
            self._flight = None
            if not flight.task.done():
                flight.task.cancel()
            try:
                await asyncio.gather(flight.task, return_exceptions=True)
            finally:
                flight.release()


class SharedToolReads(Generic[T]):
    """Share reads only within active sessions; never retain finished jobs."""

    def __init__(self, *, freshness_s: float = 0.0, max_entries: int = 128, retention_s: float = 0.0) -> None:
        self._freshness_s = freshness_s
        self._max_entries = max_entries
        self._retention_s = retention_s
        # Weak values avoid retaining a closed loop through a cached task.
        # Active callers and an owned expiry task hold each live read strongly.
        self._loops: WeakKeyDictionary[asyncio.AbstractEventLoop, WeakValueDictionary[Hashable, SharedRead[T]]] = WeakKeyDictionary()

    async def _expire(self, entries: WeakValueDictionary, key: Hashable, read: SharedRead[T]) -> None:
        try:
            await asyncio.sleep(self._retention_s)
        except asyncio.CancelledError:
            # Shutdown/invalidation must release a parked snapshot. A new lease
            # increments callers before canceling expiry and retains that source.
            if read.callers:
                return
        finally:
            if read.expiry_task is asyncio.current_task():
                read.expiry_task = None
        if read.callers == 0:
            if entries.get(key) is read:
                del entries[key]
            await read.close()

    async def invalidate(self, key: Hashable | None) -> None:
        """Detach a cached identity; active readers finish without republishing it."""
        entries = self._loops.get(asyncio.get_running_loop())
        read = entries.pop(key, None) if entries is not None else None
        if read is not None and read.callers == 0:
            if read.expiry_task is not None:
                read.expiry_task.cancel()
                await asyncio.gather(read.expiry_task, return_exceptions=True)
            await read.close()

    @asynccontextmanager
    async def session(self, key: Hashable | None) -> AsyncIterator[SharedRead[T]]:
        """Lease loop-local state, or use an untracked read at the capacity limit."""
        loop = asyncio.get_running_loop()
        entries = self._loops.setdefault(loop, WeakValueDictionary())
        read = entries.get(key) if key is not None else None
        if read is None:
            read = SharedRead[T](self._freshness_s)
            if key is not None and len(entries) < self._max_entries:
                entries[key] = read
        read.callers += 1
        try:
            if read.expiry_task is not None:
                read.expiry_task.cancel()
                await asyncio.gather(read.expiry_task, return_exceptions=True)
            yield read
        finally:
            read.callers -= 1
            if read.callers == 0:
                if key is not None and entries.get(key) is read and self._retention_s > 0:
                    read.expiry_task = asyncio.create_task(self._expire(entries, key, read))
                else:
                    if key is not None and entries.get(key) is read:
                        del entries[key]
                    await read.close()
