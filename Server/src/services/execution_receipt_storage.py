"""Private single-writer receipt files; contains no dispatch or native job state."""

from __future__ import annotations

import os
import re
import stat
from itertools import islice
from pathlib import Path

from services.execution_receipt_identity import HEX_ID, ReceiptUnavailable
from services.private_store_bootstrap import (
    StoreBootstrapError,
    validate_bootstrap,
    finish_bootstrap,
)

METADATA_BYTES = 8192
MAX_RECORDS = 1024
METADATA_QUOTA = 16 * 1024 * 1024
RESULT_BYTES = 1024 * 1024
RESULT_QUOTA = 64 * 1024 * 1024
_MARKER = b"unity-mcp-execution-receipts-v1\n"
_ENTRY = re.compile(r"[0-9a-f]{32}\.(?:json|stage|result|result-stage)\Z")
_MAX_ENTRIES = MAX_RECORDS * 4 + 3


class ReceiptFiles:
    """Use an explicitly private operator root and one OS-held writer lock.

    Windows privacy relies on the operator's per-user inherited ACL, as with the
    existing WaitJournal. POSIX permissions are checked; they do not prove ACLs.
    Atomic intent is not an atomic transaction with Unity effects. POSIX fsyncs
    both file and directory; Windows retains the existing fsync/replace boundary.
    """

    def __init__(self, root: Path | None, *, private_root: bool = False) -> None:
        if root is None or private_root is not True:
            raise ReceiptUnavailable("execution_receipts_disabled")
        self.root = Path(root).absolute()
        self.writer = None
        self.locked = False
        self.store_id = ""
        try:
            self._open()
        except Exception as error:
            self.close()
            if isinstance(error, StoreBootstrapError):
                raise ReceiptUnavailable("receipt_bootstrap_" + str(error)) from error
            if isinstance(error, ReceiptUnavailable):
                raise
            raise ReceiptUnavailable("receipt_store_unavailable") from error

    @staticmethod
    def _private(path: Path, *, directory: bool = False) -> os.stat_result:
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ReceiptUnavailable("linked_receipt_path")
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode) or (not directory and info.st_nlink != 1):
            raise ReceiptUnavailable("invalid_receipt_path")
        if os.name == "posix":
            uid = os.getuid()  # pylint: disable=no-member  # POSIX-only ownership check.
            if info.st_uid != uid or info.st_mode & 0o077:
                raise ReceiptUnavailable("receipt_path_not_private")
        return info

    @staticmethod
    def _create(path: Path):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        return os.fdopen(os.open(path, flags, 0o600), "wb")

    def _sync_directory(self) -> None:
        if os.name != "nt":
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _open(self) -> None:
        for path in (self.root, *self.root.parents):
            if path.exists() and (
                path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400
            ):
                raise ReceiptUnavailable("linked_receipt_root")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)
        validate_bootstrap(self.root, _MARKER, _ENTRY, _MAX_ENTRIES, self._private)
        lock = self.root / "writer.lock"
        if lock.exists():
            self._private(lock)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        self.writer = os.fdopen(os.open(lock, flags, 0o600), "r+b")
        self._private(lock)
        if lock.stat().st_size > 1:
            raise ReceiptUnavailable("invalid_receipt_writer_lock")
        if lock.stat().st_size == 0:
            self.writer.write(b"1")
            self.writer.flush()
        self.writer.seek(0)
        if os.name == "nt":
            import msvcrt  # pylint: disable=import-error  # Windows-only OS lock.

            msvcrt.locking(self.writer.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # pylint: disable=import-error  # POSIX-only OS lock.

            fcntl.flock(self.writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.locked = True
        self.store_id = finish_bootstrap(self.root, _MARKER, _ENTRY, _MAX_ENTRIES, self._private)
        paths = list(islice(self.root.iterdir(), _MAX_ENTRIES + 1))
        if len(paths) > _MAX_ENTRIES:
            raise ReceiptUnavailable("receipt_directory_capacity")
        result_bytes = 0
        for path in paths:
            if path.name in {"owner", "installation", "writer.lock"}:
                continue
            if not _ENTRY.fullmatch(path.name):
                raise ReceiptUnavailable("invalid_receipt_entry")
            size = self._private(path).st_size
            is_result = path.suffix in {".result", ".result-stage"}
            if size > (RESULT_BYTES if is_result else METADATA_BYTES):
                raise ReceiptUnavailable("invalid_receipt_entry")
            if is_result:
                result_bytes += size
                if result_bytes > RESULT_QUOTA:
                    raise ReceiptUnavailable("receipt_result_storage_capacity")
            if path.suffix in {".stage", ".result-stage"}:
                # Only final metadata intent admits a handler. Neither kind of
                # orphan stage authorizes dispatch; final intents recover UNKNOWN.
                path.unlink()
        self._sync_directory()

    @property
    def overhead(self) -> int:
        return len(_MARKER) + 1 + 33

    def ids(self) -> list[str]:
        paths = list(islice(self.root.iterdir(), _MAX_ENTRIES + 1))
        if len(paths) > _MAX_ENTRIES:
            raise ReceiptUnavailable("receipt_directory_capacity")
        records = []
        for path in paths:
            if path.name in {"owner", "installation", "writer.lock"}:
                continue
            if not _ENTRY.fullmatch(path.name) or path.suffix not in {".json", ".result"}:
                raise ReceiptUnavailable("invalid_receipt_entry")
            self._private(path)
            if path.suffix == ".json":
                records.append(path.stem)
        if len(records) > MAX_RECORDS:
            raise ReceiptUnavailable("receipt_record_capacity")
        return records

    def read(self, receipt_id: str) -> bytes:
        return self._read(receipt_id, "json", METADATA_BYTES)

    def read_result(self, receipt_id: str) -> bytes:
        return self._read(receipt_id, "result", RESULT_BYTES)

    def _read(self, receipt_id: str, suffix: str, limit: int) -> bytes:
        if type(receipt_id) is not str or not HEX_ID.fullmatch(receipt_id):
            raise ReceiptUnavailable("invalid_receipt_id")
        path = self.root / f"{receipt_id}.{suffix}"
        if self._private(path).st_size > limit:
            raise ReceiptUnavailable("receipt_file_limit")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ReceiptUnavailable("receipt_file_limit")
        return raw

    def write(self, receipt_id: str, raw: bytes) -> None:
        self._write(receipt_id, raw, "stage", "json", METADATA_BYTES)

    def write_result(self, receipt_id: str, raw: bytes) -> None:
        self._write(receipt_id, raw, "result-stage", "result", RESULT_BYTES)

    def _write(
        self, receipt_id: str, raw: bytes, stage_suffix: str, final_suffix: str, limit: int
    ) -> None:
        if type(receipt_id) is not str or not HEX_ID.fullmatch(receipt_id):
            raise ReceiptUnavailable("invalid_receipt_id")
        if type(raw) is not bytes or len(raw) > limit:
            raise ReceiptUnavailable("receipt_file_limit")
        stage, final = (
            self.root / f"{receipt_id}.{suffix}" for suffix in (stage_suffix, final_suffix)
        )
        # Never erase an uncertain stage here. The domain is poisoned on any I/O
        # failure, and exclusive reopen reconciles it without executing anything.
        with self._create(stage) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, final)
        self._sync_directory()

    def remove(self, receipt_id: str) -> None:
        self._remove(receipt_id, "json")

    def remove_result(self, receipt_id: str) -> None:
        self._remove(receipt_id, "result")

    def _remove(self, receipt_id: str, suffix: str) -> None:
        if type(receipt_id) is not str or not HEX_ID.fullmatch(receipt_id):
            raise ReceiptUnavailable("invalid_receipt_id")
        path = self.root / f"{receipt_id}.{suffix}"
        self._private(path)
        path.unlink()
        self._sync_directory()

    def reconcile_results(self, retained: frozenset[str]) -> None:
        """Remove orphan results only while this instance holds the exclusive lock."""
        if not self.locked:
            raise ReceiptUnavailable("receipt_writer_not_locked")
        for path in self.root.iterdir():
            if path.suffix == ".result" and path.stem not in retained:
                self._private(path)
                path.unlink()
        self._sync_directory()

    def close(self) -> None:
        if self.writer is not None:
            try:
                if self.locked:
                    self.writer.seek(0)
                    if os.name == "nt":
                        import msvcrt  # pylint: disable=import-error

                        msvcrt.locking(self.writer.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl  # pylint: disable=import-error

                        fcntl.flock(self.writer.fileno(), fcntl.LOCK_UN)
            finally:
                self.writer.close()
                self.writer = None
                self.locked = False
