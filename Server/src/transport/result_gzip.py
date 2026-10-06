"""Single-member bounded gzip inflation into the already reserved result buffer."""
from typing import Final, Protocol
import zlib

GZIP_WORKING_BYTES: Final = 512 * 1024
OUTPUT_CHUNK_BYTES: Final = 64 * 1024


class GzipResultError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _Inflater(Protocol):
    unconsumed_tail: bytes
    unused_data: bytes
    eof: bool

    def decompress(self, data: bytes | memoryview, max_length: int = 0) -> bytes: ...


class GzipResultDecoder:
    """Mutable cursor/inflater; all output is bounded by the advertised allocation."""

    def __init__(self, destination: bytearray) -> None:
        self._destination = destination
        self._offset = 0
        self._inflater: _Inflater = zlib.decompressobj(31)

    def feed(self, data: memoryview, *, final: bool) -> None:
        incoming: bytes | memoryview = data
        try:
            while True:
                remaining = len(self._destination) - self._offset
                limit = min(OUTPUT_CHUNK_BYTES, remaining + 1)
                output = self._inflater.decompress(incoming, limit)
                if len(output) > remaining:
                    raise GzipResultError("decoded_result_size_exceeded")
                memoryview(self._destination)[self._offset:self._offset + len(output)] = output
                self._offset += len(output)
                if self._inflater.unused_data:
                    raise GzipResultError("gzip_trailing_data")
                incoming = self._inflater.unconsumed_tail
                if not incoming and len(output) < limit:
                    break
        except zlib.error as exc:
            raise GzipResultError("invalid_gzip_stream") from exc
        if self._inflater.eof and not final:
            raise GzipResultError("gzip_ended_before_wire_size")
        if final and (not self._inflater.eof or self._offset != len(self._destination)):
            raise GzipResultError("gzip_incomplete_or_size_mismatch")
