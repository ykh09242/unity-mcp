"""Recoverable identity publication for private, exclusively locked stores."""

from __future__ import annotations

from itertools import islice
import os
from pathlib import Path
import re
import stat
from uuid import uuid4


class StoreBootstrapError(RuntimeError):
    """Preserve an unrecognized directory; never invent identity over records."""


def _read(path: Path, limit: int, private) -> bytes:
    private(path)
    info = path.lstat()
    if (
        path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size > limit
    ):
        raise StoreBootstrapError("invalid_metadata_path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise StoreBootstrapError("metadata_identity_changed")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise StoreBootstrapError("invalid_metadata_size")
    return raw


def validate_bootstrap(root, marker, record_pattern, limit, private, *, locked=False):
    """Validate before creating the lock, and again after acquiring it."""
    paths = list(islice(root.iterdir(), limit + 3))
    if len(paths) > limit + 2:
        raise StoreBootstrapError("directory_capacity")
    names = {path.name for path in paths}
    owner_exists = "owner" in names
    if owner_exists:
        if _read(root / "owner", len(marker), private) != marker:
            raise StoreBootstrapError("unrecognized_owner")
        fixed = {"owner", "installation", "writer.lock", "owner.stage", "installation.stage"}
        if any(name not in fixed and not record_pattern.fullmatch(name) for name in names):
            raise StoreBootstrapError("unrecognized_entry")
    elif names - {"writer.lock", "owner.stage"}:
        raise StoreBootstrapError("unrecognized_root")
    if "writer.lock" in names and not locked:
        if _read(root / "writer.lock", 1, private) not in (b"", b"1"):
            raise StoreBootstrapError("unrecognized_writer_lock")
    if "owner.stage" in names:
        if not marker.startswith(_read(root / "owner.stage", len(marker), private)):
            raise StoreBootstrapError("invalid_owner_stage")
    identity = None
    if "installation" in names:
        identity = _read(root / "installation", 33, private)
        if not re.fullmatch(rb"[0-9a-f]{32}\n", identity):
            raise StoreBootstrapError("invalid_installation")
    elif any(record_pattern.fullmatch(name) for name in names):
        raise StoreBootstrapError("records_without_installation")
    if "installation.stage" in names:
        stage = _read(root / "installation.stage", 33, private)
        if not re.fullmatch(rb"(?:[0-9a-f]{0,32}|[0-9a-f]{32}\n)", stage):
            raise StoreBootstrapError("invalid_installation_stage")
        if identity is not None and stage != identity:
            raise StoreBootstrapError("ambiguous_installation_stage")


def _sync_directory(root: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _publish(root: Path, name: str, raw: bytes) -> None:
    # Only called under the store's OS writer lock after bounded validation.
    stage = root / (name + ".stage")
    stage.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(stage, flags, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stage, root / name)
    _sync_directory(root)


def finish_bootstrap(root, marker, record_pattern, limit, private) -> str:
    """Caller owns the existing OS writer lock until the store is closed."""
    validate_bootstrap(root, marker, record_pattern, limit, private, locked=True)
    owner = root / "owner"
    if not owner.exists():
        _publish(root, "owner", marker)
    else:
        (root / "owner.stage").unlink(missing_ok=True)
    installation = root / "installation"
    if not installation.exists():
        stage = root / "installation.stage"
        raw = _read(stage, 33, private) if stage.exists() else b""
        # A complete unpublished identity survives restart. A partial candidate
        # has never admitted a record and can be replaced only in this empty store.
        if re.fullmatch(rb"[0-9a-f]{32}\n", raw):
            # Preserve the sole durable candidate even if recovery itself exits.
            os.replace(stage, installation)
            _sync_directory(root)
        else:
            _publish(root, "installation", (uuid4().hex + "\n").encode("ascii"))
    else:
        (root / "installation.stage").unlink(missing_ok=True)
    identity = _read(installation, 33, private)
    _sync_directory(root)
    return identity[:-1].decode("ascii")
