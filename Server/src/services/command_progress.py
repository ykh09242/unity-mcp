"""Bounded foreground observations; reporting never owns or resumes Unity work."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Literal

import anyio

if TYPE_CHECKING:
    from fastmcp import Context

CAPABILITY = "command_progress_v1"
MAX_REPORTERS = 256
MAX_PROGRESS_FRAMES = 256
MAX_PROGRESS_BYTES = 512
_NOTIFICATION_SECONDS = 0.2
_SEND_INTERVAL = 0.1


@dataclass(frozen=True, slots=True)
class CommandProgressUpdate:
    completed: int
    total: int
    stage: Literal["batch_items"]


@dataclass(frozen=True, slots=True)
class ProgressSource:
    """Exact admitted command evidence, independent of subsequent ContextVar changes."""

    transport: str
    command_id: str
    session_id: str
    generation: str
    user_id: str | None
    expires_at: float | None
    guard: Callable[[], Awaitable[bool]]


_owner: ContextVar[_Reporter | None] = ContextVar("unity_foreground_progress", default=None)
_admission_lock = RLock()
_active_reporters = 0


class ProgressSink:
    """Opaque worker capability; closing severs the original SDK callback/evidence."""

    def __init__(self, reporter: _Reporter, total: int) -> None:
        self._reporter, self._total = reporter, total

    def bind(self, source: ProgressSource) -> bool:
        return self._reporter.bind(source)

    def offer(self, update: CommandProgressUpdate) -> bool:
        return self._reporter.offer(update, self._total)

    def offer_from_thread(self, update: CommandProgressUpdate) -> None:
        self._reporter.offer(update, self._total)

    def close(self) -> None:
        self._reporter.close()


class _Reporter:
    """One latest slot, one wake and one cancellable SDK sender per request."""

    def __init__(self, callback, group: anyio.abc.TaskGroup) -> None:
        self._loop = asyncio.get_running_loop()
        self._callback = callback
        self._group = group
        self._lock = RLock()
        self._wake = anyio.Event()
        self._wake_pending = False
        self._closed = False
        self._claimed = False
        self._source: ProgressSource | None = None
        self._latest: CommandProgressUpdate | None = None
        self._last_received = 0
        self._next_send_at = 0.0
        self._cancel: anyio.CancelScope | None = None

    def claim(self, total: int) -> ProgressSink | None:
        with self._lock:
            if self._closed or self._claimed or type(total) is not int or not 1 <= total <= 100:
                return None
            group = self._group
            if group is None:
                return None
            self._group = None
            self._claimed = True
            sink = ProgressSink(self, total)
        group.start_soon(self._run)
        return sink

    def bind(self, source: ProgressSource) -> bool:
        with self._lock:
            if self._closed or self._source is not None:
                return False
            if source.expires_at is not None and source.expires_at <= self._loop.time():
                return False
            self._source = source
            return True

    def offer(self, update: CommandProgressUpdate, total: int) -> bool:
        with self._lock:
            if (
                self._closed
                or self._source is None
                or type(update.completed) is not int
                or type(update.total) is not int
                or update.total != total
                or not self._last_received < update.completed <= total
                or update.stage != "batch_items"
            ):
                return False
            self._last_received = update.completed
            self._latest = update
            if not self._wake_pending:
                self._wake_pending = True
                self._loop.call_soon_threadsafe(self._notify)
            return True

    def _notify(self) -> None:
        with self._lock:
            if not self._closed:
                self._wake.set()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._latest = None
            self._source = None
            self._callback = None
            self._group = None
        self._loop.call_soon_threadsafe(self._stop)

    def _stop(self) -> None:
        if self._cancel is not None:
            self._cancel.cancel()

    async def _run(self) -> None:
        with anyio.CancelScope() as scope:
            self._cancel = scope
            try:
                while True:
                    with self._lock:
                        if self._closed:
                            return
                        wake = self._wake
                    await wake.wait()
                    with self._lock:
                        self._wake = anyio.Event()
                        self._wake_pending = False
                        delay = max(0.0, self._next_send_at - self._loop.time())
                    if delay:
                        await anyio.sleep(delay)
                    with self._lock:
                        if self._closed:
                            return
                        update, source = self._latest, self._source
                        self._latest = None
                        callback = self._callback
                    if update is None or source is None or callback is None:
                        continue
                    remaining = (
                        source.expires_at - self._loop.time()
                        if source.expires_at is not None
                        else _NOTIFICATION_SECONDS
                    )
                    if remaining <= 0:
                        self.close()
                        return
                    try:
                        with anyio.move_on_after(min(_NOTIFICATION_SECONDS, remaining)) as send:
                            if not await source.guard():
                                self.close()
                                return
                            with self._lock:
                                if (
                                    self._closed
                                    or self._source is not source
                                    or (
                                        source.expires_at is not None
                                        and source.expires_at <= self._loop.time()
                                    )
                                ):
                                    return
                                self._next_send_at = self._loop.time() + _SEND_INTERVAL
                            await callback(update.completed, update.total, "Batch items settled")
                        if send.cancel_called:
                            self.close()
                            return
                    except Exception:
                        # Optional public callback/authorization boundary: never
                        # turn report failure into command failure or work retry.
                        self.close()
                        return
            finally:
                self._cancel = None


def _eligible(ctx: Context | None) -> bool:
    rc = getattr(ctx, "request_context", None)
    meta = getattr(rc, "meta", None)
    if meta is None:
        return False
    token = meta.get("progressToken")
    if type(token) is int:
        return -(2**53) < token < 2**53
    if type(token) is str:
        # Reject without allocating an encoded copy of an oversized token.
        return len(token) <= 256 and len(token.encode("utf-8", errors="replace")) <= 256
    return False


@asynccontextmanager
async def foreground_progress(ctx: Context | None):
    """Own the reporter only through this public invocation, including cancellation."""
    global _active_reporters
    admitted = False
    if _eligible(ctx):
        with _admission_lock:
            if _active_reporters < MAX_REPORTERS:
                _active_reporters += 1
                admitted = True
    if not admitted:
        token = _owner.set(None)
        try:
            yield
        finally:
            _owner.reset(token)
        return
    reporter = None
    token = None
    error: BaseException | None = None
    try:
        async with anyio.create_task_group() as group:
            reporter = _Reporter(ctx.report_progress, group)
            token = _owner.set(reporter)
            try:
                yield
            except BaseException as caught:
                # Preserve the original public exception/cancellation rather than
                # allowing TaskGroup to replace it with an exception group.
                error = caught
            finally:
                reporter.close()
                group.cancel_scope.cancel()
        if error is not None:
            raise error
    finally:
        if reporter is not None:
            reporter.close()
        if token is not None:
            _owner.reset(token)
        with _admission_lock:
            _active_reporters -= 1


def claim_command_progress(total: int) -> ProgressSink | None:
    """Claim once, after whole-batch admission and before its one execution send."""
    reporter = _owner.get()
    return reporter.claim(total) if reporter is not None else None
