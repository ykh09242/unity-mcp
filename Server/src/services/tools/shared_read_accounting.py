"""Pin shared storage while discounting only proven immutable copy costs."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from models.response_limits import ResponseCopyPlan, ResponseOwner, response_size

# Avoid adding a traversal and ownership group to small response deliveries.
_MIN_SHARED_CHARGE = 256 * 1024


def detached_copy_charge(value: object, source_charge: int) -> int | None:
    """Discount only a final graph whose complete charge fits its pinned source."""
    if source_charge < _MIN_SHARED_CHARGE:
        return None
    plan = ResponseCopyPlan()
    charge = response_size(value, copy_plan=plan)
    return plan.detached_bytes if charge is not None and charge <= source_charge else None


class SharedReadAccounting:
    """Hold source admission through every discounted copy and its descendants."""

    def __init__(self, source: ResponseOwner) -> None:
        self.source = source
        self.references = 1
        self.copy_bytes: int | None = None
        self._reserve: Callable[[ResponseOwner], bool] | None = None

    def enable(self, value: object) -> None:
        plan = ResponseCopyPlan()
        charge = response_size(value, copy_plan=plan)
        self.enable_plan(plan, charge)

    def enable_plan(self, plan: ResponseCopyPlan, validated_charge: int | None) -> None:
        """Apply worker-produced evidence only on the ledger's owning loop."""
        if len(self.source.entries) != 1 or len(self.source.copy_reservations) != 1:
            return
        ledger, key = self.source.entries[0]
        reservation = ledger.get(key)
        charge = reservation.get("bytes") if isinstance(reservation, Mapping) else reservation
        if type(charge) is not int or validated_charge is None or validated_charge > charge:
            return
        if plan.detached_bytes is None:
            return
        self.copy_bytes = plan.detached_bytes
        self._reserve = self.source.copy_reservations[0]
        self.source.copy_reservations[0] = self._reserve_copy

    def _reserve_copy(self, owner: ResponseOwner) -> bool:
        if self.source.released or self._reserve is None:
            return False
        previous = owner.requested_copy_bytes
        try:
            owner.requested_copy_bytes = self.copy_bytes
            if not self._reserve(owner):
                return False
        finally:
            owner.requested_copy_bytes = previous
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
