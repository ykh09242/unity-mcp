"""Process-local, quota-bounded retention of already admitted MCP result bytes."""

from __future__ import annotations

import base64
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from weakref import WeakKeyDictionary

from pydantic import JsonValue

from models.response_limits import MAX_RESPONSE_BYTES

PAGE_BYTES = 256 * 1024
RETENTION_SECONDS = 600
_MAX_ENTRIES = 64
_MAX_OWNER_ENTRIES = 16


@dataclass(frozen=True, slots=True)
class ResponseScope:
    """An authenticated principal, resolved project and optional legacy session."""

    principal: str
    project: str
    session: str


@dataclass(frozen=True, slots=True)
class ResponseAuthority:
    scope: ResponseScope
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Entry:
    payload: bytes
    authority: ResponseAuthority
    expires: float
    digest: str


class ResponseStore:
    """Own immutable result copies until explicit release or fixed expiry.

    This mutable cache never evicts an unexpired handle to admit another result.
    Expired bytes are reclaimed on the next cache operation or server collection.
    It imposes no admission limit on ordinary tool execution or small responses.
    """

    def __init__(
        self, max_bytes: int = 256 * 1024 * 1024, *, clock: Callable[[], float] = time.monotonic
    ):
        self._max_bytes = max_bytes
        self._clock = clock
        self._lock = Lock()
        self._entries: dict[str, _Entry] = {}
        self._bytes = 0

    def _prune(self) -> None:
        now = self._clock()
        for key, entry in tuple(self._entries.items()):
            if entry.expires <= now:
                self._bytes -= len(entry.payload)
                del self._entries[key]

    @property
    def retained_bytes(self) -> int:
        with self._lock:
            self._prune()
            return self._bytes

    def put(self, payload: bytes, authority: ResponseAuthority) -> dict[str, JsonValue] | None:
        """Retain only a bounded admitted encoding; charge its actual immutable bytes."""
        from uuid import uuid4

        if type(payload) is not bytes or len(payload) > MAX_RESPONSE_BYTES:
            return None
        digest = hashlib.sha256(payload).hexdigest()
        with self._lock:
            self._prune()
            # Repeated observation of an immutable task result reuses its fixed
            # lifetime instead of consuming the cache once per poll.
            for response_id, entry in self._entries.items():
                if entry.authority == authority and entry.digest == digest:
                    return self._handle(response_id, entry)
            owned = [
                entry
                for entry in self._entries.values()
                if entry.authority.scope.principal == authority.scope.principal
            ]
            if (
                len(self._entries) >= _MAX_ENTRIES
                or len(owned) >= _MAX_OWNER_ENTRIES
                or self._bytes + len(payload) > self._max_bytes
                or sum(len(entry.payload) for entry in owned) + len(payload)
                > max(MAX_RESPONSE_BYTES, self._max_bytes // 2)
            ):
                return None
            response_id = uuid4().hex
            self._entries[response_id] = _Entry(
                payload, authority, self._clock() + RETENTION_SECONDS, digest
            )
            self._bytes += len(payload)
            return self._handle(response_id, self._entries[response_id])

    def _handle(self, response_id: str, entry: _Entry) -> dict[str, JsonValue]:
        return {
            "state": "retained",
            "response_id": response_id,
            "total_bytes": len(entry.payload),
            "sha256": entry.digest,
            "page_bytes": PAGE_BYTES,
            "expires_in_seconds": max(0, int(entry.expires - self._clock())),
            "read_tool": "read_response",
            "encoding": "base64",
            "json_encoding": "utf-8",
        }

    def _entry(self, response_id: str, authority: ResponseAuthority) -> _Entry | None:
        self._prune()
        entry = self._entries.get(response_id)
        if (
            entry is None
            or entry.authority.scope != authority.scope
            or not entry.authority.permissions <= authority.permissions
        ):
            return None
        return entry

    def read(
        self, response_id: str, authority: ResponseAuthority, *, offset: int = 0
    ) -> dict[str, JsonValue] | None:
        """Copy at most one fixed page; no result graph or file path is accepted."""
        with self._lock:
            entry = self._entry(response_id, authority)
            if entry is None:
                return None
            if type(offset) is not int or not 0 <= offset <= len(entry.payload):
                raise ValueError("response offset is outside the retained result")
            end = min(offset + PAGE_BYTES, len(entry.payload))
            page = entry.payload[offset:end]
            result: dict[str, JsonValue] = {
                "state": "page",
                "response_id": response_id,
                "offset": offset,
                "next_offset": end if end < len(entry.payload) else None,
                "total_bytes": len(entry.payload),
                "sha256": entry.digest,
                "expires_in_seconds": max(0, int(entry.expires - self._clock())),
                "encoding": "base64",
                "json_encoding": "utf-8",
            }
        # Release/expiry can reclaim the full result while this small copy is encoded.
        result["data_base64"] = base64.b64encode(page).decode("ascii")
        return result

    def release(self, response_id: str, authority: ResponseAuthority) -> bool:
        with self._lock:
            entry = self._entry(response_id, authority)
            if entry is None:
                return False
            self._bytes -= len(entry.payload)
            del self._entries[response_id]
            return True


_stores: WeakKeyDictionary[object, ResponseStore] = WeakKeyDictionary()
_stores_lock = Lock()


def response_store(server: object) -> ResponseStore:
    """Server instances cannot share handles; collection releases their retained bytes."""
    with _stores_lock:
        store = _stores.get(server)
        if store is None:
            store = ResponseStore()
            _stores[server] = store
        return store
