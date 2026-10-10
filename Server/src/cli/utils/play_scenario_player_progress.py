"""Bounded non-authoritative receipts from the owned Player main loop."""

from collections import deque
from contextlib import ExitStack, contextmanager
import ctypes
import json
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Annotated, BinaryIO, Final, Iterator

from models.play_scenarios import JobId, PlayScenarioCommand
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

PROGRESS_LIMIT: Final = 16384


@contextmanager
def open_progress_snapshot(path: Path) -> Iterator[BinaryIO]:
    """Own one read handle while allowing the native writer to atomically replace it."""
    if os.name != "nt":
        with path.open("rb") as stream:
            yield stream
        return
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ; FILE_SHARE_READ | WRITE | DELETE; OPEN_EXISTING; NORMAL.
    handle = create(str(path), 0x80000000, 7, None, 3, 128, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = None
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    finally:
        if descriptor is None:
            close(handle)
    try:
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream


def read_progress_bytes(path: Path) -> bytes:
    """Read at most 16 KiB, using share-delete ownership on Windows."""
    started = time.monotonic()
    with ExitStack() as ownership:
        for attempt in range(3):
            try:
                stream = ownership.enter_context(open_progress_snapshot(path))
                break
            except OSError as exc:
                if (
                    os.name != "nt"
                    or getattr(exc, "winerror", None) not in (2, 32, 33)
                    or attempt == 2
                ):
                    raise
                remaining = 0.05 - (time.monotonic() - started)
                if remaining < 0.01:
                    raise
                time.sleep(0.01)
                if time.monotonic() - started >= 0.05:
                    raise
        encoded = stream.read(PROGRESS_LIMIT + 1)
    if len(encoded) > PROGRESS_LIMIT:
        raise ValueError("progress exceeds its byte bound")
    return encoded


class PlayerProgress(BaseModel):
    """A scalar receipt proves only the observed runner updates, never a terminal outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    job_id: JobId
    definition_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
    build_id: JobId | None
    payload_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")] | None
    build_source_revision: Annotated[str, Field(strict=True, max_length=128)] | None
    process_id: Annotated[int, Field(strict=True, ge=1)]
    sequence: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    main_loop_sequence: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    heartbeat_unix_ms: Annotated[int, Field(strict=True, ge=0)]
    elapsed_ms: Annotated[int, Field(strict=True, ge=0)]
    phase: Annotated[str, Field(strict=True, max_length=64)]
    iteration: Annotated[int, Field(strict=True, ge=0, le=10)]
    stage: Annotated[str, Field(strict=True, max_length=64)]
    step_index: Annotated[int, Field(strict=True, ge=-1, le=63)]
    status: Annotated[str, Field(strict=True, max_length=64)]
    last_observation: Annotated[str, Field(strict=True, max_length=512)] | None = None

    @field_validator("build_source_revision")
    @classmethod
    def bounded_revision(cls, value: str | None) -> str | None:
        return PlayScenarioCommand.check_revision(value)


@dataclass(frozen=True, slots=True)
class ProgressIdentity:
    """The launcher-owned identity to which every progress receipt must belong."""

    job_id: str
    definition_hash: str
    build_id: str | None
    payload_hash: str | None
    build_source_revision: str | None
    process_id: int


class ProgressObserver:
    """Mutable bounded observer retains one scalar receipt and one first integrity error."""

    def __init__(self, identity: ProgressIdentity, stale_seconds: float = 30) -> None:
        self.identity = identity
        self.stale_seconds = stale_seconds
        self.last: PlayerProgress | None = None
        self.first_error: str | None = None
        self.last_advance = time.monotonic()
        self.stale_observed = False
        self.heartbeat_observed = False
        self.unavailable_observations = 0
        self.observations: deque[dict[str, JsonValue]] = deque(maxlen=128)

    def observe(self, path: Path) -> None:
        """Read an atomic bounded snapshot without counting provider/object evaluations."""
        try:
            if path.is_symlink():
                raise ValueError("progress is linked")
            encoded = read_progress_bytes(path)
            receipt = PlayerProgress.model_validate_json(encoded)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) or getattr(exc, "winerror", None) in (32, 33):
                self.unavailable_observations = min(2**31 - 1, self.unavailable_observations + 1)
                self.stale_observed |= time.monotonic() - self.last_advance >= self.stale_seconds
                return
            self.first_error = self.first_error or "player_progress_invalid"
            return
        except (ValueError, ValidationError):
            self.first_error = self.first_error or "player_progress_invalid"
            return
        observed = ProgressIdentity(
            receipt.job_id,
            receipt.definition_hash,
            receipt.build_id,
            receipt.payload_hash,
            receipt.build_source_revision,
            receipt.process_id,
        )
        if observed != self.identity:
            self.first_error = self.first_error or "player_progress_identity_mismatch"
            return
        if self.last is not None and (
            receipt.sequence < self.last.sequence
            or receipt.main_loop_sequence < self.last.main_loop_sequence
            or receipt.elapsed_ms < self.last.elapsed_ms
        ):
            self.first_error = self.first_error or "player_progress_sequence_regressed"
            return
        if self.last is None or receipt.main_loop_sequence > self.last.main_loop_sequence:
            self.last_advance = time.monotonic()
        self.heartbeat_observed |= receipt.main_loop_sequence > 0 and receipt.heartbeat_unix_ms > 0
        self.stale_observed |= time.monotonic() - self.last_advance >= self.stale_seconds
        if self.last is None or receipt.sequence != self.last.sequence:
            self.observations.append(
                {
                    key: getattr(receipt, key)
                    for key in (
                        "sequence",
                        "main_loop_sequence",
                        "heartbeat_unix_ms",
                        "elapsed_ms",
                        "phase",
                        "iteration",
                        "stage",
                        "step_index",
                        "status",
                    )
                }
            )
        while len(json.dumps(list(self.observations), ensure_ascii=True).encode("utf-8")) > 49152:
            self.observations.popleft()
        self.last = receipt

    def diagnostics(self) -> dict[str, JsonValue]:
        """Expose bounded diagnostics explicitly separate from the native run report."""
        return {
            "progress_unavailable_observations": self.unavailable_observations,
            "heartbeat_observed": self.heartbeat_observed,
            "heartbeat_stale_observed": self.stale_observed,
            "progress_error": self.first_error,
            "last_progress": self.last.model_dump(mode="json") if self.last is not None else None,
        }
