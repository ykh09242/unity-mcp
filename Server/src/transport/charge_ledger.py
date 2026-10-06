"""Owner-loop reservation mapping with immutable charges and indexed totals."""
from collections.abc import Hashable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Self, TypeAlias, TypeVar

ChargeValue: TypeAlias = int | str | None
ChargeRecord: TypeAlias = Mapping[str, ChargeValue]
IndexKey = TypeVar('IndexKey', bound=Hashable)


@dataclass(frozen=True, slots=True)
class _Charge:
    size: int
    user: str | None
    session: str
    record: ChargeRecord


def _freeze(record: ChargeRecord) -> _Charge:
    size, user, session = record['bytes'], record['user_id'], record['session_id']
    if type(size) is not int or size < 0:
        raise ValueError('Reservation bytes must be a nonnegative integer')
    if user is not None and type(user) is not str:
        raise TypeError('Reservation principal must be a string or None')
    if type(session) is not str:
        raise TypeError('Reservation session must be a string')
    return _Charge(size, user, session, MappingProxyType({
        'bytes': size, 'user_id': user, 'session_id': session}))


class ChargeLedger(MutableMapping[Hashable, ChargeRecord]):
    """All mutations update this ledger's totals, including captured-owner pop.

    Admission and mutation are synchronous on one owning event loop. Updating
    totals cannot yield between budget observation and reservation insertion.
    """

    def __init__(self, records: Mapping[Hashable, ChargeRecord] | None = None) -> None:
        self._records: dict[Hashable, _Charge] = {}
        self._users: dict[str | None, int] = {}
        self._sessions: dict[str, int] = {}
        self._total_bytes = 0
        if records is not None:
            self.update(records)

    def __getitem__(self, key: Hashable) -> ChargeRecord:
        return self._records[key].record

    def __iter__(self) -> Iterator[Hashable]:
        return iter(self._records)

    def __len__(self) -> int:
        return len(self._records)

    @staticmethod
    def _change(index: dict[IndexKey, int], key: IndexKey, amount: int) -> None:
        total = index.get(key, 0) + amount
        if total:
            index[key] = total
        else:
            index.pop(key, None)

    def _adjust(self, reservation: _Charge, direction: int) -> None:
        amount = reservation.size * direction
        self._total_bytes += amount
        self._change(self._users, reservation.user, amount)
        self._change(self._sessions, reservation.session, amount)

    @staticmethod
    def _restore(index: dict[IndexKey, int], saved: dict[IndexKey, int | None]) -> None:
        for key, value in saved.items():
            if value is None:
                index.pop(key, None)
            else:
                index[key] = value

    def __setitem__(self, key: Hashable, record: ChargeRecord) -> None:
        reservation = _freeze(record)
        previous = self._records.get(key)
        affected = (reservation, previous) if previous is not None else (reservation,)
        users = {entry.user: self._users.get(entry.user) for entry in affected}
        sessions = {entry.session: self._sessions.get(entry.session) for entry in affected}
        total = self._total_bytes
        try:
            if previous is not None:
                self._adjust(previous, -1)
            self._adjust(reservation, 1)
            self._records[key] = reservation
        except Exception:
            # Allocation or a user-defined Hashable may fail during insertion.
            # Restore the captured accounting before propagating that failure.
            self._total_bytes = total
            self._restore(self._users, users)
            self._restore(self._sessions, sessions)
            raise

    def __delitem__(self, key: Hashable) -> None:
        self._adjust(self._records.pop(key), -1)

    def popitem(self) -> tuple[Hashable, ChargeRecord]:
        key, reservation = self._records.popitem()
        self._adjust(reservation, -1)
        return key, reservation.record

    def clear(self) -> None:
        self._records.clear()
        self._users.clear()
        self._sessions.clear()
        self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def user_bytes(self, user_id: str | None) -> int:
        return self._users.get(user_id, 0)

    def session_bytes(self, session_id: str) -> int:
        return self._sessions.get(session_id, 0)

    def __ior__(self, records: Mapping[Hashable, ChargeRecord]) -> Self:
        self.update(records)
        return self
