"""Worker-owned phase logs and incremental retention of positively owned sessions."""

import os
from pathlib import Path
import re
import stat
import time

RETENTION_SECONDS = 7 * 86400
SESSION = re.compile(r"phase-timing-[1-9][0-9]*-[a-f0-9]{32}")
MAGIC = b"unity-mcp-phase-log-v1\n"


def _plain(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise OSError("Diagnostic path is not a single-link regular file")
    return info


def _same(left, right):
    return (
        left is not None
        and right is not None
        and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
    )


def _open_existing(path, info):
    stream = path.open("r+b", buffering=0)
    try:
        if not _same(info, os.fstat(stream.fileno())) or not _same(info, _plain(path)):
            raise OSError("Diagnostic file identity changed")
        return stream
    except BaseException:
        stream.close()
        raise


def _lock(stream):
    if os.name == "nt":
        import msvcrt  # pylint: disable=import-error  # Windows-only standard library.

        # Lock beyond the small immutable owner header so other processes can
        # inspect ownership without trying to read a mandatory locked byte.
        stream.seek(4096)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        stream.seek(0)
    else:
        import fcntl  # pylint: disable=import-error  # POSIX-only standard library.

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _directory_matches(directory, identity):
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not _same(info, identity)
    ):
        raise OSError("Diagnostic directory identity changed")


def _paths(path):
    return (path, path.with_suffix(".log.1"), path.with_suffix(".log.2"))


class PhaseLogFile:
    """A unique session never reopens another session's lease, even after PID reuse."""

    def __init__(self, path, max_bytes):
        if not SESSION.fullmatch(path.stem) or path.suffix != ".log":
            raise ValueError("Invalid diagnostic session name")
        directory = path.parent.resolve(strict=True)
        self.path = directory / path.name
        self.paths = _paths(self.path)
        self.owner_path = self.path.with_suffix(".owner")
        self.directory_identity = directory.stat()
        self.max_bytes = max_bytes
        self.owner = None
        self.maintenance = LogRetention(directory)
        self.next_maintenance = 0

    def _start(self):
        _directory_matches(self.path.parent, self.directory_identity)
        if any(path.exists() or path.is_symlink() for path in self.paths):
            raise OSError("Diagnostic session already has files")
        owner = self.owner_path.open("x+b")
        try:
            _lock(owner)
            owner.write(MAGIC + self.path.stem.encode("ascii") + b"\n")
            owner.flush()
        except BaseException:
            identity = os.fstat(owner.fileno())
            owner.close()
            try:
                if _same(identity, _plain(self.owner_path)):
                    self.owner_path.unlink()
            except OSError:
                pass
            raise
        self.owner = owner

    def write(self, encoded):
        if self.owner is None:
            self._start()
        _directory_matches(self.path.parent, self.directory_identity)
        states = [_plain(path) for path in self.paths]
        if states[0] is not None and states[0].st_size + len(encoded) > self.max_bytes:
            if states[1] is not None:
                self.paths[1].replace(self.paths[2])
            self.path.replace(self.paths[1])
            states[0] = None
        if states[0] is None:
            with self.path.open("xb") as output:
                output.write(encoded)
        else:
            with _open_existing(self.path, states[0]) as output:
                output.seek(0, os.SEEK_END)
                output.write(encoded)

    def maintain(self):
        now = time.monotonic()
        if now >= self.next_maintenance:
            self.next_maintenance = now + 60
            return self.maintenance.step()
        return None

    def close(self):
        self.maintenance.close()
        if self.owner is not None:
            try:
                info = os.fstat(self.owner.fileno())
                if _same(info, _plain(self.owner_path)):
                    # A quiet but long-lived writer receives a full retention
                    # window after graceful shutdown. Crashes use last-write age.
                    self.owner_path.touch()
            finally:
                self.owner.close()
                self.owner = None


class LogRetention:
    """At most 128 directory entries per worker pass; no recursive traversal."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve(strict=True)
        self.identity = self.directory.stat()
        self.scan = None
        self.result = {}

    def step(self, max_entries=128):
        _directory_matches(self.directory, self.identity)
        if self.scan is None:
            self.scan = os.scandir(self.directory)
            self.result = {
                "scan_complete": False,
                "entries_scanned": 0,
                "owned_sessions": 0,
                "active_sessions": 0,
                "retained_bytes": 0,
                "oldest_retained_age_seconds": 0,
                "deleted_sessions": 0,
                "cleanup_errors": 0,
            }
        for _ in range(max_entries):
            entry = next(self.scan, None)
            if entry is None:
                self.close()
                self.result["scan_complete"] = True
                break
            self.result["entries_scanned"] += 1
            path = self.directory / entry.name
            if path.suffix == ".owner" and SESSION.fullmatch(path.stem):
                try:
                    self._inspect(path)
                except OSError:
                    self.result["cleanup_errors"] += 1
        return dict(self.result)

    def _inspect(self, owner_path):
        owner_info = _plain(owner_path)
        if owner_info is None or owner_info.st_size > 256:
            return
        paths = _paths(owner_path.with_suffix(".log"))
        retired = False
        with _open_existing(owner_path, owner_info) as owner:
            if owner.read(257) != MAGIC + owner_path.stem.encode("ascii") + b"\n":
                return
            active = False
            try:
                _lock(owner)
            except OSError:
                # Unsupported locks and permission failures also fail closed.
                active = True
            # Graceful close can touch the owner immediately before we acquire
            # its lease. Never apply the age sampled before that handoff.
            refreshed = _plain(owner_path)
            if not _same(owner_info, refreshed) or not _same(refreshed, os.fstat(owner.fileno())):
                raise OSError("Diagnostic owner changed during retention")
            owner_info = refreshed
            if not active:
                owner.seek(0)
                if owner.read(257) != MAGIC + owner_path.stem.encode("ascii") + b"\n":
                    return
            states = [_plain(path) for path in paths]
            age = max(
                0,
                time.time()
                - max(
                    [owner_info.st_mtime] + [info.st_mtime for info in states if info is not None]
                ),
            )
            self.result["owned_sessions"] += 1
            self.result["active_sessions"] += int(active)
            if not active and age > RETENTION_SECONDS:
                _directory_matches(self.directory, self.identity)
                for path, info in zip(paths, states):
                    if info is not None:
                        if not _same(info, _plain(path)):
                            raise OSError("Diagnostic log changed during retention")
                        path.unlink()
                retired = True
            else:
                self.result["retained_bytes"] += owner_info.st_size + sum(
                    info.st_size for info in states if info is not None
                )
                self.result["oldest_retained_age_seconds"] = max(
                    self.result["oldest_retained_age_seconds"], age
                )
        # Windows cannot unlink the open lease. A unique session never resumes;
        # concurrent cleaners are safe, and replacement owners are preserved.
        if retired and _same(owner_info, _plain(owner_path)):
            owner_path.unlink()
            self.result["deleted_sessions"] += 1

    def close(self):
        if self.scan is not None:
            self.scan.close()
            self.scan = None
