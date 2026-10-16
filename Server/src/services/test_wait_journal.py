"""Private, physically bounded records for optional MCP bounded-wait Tasks.

This is a single-writer journal, not a native job store. Interrupted Python waits
become honest JSON-RPC failures on reopen; reopening never dispatches a new read.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from itertools import islice
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from models.response_limits import (
    MAX_RESPONSE_BYTES,
    MAX_RESPONSE_DEPTH,
    MAX_RESPONSE_NODES,
    bounded_json_text,
    response_size,
)


class JournalUnavailable(RuntimeError):
    """The optional durable routing domain is unavailable; do not emit handles."""


class JournalCapacity(JournalUnavailable):
    """Reservations cannot fit; augmentation must fall back before creation."""


class TaskNotFound(LookupError):
    """Identical outcome for absent, expired and foreign-owned records."""


class DeadlineCheckpoint(BaseModel):
    """Persisted clock evidence, never a new countdown after restart."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    remaining_seconds: float = Field(ge=0, le=60)
    checkpoint_wall: float
    wall_deadline: float

    @classmethod
    def capture(cls, remaining: float, *, now: float) -> DeadlineCheckpoint:
        return cls(
            remaining_seconds=max(0, remaining),
            checkpoint_wall=now,
            wall_deadline=now + max(0, remaining),
        )

    def remaining(self, *, now: float) -> float:
        if not math.isfinite(now) or now < self.checkpoint_wall:
            return 0
        return max(0, min(self.remaining_seconds, self.wall_deadline - now))


class WaitCommandEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: str = Field(max_length=256)
    session_id: str | None = Field(default=None, max_length=1024)
    command_id: str | None = Field(default=None, max_length=1024)
    dispatched: bool = False
    command: str | None = Field(default=None, max_length=256)


class WaitOperation(BaseModel):
    """Trusted capture DTO. It deliberately contains no request/context objects."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    owner: str = Field(min_length=1, max_length=1024)
    user_id: str | None = Field(default=None, max_length=1024)
    project: str = Field(min_length=1, max_length=1024)
    instance: str = Field(min_length=1, max_length=2048)
    session_id: str = Field(min_length=1, max_length=1024)
    job_id: str = Field(min_length=1, max_length=1024)
    wait_timeout: int = Field(default=1, ge=1, le=60)
    evidence: WaitCommandEvidence | None = None
    wait: DeadlineCheckpoint
    outer: DeadlineCheckpoint


TaskStatus = Literal["working", "completed", "failed", "cancelled"]


class WaitRecord(BaseModel):
    """Versioned storage envelope; result is the exact ordinary wire result."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    task_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    operation: WaitOperation
    created_at: float
    updated_at: float
    expires_at: float
    status: TaskStatus = "working"
    cancel_requested: bool = False
    result: dict[str, JsonValue] | None = None
    error: dict[str, JsonValue] | None = None


class WaitJournal:
    """One private directory, exclusive OS writer, bounded atomic replacements.

    Each live record reserves TWO (ordinary response ceiling + metadata ceiling)
    slots. After terminal commit only actual file bytes remain charged. The
    current file and its sole .stage replacement fit that reservation together.
    With the default 128 MiB quota this intentionally permits only ONE worst-case
    active wait, even though a runtime's independent worker cap may be four.

    POSIX roots/entries must be current-user owned and private. Windows uses
    the supplied per-user directory's inherited ACL; POSIX modes do not prove
    Windows ACL privacy. The eventual installer must supply that private root.
    """

    _MARKER = b"unity-mcp-test-wait-journal-v1\n"
    _NAME = re.compile(r"^[0-9a-f]{32}\.(?:json|stage)$")

    @staticmethod
    def _check_posix_stat(info, *, uid: int, directory: bool) -> None:
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if info.st_uid != uid or info.st_mode & 0o077 or not expected_type(info.st_mode):
            raise JournalUnavailable("Journal path must be private and current-user owned")

    @classmethod
    def _require_private(cls, path: Path, *, directory: bool = False) -> None:
        if os.name == "posix":
            uid = os.getuid()  # pylint: disable=no-member  # POSIX-only standard library.
            cls._check_posix_stat(path.lstat(), uid=uid, directory=directory)

    @staticmethod
    def _create_private(path: Path):
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
        )
        return os.fdopen(descriptor, "wb")

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.time,
        result_bytes: int = MAX_RESPONSE_BYTES,
        metadata_bytes: int = 65_536,
        quota_bytes: int = 128 * 1024 * 1024,
        max_records: int = 256,
        ttl_seconds: float = 3600,
    ) -> None:
        if not (
            0 < result_bytes <= MAX_RESPONSE_BYTES
            and 0 < metadata_bytes <= 65_536
            and quota_bytes > 0
            and 0 < max_records <= 256
            and 0 < ttl_seconds <= 3600
        ):
            raise ValueError("Unsupported journal bounds")
        self.root = Path(root).absolute()
        self.clock = clock
        self.result_bytes = result_bytes
        self.metadata_bytes = metadata_bytes
        self.quota_bytes = quota_bytes
        self.max_records = max_records
        self.ttl_seconds = ttl_seconds
        self._record_bytes = result_bytes + metadata_bytes
        self._lock = threading.RLock()
        self._writer = None
        self._index: dict[str, tuple[int, bool]] = {}
        self.installation_id = ""
        try:
            self._open()
        except Exception as error:
            self.close()
            if isinstance(error, JournalUnavailable):
                raise
            raise JournalUnavailable("Wait journal cannot be opened") from error

    def _open(self) -> None:
        for path in (self.root, *self.root.parents):
            if path.exists() and (
                path.is_symlink()
                or getattr(path.lstat(), "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            ):
                raise JournalUnavailable("Linked journal path is unsupported")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._require_private(self.root, directory=True)
        marker = self.root / "owner"
        if not marker.exists():
            if any(self.root.iterdir()):
                raise JournalUnavailable("Journal directory is not private")
            with self._create_private(marker) as stream:
                stream.write(self._MARKER)
                stream.flush()
                os.fsync(stream.fileno())
        if (
            marker.is_symlink()
            or marker.stat().st_size != len(self._MARKER)
            or marker.read_bytes() != self._MARKER
        ):
            raise JournalUnavailable("Unrecognized journal directory")
        self._require_private(marker)
        lock_path = self.root / "writer.lock"
        if lock_path.is_symlink():
            raise JournalUnavailable("Linked writer lock")
        if lock_path.exists():
            self._require_private(lock_path)
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            0o600,
        )
        self._writer = os.fdopen(descriptor, "r+b")
        if os.name == "posix":
            uid = os.getuid()  # pylint: disable=no-member  # POSIX-only standard library.
            self._check_posix_stat(os.fstat(self._writer.fileno()), uid=uid, directory=False)
        if lock_path.stat().st_size > 1:
            raise JournalUnavailable("Invalid writer lock")
        if lock_path.stat().st_size == 0:
            self._writer.write(b"1")
            self._writer.flush()
        self._writer.seek(0)
        if os.name == "nt":
            import msvcrt  # pylint: disable=import-error  # Windows-only standard library.

            msvcrt.locking(self._writer.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # pylint: disable=import-error  # POSIX-only standard library.

            fcntl.flock(self._writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        paths = list(islice(self.root.iterdir(), self.max_records * 2 + 4))
        if len(paths) > self.max_records * 2 + 3:
            raise JournalUnavailable("Journal directory exceeds file bound")
        installation = self.root / "installation"
        if not installation.exists():
            if any(path.suffix in {".json", ".stage"} for path in paths):
                raise JournalUnavailable("Existing wait records have no installation identity")
            with self._create_private(installation) as stream:
                stream.write((uuid4().hex + "\n").encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
        self._require_private(installation)
        if installation.is_symlink() or installation.stat().st_size != 33:
            raise JournalUnavailable("Invalid journal installation identity")
        identity = installation.read_bytes()
        if not re.fullmatch(rb"[0-9a-f]{32}\n", identity):
            raise JournalUnavailable("Invalid journal installation identity")
        self.installation_id = identity[:-1].decode("ascii")
        for path in paths:
            if path.name in {"owner", "writer.lock", "installation"}:
                continue
            if (
                path.is_symlink()
                or not path.is_file()
                or not self._NAME.fullmatch(path.name)
                or path.stat().st_size > self._record_bytes
            ):
                raise JournalUnavailable("Invalid journal entry")
            self._require_private(path)
            if path.suffix == ".stage":
                # Exclusive writer + private version marker prove this is our
                # interrupted replacement, never a user-supplied arbitrary path.
                path.unlink()
                continue
            record = self._read(path.stem)
            self._index[record.task_id] = (path.stat().st_size, False)
        if len(self._index) > self.max_records:
            raise JournalUnavailable("Journal record count exceeds bound")
        if self.charged_bytes > self.quota_bytes:
            raise JournalUnavailable("Journal exceeds physical quota")
        self.prune()
        for task_id in tuple(self._index):
            record = self._read(task_id)
            if record.status == "working":
                self.finish(
                    task_id,
                    "failed",
                    error={
                        "code": -32603,
                        "message": "Bounded wait interrupted by server restart; native job state is unchanged",
                    },
                )

    @property
    def charged_bytes(self) -> int:
        overhead = len(self._MARKER) + 1 + 33
        return overhead + sum(
            2 * self._record_bytes if active else size for size, active in self._index.values()
        )

    @property
    def local_owner(self) -> str:
        """Shared local control trust domain; identity contains no credential."""
        return f"local:v1:{self.installation_id}"

    def _check_open(self) -> None:
        if self._writer is None:
            raise JournalUnavailable("Wait journal is closed")

    def _read(self, task_id: str) -> WaitRecord:
        path = self.root / f"{task_id}.json"
        if path.is_symlink() or path.stat().st_size > self._record_bytes:
            raise JournalUnavailable("Invalid journal record")
        self._require_private(path)
        try:
            raw = path.read_bytes()
            bounded = bounded_json_text(
                raw,
                max_bytes=self._record_bytes,
                max_depth=MAX_RESPONSE_DEPTH + 4,
                max_nodes=MAX_RESPONSE_NODES + 256,
            )
            if bounded is None:
                raise ValueError("Journal graph exceeds bounds")
            record = WaitRecord.model_validate_json(bounded)
            if (
                record.task_id != task_id
                or (record.status == "completed") != (record.result is not None)
                or (record.status == "failed") != (record.error is not None)
            ):
                raise ValueError("Inconsistent task state")
            self._validate(record)
            return record
        except (ValueError, RecursionError) as error:
            raise JournalUnavailable("Invalid journal record") from error

    def _validate(self, record: WaitRecord) -> None:
        metadata = record.model_dump(exclude_none=True, exclude={"result", "error"})
        # The added result key/separator also belongs to metadata, not to the
        # ordinary result ceiling. A valid ceiling-sized payload still fits.
        if (
            len(
                json.dumps(
                    metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode()
            )
            + 12
            > self.metadata_bytes
        ):
            raise JournalUnavailable("Wait metadata exceeds supported bounds")
        payload = record.result if record.result is not None else record.error
        if payload is not None and response_size(payload, max_bytes=self.result_bytes) is None:
            raise JournalUnavailable("Ordinary result exceeds supported response ceiling")

    def _write(self, record: WaitRecord, *, active: bool) -> None:
        self._validate(record)
        stage = self.root / f"{record.task_id}.stage"
        final = self.root / f"{record.task_id}.json"
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        count = 0
        try:
            with self._create_private(stage) as stream:
                for chunk in encoder.iterencode(record.model_dump(exclude_none=True)):
                    encoded = chunk.encode()
                    count += len(encoded)
                    if count > self._record_bytes:
                        raise JournalUnavailable("Journal record exceeds reserved capacity")
                    # Startup recovery has no live reservation. Bound its one
                    # small interruption replacement against actual physical use.
                    if (
                        not self._index[record.task_id][1]
                        and self.charged_bytes + count > self.quota_bytes
                    ):
                        raise JournalCapacity("Atomic replacement cannot fit")
                    stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(stage, final)
            if os.name != "nt":
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            self._index[record.task_id] = (count, active)
        except Exception as error:
            stage.unlink(missing_ok=True)
            if isinstance(error, JournalUnavailable):
                raise
            raise JournalUnavailable("Atomic journal commit failed") from error

    def create(self, operation: WaitOperation) -> WaitRecord:
        with self._lock:
            self._check_open()
            self.prune()
            if (
                len(self._index) >= self.max_records
                or self.charged_bytes + 2 * self._record_bytes > self.quota_bytes
            ):
                raise JournalCapacity("Wait journal reservation unavailable")
            now = self.clock()
            record = WaitRecord(
                task_id=uuid4().hex,
                operation=operation,
                created_at=now,
                updated_at=now,
                expires_at=now + self.ttl_seconds,
            )
            self._index[record.task_id] = (0, True)
            try:
                self._write(record, active=True)
            except Exception:
                self._index.pop(record.task_id)
                raise
            return record

    def get(self, task_id: str, owner: str) -> WaitRecord:
        with self._lock:
            self._check_open()
            if task_id not in self._index:
                raise TaskNotFound("Task not found")
            record = self._read(task_id)
            if record.operation.owner != owner or self.clock() >= record.expires_at:
                raise TaskNotFound("Task not found")
            return record

    def request_cancel(self, task_id: str, owner: str) -> WaitRecord:
        with self._lock:
            record = self.get(task_id, owner)
            if record.status == "working" and not record.cancel_requested:
                record.cancel_requested = True
                record.updated_at = self.clock()
                self._write(record, active=True)
            return record

    def finish(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: dict | None = None,
        error: dict | None = None,
    ) -> None:
        with self._lock:
            self._check_open()
            record = self._read(task_id)
            if record.status != "working":
                return
            if (
                status == "working"
                or (status == "completed") != (result is not None)
                or (status == "failed") != (error is not None)
            ):
                raise ValueError("Invalid terminal transition")
            record.status = status
            record.result = result
            record.error = error
            record.updated_at = self.clock()
            self._write(record, active=False)

    def prune(self) -> None:
        with self._lock:
            self._check_open()
            for task_id, (_, active) in tuple(self._index.items()):
                if not active and self.clock() >= self._read(task_id).expires_at:
                    (self.root / f"{task_id}.json").unlink()
                    del self._index[task_id]

    def close(self) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.close()
                self._writer = None

    def __enter__(self) -> WaitJournal:
        return self

    def __exit__(self, *_args) -> None:
        self.close()
