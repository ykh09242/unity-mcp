"""Server-owned bounds and continuation invariants for collector streams.

Limits come only from the server operator's environment, never tool/plugin input.
An exhausted attempt keeps durable checkpoints and requires retry or a raised limit.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import json
import os
import re
import time
from typing import Final

from services.reference_graph.store import RevisionConflict

MAX_OFFSET: Final = 2**63 - 1
_GUID: Final = re.compile(r"[a-f0-9]{32}\Z")
_BASE64: Final = re.compile(r"[A-Za-z0-9+/]*={0,2}\Z")
ENV_PREFIX: Final = "UNITY_MCP_REFERENCE_GRAPH_"


class CollectionBudgetExceeded(RuntimeError):
    """An incomplete attempt reached an operator-owned resource ceiling."""

    def __init__(self, name: str, limit: int):
        self.name = name
        self.limit = limit
        super().__init__(
            f"Reference collection exceeded {name}={limit}; evidence remains incomplete. "
            f"Retry or have the server operator raise {ENV_PREFIX}{name.upper()}."
        )


@dataclass(frozen=True, slots=True)
class CollectionLimits:
    """Ample finite ceilings; operators can explicitly provision larger projects."""

    max_pages: int = 1_000_000
    max_items: int = 50_000_000
    max_bytes: int = 64 * 1024**3
    max_seconds: int = 24 * 60 * 60
    max_source_bytes: int = 128 * 1024**2
    max_dependency_items: int = 1_000_000

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or not 1 <= value <= MAX_OFFSET:
                raise ValueError(f"{field.name} must be a positive integer at most {MAX_OFFSET}")

    @classmethod
    def from_environment(cls) -> CollectionLimits:
        values = {}
        for field in fields(cls):
            name = ENV_PREFIX + field.name.upper()
            raw = os.environ.get(name)
            if raw is not None:
                try:
                    values[field.name] = int(raw)
                except ValueError as error:
                    raise ValueError(f"{name} must be a positive integer") from error
        return cls(**values)


class CollectionBudget:
    """Mutable counters are owned by one reconciler attempt and its drained workers."""

    def __init__(self, limits: CollectionLimits):
        self.limits = limits
        self.started = time.monotonic()
        self.pages = 0
        self.items = 0
        self.bytes = 0
        self.decoded_bytes = 0

    def remaining(self) -> float:
        remaining = self.limits.max_seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise CollectionBudgetExceeded("max_seconds", self.limits.max_seconds)
        return remaining

    def request(self) -> None:
        self.remaining()
        if self.pages >= self.limits.max_pages:
            raise CollectionBudgetExceeded("max_pages", self.limits.max_pages)
        self.pages += 1

    def response(self, page: dict) -> None:
        # Iteration bounds work before accumulating another serialized response.
        # ASCII JSON chunks have one byte per character, including escaped Unicode.
        for chunk in json.JSONEncoder(ensure_ascii=True, allow_nan=False).iterencode(page):
            self.remaining()
            if len(chunk) > self.limits.max_bytes - self.bytes:
                raise CollectionBudgetExceeded("max_bytes", self.limits.max_bytes)
            self.bytes += len(chunk)

    def admit_items(self, count: int) -> None:
        self.remaining()
        if count > self.limits.max_items - self.items:
            raise CollectionBudgetExceeded("max_items", self.limits.max_items)
        self.items += count

    def source(self, size: int, accumulated: int) -> None:
        self.remaining()
        if size > self.limits.max_source_bytes - accumulated:
            raise CollectionBudgetExceeded("max_source_bytes", self.limits.max_source_bytes)
        if size > self.limits.max_bytes - self.decoded_bytes:
            raise CollectionBudgetExceeded("max_bytes", self.limits.max_bytes)
        self.decoded_bytes += size

    def dependencies(self, size: int) -> None:
        if size > self.limits.max_dependency_items:
            raise CollectionBudgetExceeded("max_dependency_items", self.limits.max_dependency_items)


def integer(value, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_OFFSET:
        raise RevisionConflict(f"Collector {name} must be a nonnegative 64-bit integer")
    return value


def boolean(value, name: str) -> bool:
    if type(value) is not bool:
        raise RevisionConflict(f"Collector {name} must be a boolean")
    return value


def text(value, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RevisionConflict(f"Collector {name} must be a nonempty string")
    return value


def rows(page: dict, key: str, maximum: int) -> list:
    result = page.get(key)
    if not isinstance(result, list) or len(result) > maximum:
        raise RevisionConflict(f"Collector {key} must be a page of at most {maximum} items")
    return result


def ordered_guids(values, previous: str = "") -> str:
    """Native snapshots are ordinal-sorted; one high-water mark rejects all overlap."""
    for value in values:
        if not isinstance(value, str) or _GUID.fullmatch(value) is None or value <= previous:
            raise RevisionConflict("Collector GUID pages must be distinct and strictly increasing")
        previous = value
    return previous


def continuation(page: dict, offset: int, size: int, *, scanned: int | None = None) -> int | None:
    if "next_offset" not in page:
        raise RevisionConflict("Collector page requires an explicit next_offset")
    value = page["next_offset"]
    integer(offset, "offset")
    if size > MAX_OFFSET - offset:
        raise RevisionConflict("Collector continuation overflowed")
    if value is None:
        return None
    following = integer(value, "next_offset")
    if following <= offset or following < offset + size:
        raise RevisionConflict("Collector continuation did not advance past its page")
    if scanned is None and following != offset + size:
        raise RevisionConflict("Collector continuation does not match its page length")
    if scanned is not None and following - offset > scanned:
        raise RevisionConflict("Collector continuation skipped past its requested page")
    return following


def decoded_size(encoded) -> int:
    if (
        not isinstance(encoded, str)
        or len(encoded) > 87384
        or len(encoded) % 4
        or _BASE64.fullmatch(encoded) is None
    ):
        raise RevisionConflict("Collector source bytes must be valid base64")
    size = len(encoded) // 4 * 3 - (len(encoded) - len(encoded.rstrip("=")))
    if size > 65536:
        raise RevisionConflict("Collector source exceeded its 65536-byte page")
    return size


def change_page(page: dict, after: int, *, maximum: int, epoch: str) -> tuple[list, int, bool]:
    """Journal revisions are consecutive, and continuation exactly matches the page."""
    integer(after, "after")
    if text(page.get("epoch"), "epoch") != epoch:
        raise RevisionConflict("Collector journal epoch changed during collection")
    revision = integer(page.get("revision"), "revision")
    changes = rows(page, "changes", maximum)
    more = boolean(page.get("has_more"), "has_more")
    following = integer(page.get("next_after"), "next_after")
    previous = after
    for row in changes:
        if not isinstance(row, dict) or integer(row.get("revision"), "revision") != previous + 1:
            raise RevisionConflict("Collector change revisions must be consecutive and advancing")
        previous = row["revision"]
    if (
        following != previous
        or (more and not changes)
        or following > revision
        or more != (following < revision)
    ):
        raise RevisionConflict("Collector journal continuation did not match its changes")
    return changes, following, more
