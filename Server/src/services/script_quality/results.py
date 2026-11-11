"""Bounded, immutable retained evidence with reservations for unsettled native jobs."""

from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import json
from time import monotonic


class RetentionRefused(ValueError):
    """Capacity or identity prevents retaining this observation."""


@dataclass(frozen=True)
class ResultRecord:
    owner: tuple[str, str]
    encoded: bytes
    expires: float
    baseline: bool


class QualityResults:
    def __init__(
        self,
        *,
        max_records=16,
        scope_bytes=128 * 1024 * 1024,
        total_bytes=256 * 1024 * 1024,
        reservation_bytes=9 * 1024 * 1024,
        ttl=1800,
        clock=monotonic,
    ):
        self.max_records = max_records
        self.scope_bytes = scope_bytes
        self.total_bytes = total_bytes
        self.reservation_bytes = reservation_bytes
        self.ttl = ttl
        self.clock = clock
        self.active: dict[str, tuple[str, str]] = {}
        self.records: OrderedDict[str, ResultRecord] = OrderedDict()

    def _expire(self):
        now = self.clock()
        for identity, record in list(self.records.items()):
            if record.expires <= now:
                del self.records[identity]

    def _bytes(self, owner=None):
        return sum(
            len(record.encoded)
            for record in self.records.values()
            if owner is None or record.owner == owner
        ) + sum(
            self.reservation_bytes
            for scope in self.active.values()
            if owner is None or scope == owner
        )

    def _evict(self, owner=None):
        identity = next(
            (
                identity
                for identity, record in self.records.items()
                if owner is None or record.owner == owner
            ),
            None,
        )
        if identity is None:
            raise RetentionRefused("analysis_retention_capacity")
        del self.records[identity]

    def reserve(self, identity, owner):
        self._expire()
        if identity in self.active:
            if self.active[identity] != owner:
                raise RetentionRefused("analysis_identity_conflict")
            return
        if identity in self.records:
            raise RetentionRefused("analysis_identity_conflict")
        # Check the non-evictable lower bound before removing any completed evidence.
        if (
            sum(scope == owner for scope in self.active.values()) + 1
        ) * self.reservation_bytes > self.scope_bytes or (
            len(self.active) + 1
        ) * self.reservation_bytes > self.total_bytes:
            raise RetentionRefused("analysis_retention_capacity")
        while self._bytes(owner) + self.reservation_bytes > self.scope_bytes:
            self._evict(owner)
        while self._bytes() + self.reservation_bytes > self.total_bytes:
            self._evict()
        self.active[identity] = owner

    def settle(self, identity, owner):
        if self.active.get(identity) == owner:
            del self.active[identity]

    def complete(self, identity, owner, payload, *, baseline=False):
        if self.active.get(identity) != owner:
            raise RetentionRefused("analysis_reservation_missing")
        output = BytesIO()
        for chunk in json.JSONEncoder(
            ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).iterencode(payload):
            encoded = chunk.encode("utf-8")
            if output.tell() + len(encoded) > self.reservation_bytes:
                raise RetentionRefused("analysis_result_limit")
            output.write(encoded)
        self._expire()
        while sum(record.owner == owner for record in self.records.values()) >= self.max_records:
            self._evict(owner)
        while len(self.records) >= 512:
            self._evict()
        self.records[identity] = ResultRecord(
            owner, output.getvalue(), self.clock() + self.ttl, baseline
        )
        self.settle(identity, owner)

    def read(self, identity, owner, *, baseline=False):
        self._expire()
        record = self.records.get(identity)
        if record is None or record.owner != owner or baseline and not record.baseline:
            return None
        return json.loads(record.encoded)
