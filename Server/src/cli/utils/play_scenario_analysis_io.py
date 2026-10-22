"""Bounded local artifact I/O shared by offline scenario analysis commands."""

import os
from pathlib import Path
import stat

from cli.utils.play_scenario_query_compare import read_report
from pydantic import JsonValue


class AnalysisError(ValueError):
    """Selected evidence or output violates the offline artifact contract."""


def safe_local_path(source: Path) -> Path:
    """Reject remote/device namespaces, sensitive filenames and linked ancestors."""
    spelling = os.fspath(source).replace("\\", "/")
    if spelling.startswith(("//", "/??/")):
        raise AnalysisError("Remote or device paths are unsupported")
    path = Path(os.path.abspath(source))
    if os.fspath(path).replace("\\", "/").startswith(("//", "/??/")):
        raise AnalysisError("Remote or device paths are unsupported")
    parts = [part.casefold() for part in path.parts]
    device_names = {"con", "prn", "aux", "nul", "conin$", "conout$"}
    device_names.update(f"{prefix}{index}" for prefix in ("com", "lpt") for index in range(1, 10))
    if any(part.rstrip(" .").split(".")[0] in device_names for part in parts):
        raise AnalysisError("Device paths are unsupported")
    if any(part.startswith(".env") for part in parts) or any(
        ":" in part for part in path.parts if part != path.anchor
    ):
        raise AnalysisError("Sensitive or alternate-stream paths are unsupported")
    for ancestor in (*reversed(path.parents), path):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise AnalysisError("Linked or reparse paths are unsupported")
    return path


def read_bytes(source: Path, limit: int) -> bytes:
    """Read at most limit bytes from one stable regular local file."""
    path = safe_local_path(source)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise AnalysisError(f"Artifact must be a regular file within {limit} bytes")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise AnalysisError("Artifact changed before reading")
        if opened.st_size > limit:
            raise AnalysisError("Opened artifact exceeds its byte limit")
        content = stream.read(opened.st_size + 1)
    if len(content) != opened.st_size:
        raise AnalysisError("Artifact changed while reading")
    return content


def read_json(source: Path) -> dict[str, JsonValue]:
    """Reuse native report JSON bounds, duplicate-key and finite-number validation."""
    return read_report(safe_local_path(source), "analysis")


def write_new(source: Path, content: bytes) -> Path:
    """Create an explicit local output without overwriting any existing artifact."""
    path = safe_local_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_local_path(path)
    with path.open("xb") as stream:
        stream.write(content)
    return path
