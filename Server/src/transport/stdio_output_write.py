"""Interrupt kernel pipe writes while preserving the actual worker's ownership."""

from __future__ import annotations

import os
import stat
import threading

import anyio

from transport.stdio_input_read import _interrupt_windows_read as _interrupt_windows_io


class StdioOutputWrite:
    """Raw writes for an SDK-claimed pipe; never close or recycle its descriptor."""

    def __init__(self, source):
        self.source = source
        self.fd = source.fileno()
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._thread_id = None
        # One serial consumer owns this pipe. Unrelated AnyIO jobs must never
        # prevent its writer from starting and acknowledging a shutdown request.
        self.write_slot = anyio.CapacityLimiter(1)

    @classmethod
    def for_pipe(cls, source):
        try:
            if os.name in {"posix", "nt"} and stat.S_ISFIFO(os.fstat(source.fileno()).st_mode):
                return cls(source)
        except (AttributeError, OSError):
            pass
        return source

    def run(self, operation):
        with self._lock:
            if self._stopped.is_set():
                raise InterruptedError("MCP stdio output stopped")
            self._thread_id = threading.get_native_id()
        blocking = None
        try:
            if os.name == "posix":
                blocking = os.get_blocking(self.fd)
                os.set_blocking(self.fd, False)
            operation()
        finally:
            try:
                if blocking is not None:
                    os.set_blocking(self.fd, blocking)
            finally:
                with self._lock:
                    self._thread_id = None

    def write(self, data):
        while not self._stopped.is_set():
            try:
                # Bound each kernel call and return its exact successful offset.
                # Windows uses synchronous pipes on every supported Python,
                # including 3.11 (where os.set_blocking cannot handle pipes).
                return os.write(self.fd, data[:65_536])
            except BlockingIOError:
                self._stopped.wait(0.01)
            except InterruptedError:
                if not self._stopped.is_set():
                    continue
                raise
        raise InterruptedError("MCP stdio output stopped")

    def flush(self):
        # os.write has no Python userspace buffer to flush.
        pass

    def interrupt(self):
        with self._lock:
            self._stopped.set()
            if os.name == "nt" and self._thread_id is not None:
                # Cancellation only requests a wakeup. The caller must wait for
                # run() to exit before releasing payloads/restoring the SDK FD.
                _interrupt_windows_io(self._thread_id)
