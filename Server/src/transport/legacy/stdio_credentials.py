"""Read only dedicated launch credentials from the current user's secure store."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import stat


def read_stdio_token(generation: str) -> str | None:
    """An untrusted greeting may identify only a strict random stdio launch ID."""
    if re.fullmatch(r"[0-9a-f]{32}", generation) is None:
        return None
    if os.name == "nt":
        return _read_windows_token(generation)
    # These stdlib members exist only on POSIX; this branch is never used on Windows.
    directory_flag = getattr(os, "O_DIRECTORY")
    follow_guard = getattr(os, "O_NOFOLLOW")
    user_id = getattr(os, "getuid")()
    directories = []
    try:
        directory_fd = os.open(Path.home(), os.O_RDONLY | directory_flag | follow_guard)
        directories.append(directory_fd)
        for part in (".unity-mcp", "stdio-auth", generation):
            parent = os.fstat(directory_fd)
            if parent.st_uid != user_id or parent.st_mode & 0o022:
                return None
            directory_fd = os.open(
                part, os.O_RDONLY | directory_flag | follow_guard, dir_fd=directory_fd
            )
            directories.append(directory_fd)
        directory = os.fstat(directory_fd)
        if directory.st_uid != user_id or directory.st_mode & 0o077:
            return None
        descriptor = os.open("token", os.O_RDONLY | follow_guard, dir_fd=directory_fd)
    except OSError:
        return None
    finally:
        for directory_fd in reversed(directories):
            os.close(directory_fd)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != user_id
            or metadata.st_mode & 0o077
            or metadata.st_size > 256
        ):
            return None
        try:
            token = stream.read(257).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return token if 32 <= len(token) <= 256 else None


def _read_windows_token(generation: str) -> str | None:
    """CredRead uses this logon session, avoiding assumptions about Windows chmod."""

    class Credential(ctypes.Structure):
        _fields_ = [
            ("flags", ctypes.c_uint32),
            ("type", ctypes.c_uint32),
            ("target", ctypes.c_wchar_p),
            ("comment", ctypes.c_wchar_p),
            ("last_written", ctypes.c_uint64),
            ("size", ctypes.c_uint32),
            ("blob", ctypes.c_void_p),
            ("persist", ctypes.c_uint32),
            ("attribute_count", ctypes.c_uint32),
            ("attributes", ctypes.c_void_p),
            ("alias", ctypes.c_wchar_p),
            ("user", ctypes.c_wchar_p),
        ]

    library = ctypes.WinDLL("advapi32", use_last_error=True)
    read = library.CredReadW
    read.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    read.restype = ctypes.c_int
    free = library.CredFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    pointer = ctypes.c_void_p()
    if not read("MCPForUnity.Stdio:" + generation, 1, 0, ctypes.byref(pointer)):
        return None
    try:
        credential = ctypes.cast(pointer, ctypes.POINTER(Credential)).contents
        if not credential.blob or not 32 <= credential.size <= 256:
            return None
        try:
            return ctypes.string_at(credential.blob, credential.size).decode("utf-8")
        except UnicodeDecodeError:
            return None
    finally:
        free(pointer)
