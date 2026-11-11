"""Bounded, connection-owned assembly of negotiated large_result_v1 messages.

Admission and completion run under the hub lock; claimed chunk processing may
run in a worker. Reservations survive discard until that physical worker exits.
Completed bytes stay reserved until discard; the caller decodes JSON once.
"""

from collections.abc import Callable
from dataclasses import dataclass
import math
import time
from typing import Final
from uuid import UUID
from transport.result_gzip import GZIP_WORKING_BYTES, GzipResultDecoder, GzipResultError

CAPABILITY: Final = "large_result_v1"
COMPRESSION_CAPABILITY: Final = "large_result_gzip_v1"
COMPRESSION_THRESHOLD_BYTES: Final = 1024 * 1024
THRESHOLD_BYTES: Final = 256 * 1024
MAX_RESULT_BYTES: Final = 64 * 1024 * 1024
MAX_FRAME_BYTES: Final = 64 * 1024
HEADER_BYTES: Final = 44
CHUNK_PAYLOAD_BYTES: Final = MAX_FRAME_BYTES - HEADER_BYTES
MAGIC: Final = b"ULR1"


class LargeResultProtocolError(ValueError):
    """A malformed or unowned transfer; reason is safe to log without payloads."""

    def __init__(self, reason: str, command_id: str | None = None) -> None:
        self.reason = reason
        self.command_id = command_id
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class CompletedLargeResult:
    command_id: str
    payload: bytearray


@dataclass(slots=True)
class _Transfer:
    """Mutable cursor and buffer are owned by one in-progress transfer."""

    payload: bytearray
    chunk_count: int
    deadline: float
    wire_bytes: int
    decoder: GzipResultDecoder | None = None
    offset: int = 0
    received_chunks: int = 0
    completed: bool = False
    in_flight: bool = False
    discarded: bool = False


@dataclass(frozen=True, slots=True)
class ResultChunk:
    """One claimed chunk; only process() may run outside the owner loop."""

    owner: str
    command_id: str
    transfer: _Transfer
    frame: bytes

    @property
    def compressed(self) -> bool:
        return self.transfer.decoder is not None

    def process(self) -> bool:
        transfer = self.transfer
        source = memoryview(self.frame)[HEADER_BYTES:]
        if transfer.decoder is None:
            memoryview(transfer.payload)[transfer.offset : transfer.offset + len(source)] = source
        else:
            try:
                transfer.decoder.feed(
                    source, final=transfer.offset + len(source) == transfer.wire_bytes
                )
            except GzipResultError:
                # Do not carry a buffer-owning exception traceback out of a worker.
                return False
        return True


def _canonical_id(command_id: str) -> bool:
    try:
        return str(UUID(command_id)) == command_id
    except (ValueError, AttributeError):
        return False


class LargeResultAssembler:
    """Accumulates bytes with finite local and caller-owned aggregate reservations.

    owner is a unique connection generation, never a reusable session ID.
    pending(owner, id) must reject completed/cancelled commands and wrong sockets.
    reserve/release must coordinate with the normal response retention budget.
    """

    def __init__(
        self,
        pending: Callable[[str, str], bool],
        reserve: Callable[[str, str, int], bool],
        release: Callable[[str, str], None],
        *,
        timeout_seconds: float = 300.0,
        max_total_bytes: int = 128 * 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
        reserve_compressed: Callable[[str, str, int, int], bool] | None = None,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 86400
            or not 0 < max_total_bytes <= 256 * 1024 * 1024
        ):
            raise LargeResultProtocolError("invalid_assembler_limits")
        self._pending = pending
        self._reserve = reserve
        self._reserve_compressed = reserve_compressed
        self._release = release
        self._timeout = timeout_seconds
        self._max_total = max_total_bytes
        self._clock = clock
        self._transfers: dict[tuple[str, str], _Transfer] = {}
        self._retained_bytes = 0

    @property
    def retained_bytes(self) -> int:
        return self._retained_bytes

    def begin(
        self,
        owner: str,
        command_id: str,
        total_bytes: int,
        chunk_count: int,
        *,
        encoding: str = "identity",
        decoded_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Reserve within the caller's remaining elapsed budget, never a peer-supplied value.

        Standalone callers retain the configured finite default. An enclosing
        command may include response grace beyond the public one-day work budget.
        Progress does not extend either deadline. Ignore valid unowned results.
        """
        if not _canonical_id(command_id):
            raise LargeResultProtocolError("invalid_result_id")
        if encoding not in {"identity", "gzip"}:
            raise LargeResultProtocolError("unsupported_result_encoding", command_id)
        compressed = encoding == "gzip"
        if (
            type(total_bytes) is not int
            or type(chunk_count) is not int
            or not (1 if compressed else THRESHOLD_BYTES) <= total_bytes <= MAX_RESULT_BYTES
            or chunk_count != (total_bytes + CHUNK_PAYLOAD_BYTES - 1) // CHUNK_PAYLOAD_BYTES
        ):
            raise LargeResultProtocolError("invalid_result_size", command_id)
        if compressed:
            if (
                type(decoded_bytes) is not int
                or not COMPRESSION_THRESHOLD_BYTES <= decoded_bytes <= MAX_RESULT_BYTES
            ):
                raise LargeResultProtocolError("invalid_decoded_result_size", command_id)
            allocation_bytes = decoded_bytes
        else:
            if decoded_bytes is not None:
                raise LargeResultProtocolError("unexpected_decoded_result_size", command_id)
            allocation_bytes = total_bytes
        key = (owner, command_id)
        if not self._pending(owner, command_id):
            self.discard(owner, command_id)
            return False
        if key in self._transfers:
            self.discard(owner, command_id)
            raise LargeResultProtocolError("duplicate_result_start", command_id)
        remaining = self._timeout if timeout_seconds is None else timeout_seconds
        if type(remaining) not in (int, float):
            raise LargeResultProtocolError("invalid_result_timeout", command_id)
        try:
            deadline = self._clock() + remaining
            valid = math.isfinite(remaining) and math.isfinite(deadline)
        except OverflowError:
            valid = False
        if not valid:
            raise LargeResultProtocolError("invalid_result_timeout", command_id)
        if remaining <= 0:
            raise LargeResultProtocolError("result_transfer_timeout", command_id)
        if self._retained_bytes + allocation_bytes > self._max_total:
            raise LargeResultProtocolError("result_capacity", command_id)
        if compressed:
            if self._reserve_compressed is None or not self._reserve_compressed(
                owner, command_id, allocation_bytes, GZIP_WORKING_BYTES
            ):
                raise LargeResultProtocolError("result_capacity", command_id)
        elif not self._reserve(owner, command_id, allocation_bytes):
            raise LargeResultProtocolError("result_capacity", command_id)
        try:
            transfer = _Transfer(bytearray(allocation_bytes), chunk_count, deadline, total_bytes)
            if compressed:
                transfer.decoder = GzipResultDecoder(transfer.payload)
        except MemoryError:
            self._release(owner, command_id)
            raise
        self._transfers[key] = transfer
        self._retained_bytes += allocation_bytes
        return True

    def feed(self, owner: str, frame: bytes) -> CompletedLargeResult | None:
        """Consume exactly the next expected complete binary WebSocket message."""
        chunk = self.prepare_chunk(owner, frame)
        if chunk is None:
            return None
        try:
            return self.complete_chunk(chunk, chunk.process())
        finally:
            self.release_chunk(chunk)

    def prepare_chunk(self, owner: str, frame: bytes) -> ResultChunk | None:
        """Validate and pin the transfer before a bounded worker may touch it."""
        if not HEADER_BYTES < len(frame) <= MAX_FRAME_BYTES or frame[:4] != MAGIC:
            raise LargeResultProtocolError("invalid_result_frame")
        try:
            command_id = frame[4:40].decode("ascii")
        except UnicodeDecodeError as exc:
            raise LargeResultProtocolError("invalid_result_id") from exc
        if not _canonical_id(command_id):
            raise LargeResultProtocolError("invalid_result_id")
        if not self._pending(owner, command_id):
            self.discard(owner, command_id)
            return None
        transfer = self._transfers.get((owner, command_id))
        if transfer is None:
            raise LargeResultProtocolError("unsolicited_result_chunk", command_id)
        reason = None
        if self._clock() >= transfer.deadline:
            reason = "result_transfer_timeout"
        elif transfer.completed or transfer.in_flight or transfer.discarded:
            reason = "result_replay"
        elif int.from_bytes(frame[40:44], "big") != transfer.offset:
            reason = "invalid_result_offset"
        elif len(frame) - HEADER_BYTES != min(
            CHUNK_PAYLOAD_BYTES, transfer.wire_bytes - transfer.offset
        ):
            reason = "invalid_result_chunk_size"
        if reason is not None:
            self.discard(owner, command_id)
            raise LargeResultProtocolError(reason, command_id)
        transfer.in_flight = True
        return ResultChunk(owner, command_id, transfer, frame)

    def complete_chunk(self, chunk: ResultChunk, valid: bool) -> CompletedLargeResult | None:
        """Commit only the still-current transfer after physical CPU work exits."""
        owner, command_id, transfer = chunk.owner, chunk.command_id, chunk.transfer
        if self._transfers.get((owner, command_id)) is not transfer or not transfer.in_flight:
            return None
        if transfer.discarded or not self._pending(owner, command_id):
            self.discard(owner, command_id)
            return None
        if self._clock() >= transfer.deadline:
            self.discard(owner, command_id)
            raise LargeResultProtocolError("result_transfer_timeout", command_id)
        if not valid:
            self.discard(owner, command_id)
            raise LargeResultProtocolError("invalid_result_compression", command_id)
        transfer.offset += len(chunk.frame) - HEADER_BYTES
        transfer.received_chunks += 1
        if transfer.offset == transfer.wire_bytes:
            if transfer.received_chunks != transfer.chunk_count:
                self.discard(owner, command_id)
                raise LargeResultProtocolError("invalid_result_chunk_count", command_id)
            transfer.completed = True
            return CompletedLargeResult(command_id, transfer.payload)
        return None

    def release_chunk(self, chunk: ResultChunk) -> None:
        """Release the pin only after the worker (including cancelled work) settles."""
        transfer = chunk.transfer
        transfer.in_flight = False
        if transfer.discarded:
            self.discard(chunk.owner, chunk.command_id)

    def discard(self, owner: str, command_id: str) -> None:
        """Release a partial/completed transfer after cancellation or JSON consumption."""
        transfer = self._transfers.get((owner, command_id))
        if transfer is not None:
            transfer.discarded = True
            if transfer.in_flight:
                return
            self._transfers.pop((owner, command_id))
            self._retained_bytes -= len(transfer.payload)
            self._release(owner, command_id)

    def discard_owner(self, owner: str) -> None:
        """Invalidate every transfer when its socket disconnects or is replaced."""
        for transfer_owner, command_id in tuple(self._transfers):
            if transfer_owner == owner:
                self.discard(owner, command_id)

    def discard_all(self) -> None:
        """Retire this assembler without freeing any still-running chunk buffer."""
        for owner, command_id in tuple(self._transfers):
            self.discard(owner, command_id)

    def expire(self) -> tuple[tuple[str, str], ...]:
        """Release timed-out transfers; caller may complete their pending errors."""
        now = self._clock()
        expired = tuple(key for key, value in self._transfers.items() if now >= value.deadline)
        for owner, command_id in expired:
            self.discard(owner, command_id)
        return expired
