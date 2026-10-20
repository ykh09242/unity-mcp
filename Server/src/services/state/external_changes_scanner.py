from __future__ import annotations

import os
from hashlib import blake2b
import json
import time
import asyncio
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.request import url2pathname

from core.config import config
from services.state.external_changes_walk import ScanCursor

_EMPTY_RESULT = {
    "external_changes_dirty": False,
    "external_changes_last_seen_unix_ms": None,
    "dirty_since_unix_ms": None,
    "last_cleared_unix_ms": None,
    "external_changes_scan_complete": None,
    "external_changes_scan_limited": None,
}


def _now_unix_ms() -> int:
    return int(time.time() * 1000)


def _in_pytest() -> bool:
    # Keep scanner inert during the Python integration suite unless explicitly invoked.
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


@dataclass
class ExternalChangesState:
    project_root: str | None = None
    last_scan_unix_ms: int | None = None
    last_seen_mtime_ns: int | None = None
    dirty: bool = False
    dirty_since_unix_ms: int | None = None
    external_changes_last_seen_unix_ms: int | None = None
    last_cleared_unix_ms: int | None = None
    # Resolved file: dependency paths; directory existence is checked on each scan.
    extra_roots: list[str] | None = None
    manifest_last_mtime_ns: int | None = None
    manifest_content_hash: bytes | None = None
    cursor: ScanCursor | None = None
    fingerprint: tuple[int, int] | None = None
    revision: int = 0
    # Coverage is unknown until a verified local project root is available.
    scan_complete: bool | None = None
    scan_limited: bool | None = None


@dataclass(frozen=True)
class ExternalChangesSnapshot:
    state: ExternalChangesState
    last_seen_mtime_ns: int | None
    revision: int = 0


class ExternalChangesScanner:
    """Detect external changes with resumable traversal and complete-pass fingerprints.

    Entry, time and byte budgets bound each call. Partial coverage is explicit;
    file deletion, rename and timestamp-preserving edits change the fingerprint.
    """

    def __init__(
        self,
        *,
        scan_interval_ms: int = 1500,
        max_entries: int = 20000,
        max_states: int = 32,
        state_ttl_ms: int = 900000,
        max_scan_seconds: float = 0.25,
        max_manifest_bytes: int = 1024 * 1024,
        max_extra_roots: int = 256,
        max_scan_bytes: int = 2 * 1024 * 1024,
    ):
        self._states: OrderedDict[str, ExternalChangesState] = OrderedDict()
        self._last_access: dict[str, int] = {}
        self._scan_interval_ms = int(scan_interval_ms)
        self._max_entries = int(max_entries)
        self._max_states = max(1, int(max_states))
        self._state_ttl_ms = max(1, int(state_ttl_ms))
        self._max_scan_seconds = max(0.001, float(max_scan_seconds))
        self._max_manifest_bytes = max(1, int(max_manifest_bytes))
        self._max_extra_roots = max(1, int(max_extra_roots))
        self._max_scan_bytes = max(1, int(max_scan_bytes))
        self._scan_state: ExternalChangesState | None = None
        self._scan_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._active_stop: threading.Event | None = None
        self._scan_deadline = 0.0

    def _get_state(self, instance_id: str) -> ExternalChangesState:
        now = _now_unix_ms()
        with self._state_lock:
            expired = [
                key
                for key in self._states
                if now - self._last_access.get(key, now) >= self._state_ttl_ms
            ]
            for key in expired:
                self._release_state(self._states.pop(key))
                self._last_access.pop(key, None)
            st = self._states.pop(instance_id, None)
            if st is None:
                if len(self._states) >= self._max_states:
                    key, retired = self._states.popitem(last=False)
                    self._release_state(retired)
                    self._last_access.pop(key, None)
                st = ExternalChangesState()
            self._last_access[instance_id] = now
            self._states[instance_id] = st
            return st

    def set_project_root(self, instance_id: str, project_root: str | None) -> None:
        if config.http_remote_hosted:
            return
        if len(instance_id) > 256 or (
            project_root and (len(project_root) > 4096 or not Path(project_root).is_absolute())
        ):
            return
        with self._state_lock:
            st = self._get_state(instance_id)
            if project_root and (
                st.project_root is None or Path(st.project_root) != Path(project_root)
            ):
                # Cached package paths, timestamps and dirty state belong to this project.
                self._release_state(st)
                self._states[instance_id] = ExternalChangesState(
                    project_root=project_root, scan_complete=False, scan_limited=False
                )

    def capture_dirty_state(self, instance_id: str) -> ExternalChangesSnapshot | None:
        """Capture the observed edits before dispatch, without scanning or advancing them."""
        if config.http_remote_hosted or len(instance_id) > 256:
            return None
        with self._state_lock:
            st = self._get_state(instance_id)
            return ExternalChangesSnapshot(st, st.last_seen_mtime_ns, st.revision)

    def clear_dirty(
        self, instance_id: str, *, expected: ExternalChangesSnapshot | None = None
    ) -> bool:
        """Acknowledge edits, optionally only if the pre-dispatch snapshot still matches."""
        if config.http_remote_hosted or len(instance_id) > 256:
            return False
        with self._state_lock:
            st = self._get_state(instance_id)
            if expected is not None and (
                st is not expected.state
                or st.last_seen_mtime_ns != expected.last_seen_mtime_ns
                or st.revision != expected.revision
            ):
                return False
            st.dirty = False
            st.dirty_since_unix_ms = None
            st.last_cleared_unix_ms = _now_unix_ms()
            # Preserve the watermark so an edit not yet sampled remains detectable.
            return True

    def _release_state(self, state: ExternalChangesState) -> None:
        if state is self._scan_state and self._active_stop is not None:
            self._active_stop.set()
        elif state.cursor is not None:
            state.cursor.close()
            state.cursor = None

    def close(self) -> None:
        """Retire cached cursors; an active worker cooperatively closes its own handles."""
        with self._state_lock:
            for state in self._states.values():
                self._release_state(state)
            self._states.clear()
            self._last_access.clear()

    def _scan_paths_max_mtime_ns(self, roots: Iterable[Path]) -> int | None:
        # Keep the existing scan boundary for callers and controlled cancellation tests.
        state = self._scan_state
        cursor = state.cursor if state is not None else None
        if cursor is None:
            watched = (
                Path(state.project_root) / "Packages/manifest.json"
                if state is not None and state.project_root
                else None
            )
            cursor = ScanCursor(roots, watched)
            if state is not None:
                state.cursor = cursor
        cursor.advance(self._max_entries, self._max_scan_bytes, self._scan_stopped)
        if state is None:
            cursor.close()
        else:
            state.scan_complete = cursor.complete and not cursor.limited
            state.scan_limited = cursor.limited
        return cursor.newest

    def _scan_stopped(self) -> bool:
        return (
            config.http_remote_hosted
            or (self._active_stop is not None and self._active_stop.is_set())
            or (self._scan_deadline > 0 and time.monotonic() >= self._scan_deadline)
        )

    @staticmethod
    def _existing_directory_roots(roots: Iterable[str]) -> list[Path]:
        existing = []
        for root in roots:
            candidate = Path(root)
            try:
                if candidate.is_dir():
                    existing.append(candidate)
            except OSError:
                continue
        return existing

    def _resolve_manifest_extra_roots(
        self, project_root: Path, st: ExternalChangesState
    ) -> list[Path]:
        """
        Parse Packages/manifest.json for local file: dependencies and resolve them to absolute paths.
        Returns a list of Paths that exist and are directories.
        """
        manifest_path = project_root / "Packages" / "manifest.json"
        if self._scan_stopped():
            return []
        try:
            stat = manifest_path.stat()
        except OSError:
            st.extra_roots = []
            st.manifest_last_mtime_ns = None
            return []

        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        if st.extra_roots is not None and st.manifest_last_mtime_ns == mtime_ns:
            return self._existing_directory_roots(st.extra_roots)

        try:
            if stat.st_size > self._max_manifest_bytes:
                raise ValueError("Local package manifest exceeds scanner budget")
            # Bound the read itself, including a file that grows after stat.
            with manifest_path.open("rb") as source:
                raw = source.read(self._max_manifest_bytes + 1)
            if len(raw) > self._max_manifest_bytes:
                raise ValueError("Local package manifest exceeds scanner budget")
            st.manifest_content_hash = blake2b(raw, digest_size=32).digest()
            doc = json.loads(raw)
        except (OSError, ValueError, UnicodeError):
            st.extra_roots = []
            st.manifest_last_mtime_ns = mtime_ns
            return []

        deps = doc.get("dependencies") if isinstance(doc, dict) else None
        if not isinstance(deps, dict):
            st.extra_roots = []
            st.manifest_last_mtime_ns = mtime_ns
            return []

        roots: list[str] = []
        base_dir = manifest_path.parent

        for _, ver in deps.items():
            if self._scan_stopped() or len(roots) >= self._max_extra_roots:
                break
            if not isinstance(ver, str):
                continue
            v = ver.strip()
            if not v.startswith("file:"):
                continue
            suffix = v[len("file:") :].strip()
            if not suffix or len(suffix) > 4096:
                continue
            # Decode explicit file:/// URIs, including Windows drives.
            # Keep absolute native and relative file: paths literal (they may contain '%').
            if suffix.startswith("///"):
                candidate = Path(url2pathname(suffix))
            elif suffix.startswith("/"):
                candidate = Path(suffix)
            else:
                candidate = (base_dir / suffix).resolve()
            roots.append(str(candidate))

        # De-dupe, preserve order
        deduped: list[str] = []
        seen = set()
        for r in roots:
            if r not in seen:
                seen.add(r)
                deduped.append(r)

        st.extra_roots = deduped
        st.manifest_last_mtime_ns = mtime_ns
        return self._existing_directory_roots(deduped)

    def update_and_get(self, instance_id: str) -> dict[str, int | bool | None]:
        """Run one bounded local scan, or return cached state when another is active."""
        if config.http_remote_hosted or len(instance_id) > 256:
            return _EMPTY_RESULT.copy()
        if not self._scan_lock.acquire(blocking=False):
            return self._cached_result(instance_id)
        return self._run_update_locked(instance_id, threading.Event())

    async def update_and_get_async(self, instance_id: str) -> dict[str, int | bool | None]:
        """Offload local traversal; cancellation requests cooperative worker stop."""
        if config.http_remote_hosted or len(instance_id) > 256:
            return _EMPTY_RESULT.copy()
        if not self._scan_lock.acquire(blocking=False):
            return self._cached_result(instance_id)
        # A throttled read needs no filesystem work. Avoid queuing its cached
        # snapshot behind unrelated commands in the shared executor, while
        # retaining the same state expiry and LRU updates as a worker read.
        with self._state_lock:
            st = self._get_state(instance_id)
            if (
                st.last_scan_unix_ms is not None
                and _now_unix_ms() - st.last_scan_unix_ms < self._scan_interval_ms
            ):
                self._scan_lock.release()
                return self._cached_result(instance_id)
        stop = threading.Event()
        try:
            from services.execution_receipt_context import submit_owned_executor

            future = submit_owned_executor(
                asyncio.get_running_loop(), None, self._run_update_locked, instance_id, stop
            )
        except BaseException:
            self._scan_lock.release()
            raise
        try:
            return await asyncio.shield(future)
        finally:
            stop.set()

    def _cached_result(self, instance_id: str) -> dict[str, int | bool | None]:
        with self._state_lock:
            st = self._states.get(instance_id)
            if st is None:
                return _EMPTY_RESULT.copy()
            return {
                "external_changes_dirty": st.dirty,
                "external_changes_last_seen_unix_ms": st.external_changes_last_seen_unix_ms,
                "dirty_since_unix_ms": st.dirty_since_unix_ms,
                "last_cleared_unix_ms": st.last_cleared_unix_ms,
                "external_changes_scan_complete": st.scan_complete,
                "external_changes_scan_limited": st.scan_limited,
            }

    def _run_update_locked(
        self, instance_id: str, stop: threading.Event
    ) -> dict[str, int | bool | None]:
        self._active_stop = stop
        self._scan_deadline = time.monotonic() + self._max_scan_seconds
        try:
            if config.http_remote_hosted:
                return _EMPTY_RESULT.copy()
            return self._update_and_get(instance_id)
        finally:
            state = self._scan_state
            if (
                state is not None
                and state.cursor is not None
                and (
                    stop.is_set()
                    or config.http_remote_hosted
                    or self._states.get(instance_id) is not state
                )
            ):
                state.cursor.close()
                state.cursor = None
                state.scan_complete = False
            self._scan_state = None
            self._active_stop = None
            self._scan_deadline = 0.0
            self._scan_lock.release()

    def _update_and_get(self, instance_id: str) -> dict[str, int | bool | None]:
        """
        Returns a small dict suitable for embedding in editor_state_v2.assets:
          - external_changes_dirty
          - external_changes_last_seen_unix_ms
          - dirty_since_unix_ms
          - last_cleared_unix_ms
        """
        st = self._get_state(instance_id)

        if _in_pytest():
            return {
                "external_changes_dirty": st.dirty,
                "external_changes_last_seen_unix_ms": st.external_changes_last_seen_unix_ms,
                "dirty_since_unix_ms": st.dirty_since_unix_ms,
                "last_cleared_unix_ms": st.last_cleared_unix_ms,
                "external_changes_scan_complete": st.scan_complete,
                "external_changes_scan_limited": st.scan_limited,
            }

        now = _now_unix_ms()
        if (
            st.last_scan_unix_ms is not None
            and (now - st.last_scan_unix_ms) < self._scan_interval_ms
        ):
            return {
                "external_changes_dirty": st.dirty,
                "external_changes_last_seen_unix_ms": st.external_changes_last_seen_unix_ms,
                "dirty_since_unix_ms": st.dirty_since_unix_ms,
                "last_cleared_unix_ms": st.last_cleared_unix_ms,
                "external_changes_scan_complete": st.scan_complete,
                "external_changes_scan_limited": st.scan_limited,
            }

        st.last_scan_unix_ms = now

        project_root = st.project_root
        if not project_root:
            return {
                "external_changes_dirty": st.dirty,
                "external_changes_last_seen_unix_ms": st.external_changes_last_seen_unix_ms,
                "dirty_since_unix_ms": st.dirty_since_unix_ms,
                "last_cleared_unix_ms": st.last_cleared_unix_ms,
                "external_changes_scan_complete": st.scan_complete,
                "external_changes_scan_limited": st.scan_limited,
            }

        root = Path(project_root)
        paths = [root / "Assets", root / "ProjectSettings", root / "Packages"]
        # Include any local package roots referenced by file: deps in Packages/manifest.json
        try:
            paths.extend(self._resolve_manifest_extra_roots(root, st))
        except Exception:
            pass
        self._scan_state = st
        newest = self._scan_paths_max_mtime_ns(paths)
        with self._state_lock:
            if self._states.get(instance_id) is not st or self._scan_stopped():
                return self._cached_result(instance_id)
            cursor = st.cursor
            if cursor is not None and cursor.complete:
                current = (cursor.count, cursor.digest)
                if (
                    cursor.watched_digest is not None
                    and cursor.watched_digest != st.manifest_content_hash
                ):
                    st.manifest_last_mtime_ns = None
                changed = cursor.limited or (
                    st.fingerprint is not None and current != st.fingerprint
                )
                if not cursor.limited:
                    st.fingerprint = current
                if newest is not None:
                    st.last_seen_mtime_ns = (
                        newest
                        if st.last_seen_mtime_ns is None
                        else max(st.last_seen_mtime_ns, newest)
                    )
                if changed:
                    st.revision += 1
                    st.external_changes_last_seen_unix_ms = now
                    if not st.dirty:
                        st.dirty = True
                        st.dirty_since_unix_ms = now
                cursor.close()
                st.cursor = None

        return {
            "external_changes_dirty": st.dirty,
            "external_changes_last_seen_unix_ms": st.external_changes_last_seen_unix_ms,
            "dirty_since_unix_ms": st.dirty_since_unix_ms,
            "last_cleared_unix_ms": st.last_cleared_unix_ms,
            "external_changes_scan_complete": st.scan_complete,
            "external_changes_scan_limited": st.scan_limited,
        }


# Global singleton (simple, process-local)
external_changes_scanner = ExternalChangesScanner()
