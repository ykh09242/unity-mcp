"""Resumable bounded filesystem fingerprinting; no file inventory grows with file count."""

from hashlib import blake2b
import os
from pathlib import Path
from stat import S_ISDIR, S_ISLNK, S_ISREG
from typing import Callable, Iterable

SKIPPED = {"library", "temp", "logs", "obj", ".git", "node_modules"}
CHUNK_BYTES = 65536


class ScanCursor:
    """Retain traversal position between calls; close handles on retirement/cancellation."""

    def __init__(self, roots: Iterable[Path], watched: Path | None = None):
        self.watched = os.path.normcase(os.path.abspath(watched)) if watched is not None else None
        self.watched_digest: bytes | None = None
        self.visited: set[str] = set()
        self.newest: int | None = None
        self.digest = 0
        self.count = 0
        self.complete = False
        self.limited = False
        self.bytes_read = 0
        self.byte_budget = CHUNK_BYTES
        self.iterator = self._walk_roots(tuple(roots))

    def close(self) -> None:
        self.iterator.close()

    def advance(self, entries: int, byte_budget: int, stopped: Callable[[], bool]) -> None:
        self.bytes_read = 0
        self.byte_budget = byte_budget
        for _ in range(entries):
            if stopped() or self.bytes_read >= byte_budget:
                break
            try:
                mtime, fingerprint = next(self.iterator)
            except StopIteration:
                self.complete = True
                break
            if mtime is not None:
                self.newest = mtime if self.newest is None else max(self.newest, mtime)
            if fingerprint is not None:
                self.digest ^= int.from_bytes(fingerprint, "big")
                self.count += 1

    @staticmethod
    def _linked(info) -> bool:
        return S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 1024)

    @staticmethod
    def _stamp(info) -> tuple[int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    @staticmethod
    def _fingerprint(path: str, metadata: str, content: bytes = b"") -> bytes:
        digest = blake2b(digest_size=32)
        digest.update(os.path.normcase(os.path.abspath(path)).encode("utf-8", "surrogatepass"))
        digest.update(b"\0" + metadata.encode("ascii") + b"\0" + content)
        return digest.digest()

    def _walk_roots(self, roots):
        for root in roots:
            yield from self._walk(root, 0)

    def _walk(self, root: Path, depth: int):
        key = os.path.normcase(os.path.abspath(root))
        if key in self.visited:
            return
        if depth >= 64 or len(self.visited) >= 20000:
            self.limited = True
            yield None, None
            return
        try:
            info = root.stat(follow_symlinks=False)
            if self._linked(info) or not S_ISDIR(info.st_mode):
                return
            with os.scandir(root) as children:
                self.visited.add(key)
                yield None, self._fingerprint(key, f"directory:{info.st_dev}:{info.st_ino}")
                for entry in children:
                    if entry.name.startswith(".") or entry.name.lower() in SKIPPED:
                        yield None, None
                        continue
                    path = Path(entry.path)
                    lowered = [part.casefold() for part in path.parts]
                    if any(
                        lowered[index : index + 3] == ["assets", "resources", "gamedata"]
                        for index in range(len(lowered) - 2)
                    ):
                        yield None, None
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if self._linked(info):
                            yield None, None
                        elif S_ISDIR(info.st_mode):
                            yield from self._walk(path, depth + 1)
                        elif S_ISREG(info.st_mode):
                            # Windows DirEntry.stat can report zero device/inode; obtain a path identity before opening.
                            yield from self._file(path, path.stat(follow_symlinks=False))
                    except OSError:
                        self.limited = True
                        yield None, None
        except FileNotFoundError:
            return  # A missing root is represented by its absence from the complete digest.
        except OSError:
            self.limited = True
            yield None, None

    def _file(self, path, before):
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not S_ISREG(opened.st_mode)
                or self._linked(opened)
                or self._stamp(opened) != self._stamp(before)
            ):
                self.limited = True
                yield None, None
                return
            digest = blake2b(digest_size=32)
            while True:
                read_size = min(CHUNK_BYTES, max(1, self.byte_budget - self.bytes_read))
                block = stream.read(read_size)
                self.bytes_read += len(block)
                digest.update(block)
                if len(block) < read_size:
                    break
                yield None, None
            after = os.fstat(stream.fileno())
            if self._stamp(after) != self._stamp(before):
                self.limited = True
                yield None, None
                return
            content_hash = digest.digest()
            if os.path.normcase(os.path.abspath(path)) == self.watched:
                self.watched_digest = content_hash
            yield (
                before.st_mtime_ns,
                self._fingerprint(str(path), str(self._stamp(before)), content_hash),
            )
