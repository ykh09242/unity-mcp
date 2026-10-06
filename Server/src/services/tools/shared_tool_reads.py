"""Bounded read sharing owned by active tool callers and their event loop."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Hashable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Generic, TypeVar
from weakref import WeakKeyDictionary

from models.response_limits import ResponseOwner, response_owner

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _CompletedRead(Generic[T]):
    value: T
    received_at: float


class SharedReadCapacityError(RuntimeError):
    """A detached response copy could not reserve its retained-byte budget."""

    def __init__(self) -> None:
        super().__init__("result_capacity")


class _ReadFlight(Generic[T]):
    """Own one snapshot until its cache reference and active fetches are gone."""

    def __init__(self, factory: Callable[[], Coroutine[None, None, T]]) -> None:
        self.owner = ResponseOwner()
        self.detached_copies: list[ResponseOwner] = []
        self.references = 1
        self.task = asyncio.create_task(self._fetch(factory))

    async def _fetch(self, factory: Callable[[], Coroutine[None, None, T]]) -> _CompletedRead[T]:
        token = response_owner.set(self.owner)
        received = False
        try:
            value = await factory()
            received = True
            return _CompletedRead(value, asyncio.get_running_loop().time())
        finally:
            response_owner.reset(token)
            if not received:
                self.owner.release()

    def release(self) -> None:
        self.references -= 1
        if self.references == 0:
            self.owner.release()
            for copy_owner in self.detached_copies:
                copy_owner.release()
            self.detached_copies.clear()


class SharedRead(Generic[T]):
    """Own one fetch and count the callers that may still need it."""

    def __init__(self, freshness_s: float) -> None:
        self.callers = 0
        self._freshness_s = freshness_s
        self._flight: _ReadFlight[T] | None = None

    async def fetch(self, factory: Callable[[], Coroutine[None, None, T]]) -> T:
        """Share a fresh read, returning private data for caller-side observations."""
        flight = self._flight
        if flight is not None and flight.task.done() and not flight.task.cancelled():
            if asyncio.get_running_loop().time() - flight.task.result().received_at >= self._freshness_s:
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

    def __init__(self, *, freshness_s: float = 0.0, max_entries: int = 128) -> None:
        self._freshness_s = freshness_s
        self._max_entries = max_entries
        self._loops: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[Hashable, SharedRead[T]]] = WeakKeyDictionary()

    @asynccontextmanager
    async def session(self, key: Hashable | None) -> AsyncIterator[SharedRead[T]]:
        """Lease loop-local state, or use an untracked read at the capacity limit."""
        loop = asyncio.get_running_loop()
        entries = self._loops.setdefault(loop, {})
        read = entries.get(key) if key is not None else None
        if read is None:
            read = SharedRead[T](self._freshness_s)
            if key is not None and len(entries) < self._max_entries:
                entries[key] = read
        read.callers += 1
        try:
            yield read
        finally:
            read.callers -= 1
            if read.callers == 0:
                if key is not None and entries.get(key) is read:
                    del entries[key]
                await read.close()
