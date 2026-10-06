"""Bounded, connection-owned assembly of negotiated large_result_v1 messages.

Call synchronously under the hub lock. Reservation callbacks share the hub's
retained-result accounting; completed bytes remain reserved until discard().
No JSON is parsed here, so the caller can decode the normal result exactly once.
"""
from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Final
from uuid import UUID

CAPABILITY: Final = "large_result_v1"
THRESHOLD_BYTES: Final = 256 * 1024
MAX_RESULT_BYTES: Final = 32 * 1024 * 1024
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
    offset: int = 0
    received_chunks: int = 0
    completed: bool = False


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

    def __init__(self, pending: Callable[[str, str], bool],
                 reserve: Callable[[str, str, int], bool],
                 release: Callable[[str, str], None], *,
                 timeout_seconds: float = 30.0,
                 max_total_bytes: int = 64 * 1024 * 1024,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not 0 < timeout_seconds <= 300 or not 0 < max_total_bytes <= 256 * 1024 * 1024:
            raise LargeResultProtocolError("invalid_assembler_limits")
        self._pending = pending
        self._reserve = reserve
        self._release = release
        self._timeout = timeout_seconds
        self._max_total = max_total_bytes
        self._clock = clock
        self._transfers: dict[tuple[str, str], _Transfer] = {}
        self._retained_bytes = 0

    @property
    def retained_bytes(self) -> int:
        return self._retained_bytes

    def begin(self, owner: str, command_id: str, total_bytes: int, chunk_count: int) -> bool:
        """Reserve a pending transfer; ignore valid late/unowned results without allocation."""
        if not _canonical_id(command_id):
            raise LargeResultProtocolError("invalid_result_id")
        if (type(total_bytes) is not int or type(chunk_count) is not int
                or not THRESHOLD_BYTES <= total_bytes <= MAX_RESULT_BYTES
                or chunk_count != (total_bytes + CHUNK_PAYLOAD_BYTES - 1) // CHUNK_PAYLOAD_BYTES):
            raise LargeResultProtocolError("invalid_result_size", command_id)
        key = (owner, command_id)
        if not self._pending(owner, command_id):
            self.discard(owner, command_id)
            return False
        if key in self._transfers:
            self.discard(owner, command_id)
            raise LargeResultProtocolError("duplicate_result_start", command_id)
        if (self._retained_bytes + total_bytes > self._max_total
                or not self._reserve(owner, command_id, total_bytes)):
            raise LargeResultProtocolError("result_capacity", command_id)
        try:
            transfer = _Transfer(bytearray(total_bytes), chunk_count, self._clock() + self._timeout)
        except MemoryError:
            self._release(owner, command_id)
            raise
        self._transfers[key] = transfer
        self._retained_bytes += total_bytes
        return True

    def feed(self, owner: str, frame: bytes) -> CompletedLargeResult | None:
        """Consume exactly the next expected complete binary WebSocket message."""
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
        elif transfer.completed:
            reason = "result_replay"
        elif int.from_bytes(frame[40:44], "big") != transfer.offset:
            reason = "invalid_result_offset"
        elif len(frame) - HEADER_BYTES != min(CHUNK_PAYLOAD_BYTES, len(transfer.payload) - transfer.offset):
            reason = "invalid_result_chunk_size"
        if reason is not None:
            self.discard(owner, command_id)
            raise LargeResultProtocolError(reason, command_id)
        length = len(frame) - HEADER_BYTES
        transfer.payload[transfer.offset:transfer.offset + length] = memoryview(frame)[HEADER_BYTES:]
        transfer.offset += length
        transfer.received_chunks += 1
        if transfer.offset == len(transfer.payload):
            if transfer.received_chunks != transfer.chunk_count:
                self.discard(owner, command_id)
                raise LargeResultProtocolError("invalid_result_chunk_count", command_id)
            transfer.completed = True
            return CompletedLargeResult(command_id, transfer.payload)
        return None

    def discard(self, owner: str, command_id: str) -> None:
        """Release a partial/completed transfer after cancellation or JSON consumption."""
        transfer = self._transfers.pop((owner, command_id), None)
        if transfer is not None:
            self._retained_bytes -= len(transfer.payload)
            self._release(owner, command_id)

    def discard_owner(self, owner: str) -> None:
        """Invalidate every transfer when its socket disconnects or is replaced."""
        for transfer_owner, command_id in tuple(self._transfers):
            if transfer_owner == owner:
                self.discard(owner, command_id)

    def expire(self) -> tuple[tuple[str, str], ...]:
        """Release timed-out transfers; caller may complete their pending errors."""
        now = self._clock()
        expired = tuple(key for key, value in self._transfers.items() if now >= value.deadline)
        for owner, command_id in expired:
            self.discard(owner, command_id)
        return expired
