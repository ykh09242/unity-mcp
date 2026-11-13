"""Bounded, immutable retained evidence with reservations for unsettled native jobs."""

from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import json
from struct import Struct
from time import monotonic

from services.script_quality.pages import DiagnosticView, classify_view


_OFFSET = Struct("<I")


class RetentionRefused(ValueError):
    """Capacity or identity prevents retaining this observation."""


@dataclass(frozen=True)
class ResultRecord:
    owner: tuple[str, str]
    header: bytes
    rows: bytes
    offsets: bytes
    selected_count: int
    has_selected: bool
    has_context: bool
    expires: float
    baseline: bool
    comparison_id: str | None
    comparison: DiagnosticView | None
    unclassified: DiagnosticView | None

    def metadata(self):
        return json.loads(self.header)

    def retained_bytes(self):
        return (
            len(self.header)
            + len(self.rows)
            + len(self.offsets)
            + sum(view.byte_size for view in (self.comparison, self.unclassified) if view)
        )

    def read_row(self, index):
        start = _OFFSET.unpack_from(self.offsets, index * _OFFSET.size)[0]
        end = _OFFSET.unpack_from(self.offsets, (index + 1) * _OFFSET.size)[0]
        return json.loads(self.rows[start:end])

    def read(self, *, include_context=True):
        payload = self.metadata()
        if self.has_selected:
            payload["diagnostics"] = [self.read_row(i) for i in range(self.selected_count)]
        if self.has_context and include_context:
            total = len(self.offsets) // _OFFSET.size - 1
            payload["context_diagnostics"] = [
                self.read_row(i) for i in range(self.selected_count, total)
            ]
        return payload


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
            record.retained_bytes()
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

    def complete(
        self, identity, owner, payload, *, baseline=False, comparison_id=None, comparison_token=None
    ):
        if self.active.get(identity) != owner:
            raise RetentionRefused("analysis_reservation_missing")
        header = {
            k: v for k, v in payload.items() if k not in {"diagnostics", "context_diagnostics"}
        }
        header_bytes = _encode(header, self.reservation_bytes)
        selected = payload.get("diagnostics", [])
        context = payload.get("context_diagnostics", [])
        offsets = bytearray(_OFFSET.pack(0)) if selected or context else bytearray()
        row_limit = (
            self.reservation_bytes
            - len(header_bytes)
            - (len(selected) + len(context) + 1) * _OFFSET.size
        )
        rows = BytesIO()
        for group in (selected, context):
            for row in group:
                rows.write(_encode(row, row_limit - rows.tell()))
                offsets.extend(_OFFSET.pack(rows.tell()))

        before = self.record(comparison_id, owner, baseline=True) if comparison_id else None
        missing = comparison = None
        if "source_token" in header and "diagnostics" in payload:
            missing = classify_view(identity, payload, None)
            if (
                before is not None
                and comparison_token
                and before.metadata().get("source_token") == comparison_token
            ):
                comparison = classify_view(identity, payload, before.read(include_context=False))
        record = ResultRecord(
            owner,
            header_bytes,
            rows.getvalue(),
            bytes(offsets),
            len(selected),
            "diagnostics" in payload,
            "context_diagnostics" in payload,
            self.clock() + self.ttl,
            baseline,
            comparison_id,
            comparison,
            missing,
        )
        if record.retained_bytes() > self.reservation_bytes:
            raise RetentionRefused("analysis_result_limit")
        self._expire()
        while sum(record.owner == owner for record in self.records.values()) >= self.max_records:
            self._evict(owner)
        while len(self.records) >= 512:
            self._evict()
        self.records[identity] = record
        self.settle(identity, owner)

    def record(self, identity, owner, *, baseline=False):
        self._expire()
        record = self.records.get(identity)
        if record is None or record.owner != owner or baseline and not record.baseline:
            return None
        return record

    def read(self, identity, owner, *, baseline=False):
        record = self.record(identity, owner, baseline=baseline)
        return record.read() if record else None


def _encode(value, limit):
    output = BytesIO()
    for chunk in json.JSONEncoder(
        ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).iterencode(value):
        encoded = chunk.encode("utf-8")
        if output.tell() + len(encoded) > limit:
            raise RetentionRefused("analysis_result_limit")
        output.write(encoded)
    return output.getvalue()
