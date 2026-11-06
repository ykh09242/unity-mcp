"""Owner checks and byte-range leases shared with the native capture producer."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import errno
import os
from pathlib import Path
import stat

from services.reference_graph.store import check_owned_path


def _windows_owner(path: Path) -> None:
    # Compare the file owner with TokenUser, never with a caller-supplied name.
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    security.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(pointer),
        pointer,
        pointer,
        pointer,
        ctypes.POINTER(pointer),
    ]
    security.GetNamedSecurityInfoW.restype = wintypes.DWORD
    security.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        pointer,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    security.EqualSid.argtypes = [pointer, pointer]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    owner, descriptor, token = pointer(), pointer(), wintypes.HANDLE()
    result = security.GetNamedSecurityInfoW(
        str(path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor)
    )
    if result:
        raise OSError(result, "Native spool ownership is unavailable")
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD(0)
            security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            user_sid = pointer.from_buffer(buffer)
            if not security.EqualSid(owner, user_sid):
                raise ValueError("Native spool must belong to the current OS user")
        finally:
            kernel.CloseHandle(token)
    finally:
        kernel.LocalFree(descriptor)


def owned(path: Path, *, directory: bool = False) -> os.stat_result:
    """Reject links, alternate owners and multiply-linked spool evidence."""
    check_owned_path(path)
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or (not directory and info.st_nlink != 1):
        raise ValueError("Native spool requires unlinked regular files and real directories")
    if os.name == "nt":
        _windows_owner(path)
    elif os.name == "posix":
        uid = os.getuid()  # pylint: disable=no-member  # POSIX-only ownership check.
        if info.st_uid != uid or info.st_mode & 0o022:
            raise ValueError("Native spool must be owned by this user and not writable by others")
    else:
        raise ValueError("Native spool ownership is unsupported on this platform")
    return info


def identity(path: Path, *, directory: bool = False) -> tuple[int, int]:
    info = owned(path, directory=directory)
    return info.st_dev, info.st_ino


@contextmanager
def lease(path: Path):
    """Open an existing lease only; lock byte zero exactly like FileStream.Lock."""
    before = owned(path)
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        current = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Native spool lease path changed; preserve it")
        try:
            if os.name == "nt":
                import msvcrt  # pylint: disable=import-error  # Windows-only standard library.

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # pylint: disable=import-error  # Unix-only standard library.

                fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise ValueError("Native spool is active or maintenance is busy") from error
            raise
        try:
            if identity(path) != (before.st_dev, before.st_ino):
                raise ValueError("Native spool lease path changed after locking; preserve it")
            yield
        finally:
            if os.name == "nt":
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.lockf(descriptor, fcntl.LOCK_UN, 1, 0)
    finally:
        os.close(descriptor)
