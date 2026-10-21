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
    """A bounded local logging queue. Disk and logging handlers run off the command thread."""

    def __init__(self, emit=None):
        self.queue = queue.Queue(maxsize=256)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.worker = None
        self.emit = emit or self._log
        self.file = None
        self.retention = None

    def _log(self, row):
        # Only this daemon owns the file lease and performs maintenance. Ordinary
        # logging handlers and command execution never acquire these file locks.
        path = _log_path
        if path is None:
            return
        if self.file is None:
            self.file = PhaseLogFile(path, _LOG_BYTES)
        encoded = ("phase_timing " + json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
        self.file.write(encoded)

    def _maintain(self):
        if self.file is not None:
            try:
                result = self.file.maintain()
                if result is not None:
                    self.retention = result
            except OSError:
                pass

    def retention_snapshot(self):
        """Last worker scan, possibly partial; reading it performs no file I/O."""
        return dict(self.retention) if self.retention is not None else None

    def offer(self, row):
        if self.stop.is_set():
            return
        if self.worker is None:
            if not self.lock.acquire(blocking=False):
                return
            try:
                if self.worker is None:
                    worker = threading.Thread(
                        target=lambda: Context().run(self._run),
                        daemon=True,
                        name="unity-phase-diagnostics",
                    )
                    worker.start()
                    self.worker = worker
            finally:
                self.lock.release()
        try:
            self.queue.put_nowait(row)
        except queue.Full:
            pass

    def _run(self):
        try:
            while not self.stop.is_set():
                try:
                    row = self.queue.get(timeout=0.2)
                except queue.Empty:
                    self._maintain()
                    continue
                try:
                    self.emit(row)
                except Exception:
                    pass
                finally:
                    del row
                    self.queue.task_done()
                self._maintain()
        finally:
            if self.file is not None:
                try:
                    self.file.close()
                except OSError:
                    pass

    def close(self):
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
