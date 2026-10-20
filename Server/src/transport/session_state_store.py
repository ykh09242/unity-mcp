"""Bounded session storage with physical expiration and owned maintenance."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import asyncio
import time

import anyio
from key_value.aio._utils.managed_entry import ManagedEntry
from key_value.aio.stores.base import BaseDestroyStore, BaseEnumerateKeysStore


class SessionStateCapacityError(RuntimeError):
    """Admission failed without removing another session's live state."""


@dataclass(frozen=True, slots=True)
class _StoredState:
    payload: str
    size: int
    expires_at: float | None


class SessionStateStore(BaseDestroyStore, BaseEnumerateKeysStore):
    """Own serialized state; expiration and accounting mutate only this store.

    FastMCP's state adapter uses the ``fastmcp_state`` collection. Admission
    bounds encoded keys/payloads, not a platform-specific estimate of Python RSS.
    Live entries are never silently evicted to admit a different session.
    """

    def __init__(
        self,
        *,
        max_entries: int = 4096,
        max_bytes: int = 16 * 1024 * 1024,
        max_entry_bytes: int = 64 * 1024,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if min(max_entries, max_bytes, max_entry_bytes) < 1:
            raise ValueError("Session storage limits must be positive")
        super().__init__(default_collection="fastmcp_state", stable_api=True)
        self._entries: dict[str, _StoredState] = {}
        self._retained_bytes = 0
        self._next_expiry = float("inf")
        self._clock = clock
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_entry_bytes = max_entry_bytes

    @property
    def retained_entries(self) -> int:
        return len(self._entries)

    @property
    def retained_bytes(self) -> int:
        return self._retained_bytes

    async def setup_collection(self, *, collection: str) -> None:
        # Reject before BaseStore allocates per-collection initialization locks.
        if collection != self.default_collection:
            raise ValueError("This store is reserved for FastMCP session state")
        await super().setup_collection(collection=collection)

    def _remove(self, key: str) -> bool:
        previous = self._entries.pop(key, None)
        if previous is None:
            return False
        self._retained_bytes -= previous.size
        if not self._entries:
            self._next_expiry = float("inf")
        return True

    def expire(self) -> int:
        """Reclaim elapsed entries using the same Unix clock as their deadlines."""
        now = self._clock()
        if now < self._next_expiry:
            return 0
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.expires_at is not None and entry.expires_at <= now
        ]
        for key in expired:
            self._remove(key)
        self._next_expiry = min(
            (entry.expires_at for entry in self._entries.values() if entry.expires_at is not None),
            default=float("inf"),
        )
        return len(expired)

    def clear(self) -> None:
        self._entries.clear()
        self._retained_bytes = 0
        self._next_expiry = float("inf")

    async def _get_managed_entry(self, *, collection: str, key: str) -> ManagedEntry | None:
        self.expire()
        entry = self._entries.get(key)
        return (
            self._serialization_adapter.load_json(json_str=entry.payload)
            if entry is not None
            else None
        )

    async def _put_managed_entry(
        self, *, collection: str, key: str, managed_entry: ManagedEntry
    ) -> None:
        self.expire()
        expires_at = (
            managed_entry.expires_at.timestamp() if managed_entry.expires_at is not None else None
        )
        if expires_at is not None and expires_at <= self._clock():
            self._remove(key)
            return
        payload = self._serialization_adapter.dump_json(entry=managed_entry)
        size = len(payload.encode("utf-8")) + len(key.encode("utf-8"))
        previous = self._entries.get(key)
        replaced_bytes = previous.size if previous is not None else 0
        if (
            size > self._max_entry_bytes
            or self._retained_bytes - replaced_bytes + size > self._max_bytes
            or (previous is None and len(self._entries) >= self._max_entries)
        ):
            raise SessionStateCapacityError(
                "Session state capacity exceeded; existing live session values were preserved"
            )
        self._entries[key] = _StoredState(payload, size, expires_at)
        self._retained_bytes += size - replaced_bytes
        if expires_at is not None:
            self._next_expiry = min(self._next_expiry, expires_at)

    async def _delete_managed_entry(self, *, collection: str, key: str) -> bool:
        return self._remove(key)

    async def _get_collection_keys(self, *, collection: str, limit: int | None = None) -> list[str]:
        self.expire()
        return list(self._entries)[:limit]

    async def _delete_store(self) -> bool:
        self.clear()
        return True

    async def _maintain(self) -> None:
        while True:
            await anyio.sleep(30)
            self.expire()

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Keep idle expiration owned by the server and clear it on shutdown."""
        # FastMCP shields teardown inside its AsyncExitStack. An AnyIO cancel scope
        # spanning this yield would then be exited beneath that newer scope.
        # The server is asyncio-based: own, cancel and join a child task instead.
        task = asyncio.create_task(self._maintain(), name="unity-session-state-maintenance")
        try:
            yield
        finally:
            task.cancel()
            try:
                with suppress(asyncio.CancelledError):
                    await task
            finally:
                self.clear()
