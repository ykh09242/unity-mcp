"""Pin shared storage while discounting only proven immutable copy costs."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping

from models.response_limits import ResponseOwner

# Avoid adding a traversal and ownership group to small response deliveries.
_MIN_SHARED_CHARGE = 256 * 1024


def detached_copy_charge(value: object, source_charge: int) -> int | None:
    """Keep encoding workspace, but do not charge shared immutable objects twice.

    response_size has already validated this graph and charged 128 bytes per
    visited node plus twice its object size, string encoding workspace and wire
    bytes. Exact builtins share immutable scalars under both supported copiers.
    The original full charge stays pinned for those objects and traversal work.
    Each detached copy still owns conservative container growth, all per-string
    encoding workspace, wire bytes and its own reservation overhead.
    """
    if source_charge < _MIN_SHARED_CHARGE:
        return None
    seen: set[int] = set()
    discount = 0
    covered = 1024

    def visit(item: object) -> bool:
        nonlocal discount, covered
        kind = type(item)
        if kind not in (dict, list, str, int, float, bool, type(None)):
            return False
        covered += 128 + 2 * sys.getsizeof(item)
        if kind is str:
            covered += 4 * len(item)
        if covered > source_charge:
            return False
        if kind in (dict, list):
            identity = id(item)
            if identity in seen:
                return False
            seen.add(identity)
            measured = 2 * sys.getsizeof(item)
            # A split-table source dict can be smaller than its combined copy.
            growth = (96 + 48 * len(item)) if kind is dict else (80 + 16 * len(item))
            discount += 128 - max(0, growth - measured)
            if kind is dict:
                return all(
                    type(key) is str and visit(key) and visit(child) for key, child in item.items()
                )
            return all(visit(child) for child in item)
        if item is None or kind in (str, int, float, bool):
            discount += 128 + 2 * sys.getsizeof(item)
            return True
        return False

    if not visit(value):
        return None
    charge = max(2048, source_charge - discount)
    return charge if charge < source_charge else None


class SharedReadAccounting:
    """Hold source admission through every discounted copy and its descendants."""

    def __init__(self, source: ResponseOwner) -> None:
        self.source = source
        self.references = 1
        self.copy_bytes: int | None = None
        self._reserve: Callable[[ResponseOwner], bool] | None = None

    def enable(self, value: object) -> None:
        if len(self.source.entries) != 1 or len(self.source.copy_reservations) != 1:
            return
        ledger, key = self.source.entries[0]
        reservation = ledger.get(key)
        charge = reservation.get("bytes") if isinstance(reservation, Mapping) else reservation
        if type(charge) is not int:
            return
        copy_bytes = detached_copy_charge(value, charge)
        if copy_bytes is None:
            return
        self.copy_bytes = copy_bytes
        self._reserve = self.source.copy_reservations[0]
        self.source.copy_reservations[0] = self._reserve_copy

    def _reserve_copy(self, owner: ResponseOwner) -> bool:
        if self.source.released or self._reserve is None:
            return False
        owner.requested_copy_bytes = self.copy_bytes
        if not self._reserve(owner):
            return False
        # Replace the underlying callback too: descendants must pin this same
        # storage even when the flight/cache and their parent delivery are gone.
        owner.copy_reservations[-1] = self._reserve_copy
        self.references += 1
        owner.on_release.append(self.release)
        return True

    def release(self) -> None:
        self.references -= 1
        if self.references == 0:
            self.source.release()
