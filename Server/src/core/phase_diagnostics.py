"""Opt-in, sampled scalar observations; no payloads, remote metrics or command ownership."""

from contextlib import contextmanager
from contextvars import Context, ContextVar
import asyncio
import atexit
import itertools
import json
import os
from pathlib import Path
import math
import queue
import threading
import time
import uuid

from core.config import config
from core.phase_log_files import PhaseLogFile

CAPABILITY = "phase_timing_v1"
STAGES = ("admission", "preflight", "send", "response", "return")
RECORD_FIELDS = {
    "event",
    "correlation",
    "sample_every",
    "native_requested",
    "outcome",
    "total_ms",
    *(s + "_ms" for s in STAGES),
}
clock = time.monotonic
_admissions = itertools.count(1)
_owner = ContextVar("phase_diagnostic_owner", default=None)
_log_path = None
_LOG_BYTES = 512 * 1024


def configure_file(log_dir):
    """Select an existing local log directory at startup; create files only on the worker."""
    global _log_path
    _log_path = (
        Path(log_dir) / f"phase-timing-{os.getpid()}-{uuid.uuid4().hex}.log"
        if config.phase_diagnostics
        else None
    )


def new_token():
    return uuid.uuid4().hex


def sample():
    return next(_admissions) % 64 == 1


class BufferedSink:
    """Bounded optional records; counter locks never cover disk or external callbacks."""

    def __init__(self, emit=None):
        self.queue = queue.Queue(maxsize=256)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.worker = None
        self.emit = emit
        self.file = None
        self.retention = None
        self.count_lock = threading.Lock()
        self.counts = dict.fromkeys(
            (
                "offered",
                "written",
                "write_failed",
                "drop_queue",
                "drop_startup",
                "drop_closed",
                "drop_shutdown",
                "checkpoint_write_failed",
            ),
            0,
        )
        self.session = uuid.uuid4().hex
        self.started = time.monotonic()
        self.checkpoint = 0
        self.next_checkpoint = 0

    @staticmethod
    def _encode(row):
        return ("phase_timing " + json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")

    def _log(self, row, sequence=None):
        # Only this daemon owns the file lease and performs maintenance. Ordinary
        # logging handlers and command execution never acquire these file locks.
        path = _log_path
        if path is None:
            return False
        if self.file is None:
            self.file = PhaseLogFile(path, _LOG_BYTES)
        if sequence is not None:
            row = {**row, "capture_session": self.session, "capture_sequence": sequence}
        self.file.write(self._encode(row))
        return True

    def _maintain(self):
        if self.file is not None:
            try:
                result = self.file.maintain()
                if result is not None:
                    self.retention = result
            except OSError:
                self.file.maintenance.close()

    def retention_snapshot(self):
        """Last worker scan, possibly partial; reading it performs no file I/O."""
        return dict(self.retention) if self.retention is not None else None

    def capture_snapshot(self):
        """Counters since this sink was created, without waiting for the writer."""
        with self.count_lock:
            values = dict(self.counts)
            dropped = sum(
                values[name]
                for name in (
                    "drop_queue",
                    "drop_startup",
                    "drop_closed",
                    "drop_shutdown",
                )
            )
            pending = values["offered"] - values["written"] - values["write_failed"] - dropped
            return {
                "event": "phase_capture_coverage",
                "schema_version": 1,
                "session": self.session,
                "checkpoint": self.checkpoint,
                "elapsed_ms": (time.monotonic() - self.started) * 1000,
                "sample_every": 64,
                **values,
                "dropped": dropped,
                "pending": pending,
                "closed": self.stop.is_set() and pending == 0,
            }

    def _count(self, name):
        with self.count_lock:
            self.counts[name] += 1

    def _publish_coverage(self, final=False):
        # Checkpoints are optional too. A later successful checkpoint exposes
        # earlier checkpoint failures; an unavailable final checkpoint is unknown.
        if self.file is None or (not final and time.monotonic() < self.next_checkpoint):
            return
        self.next_checkpoint = time.monotonic() + 30
        with self.count_lock:
            self.checkpoint += 1
        row = self.capture_snapshot()
        row["retention"] = self.retention_snapshot()
        try:
            self.file.write(self._encode(row))
        except Exception:
            self._count("checkpoint_write_failed")

    def offer(self, row):
        with self.count_lock:
            self.counts["offered"] += 1
            if self.stop.is_set():
                self.counts["drop_closed"] += 1
                return
        if self.worker is None:
            if not self.lock.acquire(blocking=False):
                self._count("drop_startup")
                return
            try:
                if self.worker is None:
                    worker = threading.Thread(
                        target=lambda: Context().run(self._run),
                        daemon=True,
                        name="unity-phase-diagnostics",
                    )
                    try:
                        worker.start()
                    except Exception:
                        self._count("drop_startup")
                        return
                    self.worker = worker
            finally:
                self.lock.release()
        # Share only a short counter/queue critical section with close. This
        # prevents an offer from enqueueing after the worker drained on shutdown.
        with self.count_lock:
            if self.stop.is_set():
                self.counts["drop_closed"] += 1
                return
            try:
                self.queue.put_nowait(row)
            except queue.Full:
                self.counts["drop_queue"] += 1

    def _run(self):
        try:
            while not self.stop.is_set():
                try:
                    row = self.queue.get(timeout=0.2)
                except queue.Empty:
                    self._maintain()
                    self._publish_coverage()
                    continue
                # Sequence settled write attempts, not offers: queue loss and
                # concurrent producers must not move later writes into an older
                # checkpoint's window.
                with self.count_lock:
                    sequence = self.counts["written"] + self.counts["write_failed"] + 1
                try:
                    result = self.emit(row) if self.emit is not None else self._log(row, sequence)
                    self._count("write_failed" if result is False else "written")
                except Exception:
                    self._count("write_failed")
                finally:
                    del row
                    self.queue.task_done()
                self._maintain()
                self._publish_coverage()
        finally:
            # Release queued row ownership and account for abandoned samples.
            # Offers after stop are rejected under the same counter lock.
            with self.count_lock:
                while True:
                    try:
                        abandoned = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    self.counts["drop_shutdown"] += 1
                    self.queue.task_done()
                    del abandoned
            self._publish_coverage(final=True)
            if self.file is not None:
                try:
                    self.file.close()
                except OSError:
                    pass

    def close(self):
        with self.count_lock:
            self.stop.set()
        # Optional logging never prolongs command/server shutdown.
        if self.worker is not None:
            self.worker.join(timeout=0.2)


_sink = BufferedSink()
atexit.register(_sink.close)
offer = _sink.offer


class Trace:
    def __init__(self, command):
        self.command = command
        self.correlation = new_token()
        self.started = self.last = clock()
        self.stage = "admission"
        self.elapsed = {}
        self.closed = False
        self.native_requested = False

    def step(self, stage):
        if self.closed or stage not in STAGES:
            return
        now = clock()
        duration = (now - self.last) * 1000
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("Invalid diagnostic clock")
        self.elapsed[self.stage + "_ms"] = self.elapsed.get(self.stage + "_ms", 0) + duration
        self.last, self.stage = now, stage

    def finish(self, outcome):
        try:
            self.step(self.stage)
            self.closed = True
            offer(
                {
                    "event": "server_phase_timing",
                    "correlation": self.correlation,
                    "sample_every": 64,
                    "native_requested": self.native_requested,
                    "outcome": outcome,
                    "total_ms": (self.last - self.started) * 1000,
                    **self.elapsed,
                }
            )
        finally:
            self.closed = True
            self.command = None
            self.correlation = None


def current():
    value = _owner.get()
    return value if value is not None and not value.closed else None


@contextmanager
def invocation(command):
    # No clock/token/owner allocation when disabled or unsampled.
    trace = token = None
    try:
        if config.phase_diagnostics and sample():
            trace = Trace(command)
            token = _owner.set(trace)
    except Exception:
        trace = None
    if token is None and _owner.get() is not None:
        token = _owner.set(None)
    outcome = "returned"
    try:
        yield
    except asyncio.CancelledError:
        outcome = "canceled"
        raise
    except BaseException:
        outcome = "raised"
        raise
    finally:
        if token is not None:
            _owner.reset(token)
        if trace is not None:
            try:
                trace.finish(outcome)
            except Exception:
                pass


def step(command, stage):
    try:
        trace = current()
        if trace is not None and trace.command == command:
            trace.step(stage)
    except Exception:
        pass


def request(command, negotiated):
    try:
        trace = current()
        if (
            trace is not None
            and trace.command == command
            and negotiated
            and not trace.native_requested
        ):
            trace.native_requested = True
            return {"correlation": trace.correlation}
    except Exception:
        pass
    return None
