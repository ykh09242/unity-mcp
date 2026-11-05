"""Cross-process ownership of stable lock files, released on close or process exit."""

from collections.abc import Iterator
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import time


class FileLockError(OSError):
    """Acquiring a file lock failed or exhausted its bounded wait."""


@contextmanager
def file_lock(path: Path, *, wait: bool = True) -> Iterator[None]:
    """Keep a stable inode; removing lock files can split cross-process ownership."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1):
        raise ValueError("Lock files must be regular files without additional links")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise ValueError("Lock files must be regular files without additional links")
        deadline = time.monotonic() + 30
        while True:
            try:
                if os.name == "nt":
                    import msvcrt  # pylint: disable=import-error  # Windows-only standard library.

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl  # pylint: disable=import-error  # Unix-only standard library.

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if not wait and error.errno in (errno.EACCES, errno.EAGAIN):
                    raise BlockingIOError("File lock is busy") from error
                if error.errno not in (errno.EACCES, errno.EAGAIN) or time.monotonic() >= deadline:
                    raise FileLockError("File lock is busy or unavailable; retry") from error
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
