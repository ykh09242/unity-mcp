"""Wake a synchronous stdin reader without closing a descriptor under it."""

from __future__ import annotations

import os
import selectors
import sys
import threading


class StdioInputRead:
    def __init__(self, source):
        self.source = source
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._thread_id = None
        self._in_place = source is getattr(sys.stdin, "buffer", None)
        self._selector = None
        if os.name == "posix":
            try:
                fd = source.fileno()
            except (AttributeError, OSError):
                return  # In-memory streams have no kernel read to interrupt.
            self._selector = getattr(selectors, "PollSelector", selectors.SelectSelector)()
            self._selector.register(fd, selectors.EVENT_READ)

    def run(self, read_line):
        with self._lock:
            if self._stopped.is_set():
                return
            self._thread_id = threading.get_native_id()
        try:
            read_line()
        except OSError:
            if not self._stopped.is_set():
                raise
        finally:
            with self._lock:
                self._thread_id = None

    def read1(self, size):
        if self._selector is not None:
            # The SDK can serve sys.stdin.buffer in place. A previous buffered
            # read can leave bytes here even when the kernel descriptor is idle.
            if self._in_place and not self._selector.select(0):
                fd = self.source.fileno()
                blocking = os.get_blocking(fd)
                if blocking:
                    os.set_blocking(fd, False)
                try:
                    chunk = self.source.read1(size)
                finally:
                    if blocking:
                        os.set_blocking(fd, True)
                if chunk:
                    return chunk
            # poll also supports regular files, terminals and /dev/null, and
            # wakes on either input or EOF. Do not leave an idle read in libc.
            while not self._stopped.is_set() and not self._selector.select(0.05):
                pass
        if self._stopped.is_set():
            return b""
        return self.source.read1(size)

    def interrupt(self):
        with self._lock:
            self._stopped.set()
            if os.name == "nt" and self._thread_id is not None:
                # Hold the registration until cancellation has been issued.
                # The pool cannot reuse the thread for unrelated work yet.
                _interrupt_windows_read(self._thread_id)


def _interrupt_windows_read(thread_id: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_thread = kernel.OpenThread
    open_thread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_thread.restype = wintypes.HANDLE
    cancel = kernel.CancelSynchronousIo
    cancel.argtypes = [wintypes.HANDLE]
    cancel.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = open_thread(0x0001, False, thread_id)  # THREAD_TERMINATE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not cancel(handle):
            error = ctypes.get_last_error()
            if error != 1168:  # ERROR_NOT_FOUND: no kernel read pending yet.
                raise ctypes.WinError(error)
    finally:
        close(handle)
