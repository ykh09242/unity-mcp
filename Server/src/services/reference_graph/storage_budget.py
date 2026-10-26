"""Durable scope admission, main-page budgets and cooperative SQLite sidecar pressure.

The ledger coordinates upgraded processes sharing one private state directory.
Sidecar sampling is deliberately not a filesystem quota: SQLite instructions and
commit flushes can overshoot between samples. Normal cache spilling stays enabled.
"""

from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Final

from services.reference_graph.store import _hash, check_owned_path


MIB: Final = 1024 * 1024
LEDGER_BYTES: Final = 16 * MIB
LEDGER_ALLOWANCE: Final = 2 * LEDGER_BYTES + MIB
SIDECARS: Final = ("", "-wal", "-shm", "-journal")
PROGRESS_INSTRUCTIONS: Final = 5000


class StorageBudgetError(ValueError):
    """An operator budget or temporary reader pressure refused a durable write."""


def _setting(name: str, default: int) -> int:
    variable = "UNITY_MCP_REFERENCE_GRAPH_STORAGE_" + name
    value = os.environ.get(variable, str(default))
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{variable} must be a positive integer") from error
    if parsed < 1:
        raise ValueError(f"{variable} must be a positive integer")
    return parsed


@dataclass(frozen=True, slots=True)
class StoragePolicy:
    scopes: int
    user_scopes: int
    total_bytes: int
    user_bytes: int
    scope_bytes: int
    initial_bytes: int

    @classmethod
    def environment(cls) -> "StoragePolicy":
        scope = _setting("MAX_SCOPE_BYTES", 8192 * MIB)
        initial = min(scope, _setting("RESERVATION_BYTES", 8 * MIB))
        if initial < 64 * 1024:
            raise ValueError("Reference graph main-file budget must allow at least 64 KiB")
        return cls(
            _setting("MAX_SCOPES", 1024),
            _setting("MAX_USER_SCOPES", 128),
            _setting("MAX_BYTES", 262144 * MIB),
            _setting("MAX_USER_BYTES", 65536 * MIB),
            scope,
            initial,
        )


def _regular(path: Path) -> None:
    check_owned_path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise ValueError("Reference graph budget files must be owned regular files")


def file_sizes(path: Path) -> dict[str, int]:
    prefix = os.fspath(path)
    sizes = {}
    for suffix in SIDECARS:
        try:
            sizes[suffix] = os.stat(prefix + suffix).st_size
        except FileNotFoundError:
            sizes[suffix] = 0
    return sizes


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Keep a stable inode; removing lock files can split cross-process ownership."""
    _regular(path)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise ValueError("Reference graph lock must be an owned regular file")
        deadline = time.monotonic() + 30
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl  # pylint: disable=import-error  # Unix-only standard library.

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN) or time.monotonic() >= deadline:
                    raise StorageBudgetError(
                        "Reference graph budget lock is busy; retry"
                    ) from error
                time.sleep(0.01)
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _reservation(capacity: int) -> int:
    return 6 * capacity + MIB


class StorageBudget:
    """A scope's monotonically reserved capacity; existing saved work is never evicted."""

    def __init__(self, path: Path, principal: str, *, existing_bytes: int = 0):
        self.path = path
        self.owner = _hash((principal,))
        self.policy = StoragePolicy.environment()
        self.capacity = 0
        self.reserved = 0
        self.capacity, self.reserved = self._reserve(
            self.policy.initial_bytes, admitting=True, existing_bytes=existing_bytes
        )

    @contextmanager
    def _ledger(self) -> Iterator[sqlite3.Connection]:
        root = self.path.parent
        with file_lock(root / ".storage.lock"):
            path = root / ".storage.sqlite"
            for suffix in SIDECARS:
                _regular(Path(str(path) + suffix))
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                descriptor = None
            if descriptor is not None:
                os.close(descriptor)
            with closing(sqlite3.connect(path, timeout=30)) as db:
                db.execute("PRAGMA synchronous=FULL")
                page_size = db.execute("PRAGMA page_size").fetchone()[0]
                db.execute(f"PRAGMA max_page_count={LEDGER_BYTES // page_size}")
                application = db.execute("PRAGMA application_id").fetchone()[0]
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if application != 0x554D5242 and (application or tables):
                    raise ValueError("Reference graph budget ledger is not owned; preserve it")
                if application == 0:
                    # Inventory once, outside the ledger write transaction. Unknown
                    # files are charged conservatively, never opened or deleted.
                    existing = []
                    names = set()
                    for item in root.iterdir():
                        name = item.name
                        for suffix in SIDECARS[1:]:
                            if name.endswith(suffix):
                                name = name[: -len(suffix)]
                                break
                        if name.endswith(".sqlite") and name != path.name:
                            names.add(name)
                    for name in names:
                        sizes = {}
                        for suffix in SIDECARS:
                            try:
                                sizes[suffix] = (root / (name + suffix)).lstat().st_size
                            except FileNotFoundError:
                                sizes[suffix] = 0
                        capacity = max(
                            self.policy.initial_bytes, sizes[""] + self.policy.initial_bytes
                        )
                        reserved = max(_reservation(capacity), sum(sizes.values()) + MIB)
                        existing.append((name, "", capacity, reserved))
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "CREATE TABLE scopes(name TEXT PRIMARY KEY,owner TEXT NOT NULL,capacity INTEGER NOT NULL,reserved INTEGER NOT NULL)"
                        )
                        db.execute("CREATE INDEX scopes_owner ON scopes(owner)")
                        db.executemany("INSERT INTO scopes VALUES (?,?,?,?)", existing)
                        db.execute("PRAGMA application_id=1431130690")
                        db.execute("PRAGMA user_version=1")
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("Unsupported reference graph budget ledger; preserve it")
                yield db

    def _reserve(
        self, requested: int, *, admitting: bool = False, existing_bytes: int = 0
    ) -> tuple[int, int]:
        with self._ledger() as db, db:
            row = db.execute(
                "SELECT owner,capacity,reserved FROM scopes WHERE name=?", (self.path.name,)
            ).fetchone()
            # The root OS lock stabilizes admission while any legacy file is
            # measured; do not hold the ledger's SQLite writer across stat I/O.
            existing = row is None and self.path.exists()
            sizes = file_sizes(self.path) if existing else {}
            db.execute("BEGIN IMMEDIATE")
            recovered = False
            if row is not None:
                if row[0] not in ("", self.owner):
                    raise ValueError("Reference graph budget identity does not match")
                if not row[0]:
                    db.execute(
                        "UPDATE scopes SET owner=? WHERE name=?", (self.owner, self.path.name)
                    )
                if existing_bytes > row[1]:
                    # Trusted SQLite page counts can exceed a legacy inventory's
                    # main-file size after WAL recovery. Preserve and charge that
                    # existing work even when it exceeds newly configured limits.
                    requested = existing_bytes + self.policy.initial_bytes
                    recovered = True
                elif admitting or requested <= row[1]:
                    return row[1], row[2]
            if existing:
                requested = max(
                    requested, max(sizes[""], existing_bytes) + self.policy.initial_bytes
                )
            count, total = db.execute(
                "SELECT count(*),coalesce(sum(reserved),0) FROM scopes"
            ).fetchone()
            user_count, user_total = db.execute(
                "SELECT count(*),coalesce(sum(reserved),0) FROM scopes WHERE owner=?", (self.owner,)
            ).fetchone()
            reserved = max(
                _reservation(requested),
                row[2] if row else 0,
                sum(sizes.values()) + MIB if existing else 0,
            )
            growth = reserved - (row[2] if row else 0)
            if not (existing or recovered) and (
                requested > self.policy.scope_bytes
                or (
                    row is None
                    and (count >= self.policy.scopes or user_count >= self.policy.user_scopes)
                )
                or total + growth + LEDGER_ALLOWANCE > self.policy.total_bytes
                or user_total + growth > self.policy.user_bytes
            ):
                raise StorageBudgetError(
                    "Reference graph storage budget exhausted; retain saved work and ask the operator to raise budgets or archive unused scopes and rebuild the ledger with all graph servers stopped"
                )
            db.execute(
                "INSERT INTO scopes VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET capacity=excluded.capacity,reserved=excluded.reserved",
                (self.path.name, self.owner, requested, reserved),
            )
            return requested, reserved

    def grow(self) -> None:
        requested = min(self.policy.scope_bytes, self.capacity * 2)
        if requested <= self.capacity:
            raise StorageBudgetError(
                "Reference graph main-file budget exhausted; delete unneeded snapshots or ask the operator to raise the budget"
            )
        self.capacity, self.reserved = self._reserve(requested)

    @contextmanager
    def writer(self, connection: sqlite3.Connection) -> Iterator["WriteMeter"]:
        with file_lock(Path(str(self.path) + ".lock")):
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            if pages * page_size > self.capacity:
                self.capacity, self.reserved = self._reserve(
                    self.capacity, admitting=True, existing_bytes=pages * page_size
                )
            if pages * page_size > self.capacity * 3 // 4:
                try:
                    self.grow()
                except StorageBudgetError:
                    # A denied increase does not prevent use of already reserved
                    # pages, reads, or deletions that fit the current allowance.
                    pass
            connection.execute(f"PRAGMA max_page_count={max(pages, self.capacity // page_size)}")
            sizes = file_sizes(self.path)
            if sizes["-wal"] > self.capacity * 2:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                connection.execute("PRAGMA busy_timeout=0")
                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    connection.execute("PRAGMA busy_timeout=30000")
                sizes = file_sizes(self.path)
            meter = WriteMeter(
                self.path,
                max(_reservation(self.capacity), sum(sizes.values())),
                sizes["-wal"] > self.capacity * 2,
            )
            connection.set_progress_handler(meter.sample, PROGRESS_INSTRUCTIONS)
            try:
                yield meter
            finally:
                connection.set_progress_handler(None, 0)


@dataclass(slots=True)
class WriteMeter:
    """Mutable callback flags belong to one transaction, never to another writer."""

    path: Path
    allowance: int
    reader_pressure: bool
    exceeded: bool = False
    stat_error: OSError | None = None

    def sample(self) -> int:
        try:
            self.exceeded |= sum(file_sizes(self.path).values()) > self.allowance
        except OSError as error:
            self.stat_error = error
        return int(self.exceeded or self.stat_error is not None)

    def check(self, changed: bool) -> None:
        self.sample()
        if self.stat_error is not None:
            raise StorageBudgetError(
                "Reference graph budget files could not be measured; retry"
            ) from self.stat_error
        if self.exceeded or (self.reader_pressure and changed):
            raise StorageBudgetError(
                "Reference graph sidecar budget reached; allow active readers to finish, then retry the rolled-back write"
            )
