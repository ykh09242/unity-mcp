"""Charge shared snapshots/copies when the source transport has no reservations."""
from __future__ import annotations

import threading
from uuid import uuid4

from models.response_limits import MAX_RESPONSE_RETAINED_BYTES, ResponseOwner


class SharedReadBudget:
    """One process-wide bounded counter for transport-unowned shared allocations."""

    def __init__(self, max_bytes: int = MAX_RESPONSE_RETAINED_BYTES, max_entries: int = 1024):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.entries: dict[str, int] = {}
        self._bytes = 0
        self._count = 0
        self._lock = threading.Lock()

    @property
    def retained_bytes(self) -> int:
        """Expose aggregate accounting for bounded admission and owned regression tests."""
        with self._lock:
            return self._bytes

    def reserve(self, owner: ResponseOwner, charge: int) -> bool:
        """Admit a source or detached copy before storing/allocating its snapshot."""
        with self._lock:
            if (owner.released or self._count >= self.max_entries
                    or charge > self.max_bytes - self._bytes):
                return False
            key = uuid4().hex
            self.entries[key] = charge
            self._bytes += charge
            self._count += 1
        owner.entries.append((self.entries, key))
        owner.on_release.append(lambda: self._release(charge))
        owner.copy_reservations.append(lambda copy_owner: self._reserve_copy(key, copy_owner))
        return True

    def _reserve_copy(self, source_key: str, owner: ResponseOwner) -> bool:
        with self._lock:
            charge = self.entries.get(source_key)
        return charge is not None and self.reserve(owner, charge)

    def _release(self, charge: int) -> None:
        # ResponseOwner removes its entry before invoking this callback. Avoid
        # iterating a mapping concurrently with another loop's delivery cleanup.
        with self._lock:
            self._bytes -= charge
            self._count -= 1


shared_read_budget = SharedReadBudget()
