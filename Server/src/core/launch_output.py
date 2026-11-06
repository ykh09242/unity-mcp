"""Bound Python output to an explicitly owned, shell-opened launch log."""

from __future__ import annotations

import io
import os
import stat
import sys
import threading

MAX_LAUNCH_LOG_BYTES = 8 * 1024 * 1024
_DISCARDED = b"[older launch output discarded; log limit is 8 MiB]\n"
_PAYLOAD_BYTES = MAX_LAUNCH_LOG_BYTES - len(_DISCARDED)


class _LaunchSink:
    def __init__(self, descriptor: int):
        self.descriptor = descriptor
        self.lock = threading.RLock()

    def write(self, data, *, discarded: bool = False) -> int:
        view = memoryview(data).cast("B")
        size = len(view)
        if size > _PAYLOAD_BYTES:
            view = view[-_PAYLOAD_BYTES:]
            discarded = True
        with self.lock:
            if discarded or os.fstat(self.descriptor).st_size + len(view) > MAX_LAUNCH_LOG_BYTES:
                # Rename-based rotation fails while cmd/uv retain Windows handles.
                # Truncate the verified inherited file, then reset its offset so a
                # non-O_APPEND descriptor cannot recreate a huge sparse file.
                os.ftruncate(self.descriptor, 0)
                os.lseek(self.descriptor, 0, os.SEEK_SET)
                self._write_all(memoryview(_DISCARDED))
            else:
                os.lseek(self.descriptor, 0, os.SEEK_END)
            self._write_all(view)
        return size

    def _write_all(self, view: memoryview) -> None:
        while view:
            written = os.write(self.descriptor, view)
            if written <= 0:
                raise OSError("Launch log write made no progress")
            view = view[written:]


class _BinaryLaunchOutput(io.RawIOBase):
    def __init__(self, sink: _LaunchSink):
        self.sink = sink

    def write(self, data) -> int:
        self._checkClosed()
        return self.sink.write(data)

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self.sink.descriptor


class _TextLaunchOutput(io.TextIOBase):
    def __init__(self, original, sink: _LaunchSink):
        # Retain the original stream: it owns the inherited descriptor's lifetime.
        self.original = original
        self.sink = sink
        self.buffer = _BinaryLaunchOutput(sink)

    @property
    def encoding(self):
        return self.original.encoding or "utf-8"

    @property
    def errors(self):
        return self.original.errors or "backslashreplace"

    def write(self, text: str) -> int:
        self._checkClosed()
        count = len(text)
        data = text.encode(self.encoding, self.errors)
        discarded = len(data) > _PAYLOAD_BYTES
        # Clip on character boundaries, including for a single oversized message.
        while len(data) > _PAYLOAD_BYTES:
            text = text[-max(1, len(text) * _PAYLOAD_BYTES // len(data) - 1) :]
            data = text.encode(self.encoding, self.errors)
        self.sink.write(data, discarded=discarded)
        return count

    def flush(self) -> None:
        self._checkClosed()
        # All writes are synchronous; the original buffer was flushed at install.

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self.original.fileno()


def install_managed_launch_output() -> bool:
    """Wrap only inherited regular-file streams matching the explicit launch path.

    Pipes, consoles, mismatched paths and ordinary stdio launches are unchanged.
    This covers Python output after bootstrap; uv's earlier installation output
    and native code writing directly to file descriptors remain shell-owned.
    """
    path = os.environ.get("UNITY_MCP_LAUNCH_LOG")
    if not path:
        return False
    try:
        expected = os.stat(path)
    except OSError:
        return False
    streams = []
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, _TextLaunchOutput):
            continue
        try:
            actual = os.fstat(stream.fileno())
            if stat.S_ISREG(actual.st_mode) and os.path.samestat(actual, expected):
                stream.flush()
                streams.append((name, stream))
        except (AttributeError, OSError, ValueError):
            continue
    if not streams:
        return False
    sink = _LaunchSink(streams[0][1].fileno())
    sink.write(b"")  # Also bound an oversized log inherited at startup.
    for name, stream in streams:
        setattr(sys, name, _TextLaunchOutput(stream, sink))
    return True
