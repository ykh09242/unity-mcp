"""Bounded, local, fingerprinted PNG attachments for a portable report."""

import base64
from hashlib import sha256
from html import escape
from pathlib import Path
import stat
import struct
from typing import Final

from pydantic import JsonValue

IMAGE_LIMIT: Final = 4 * 1024 * 1024
TOTAL_IMAGE_LIMIT: Final = 12 * 1024 * 1024


def unlinked(path: Path) -> None:
    """Reject symbolic links and Windows reparse points along an existing path."""
    for part in (path, *path.parents):
        metadata = part.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked report paths are not supported")


def screenshot_html(
    metadata: dict[str, JsonValue], root: Path | None, remaining: int
) -> tuple[str, int]:
    """Embed only a matching PNG below the explicit source's artifact directory."""
    unavailable = '<p class="muted">Image unavailable: local matching PNG evidence required.</p>'
    path_value, fingerprint = metadata.get("path"), metadata.get("sha256")
    if root is None or not isinstance(path_value, str) or not isinstance(fingerprint, str):
        return unavailable, 0
    try:
        root = root.absolute()
        unlinked(root)
        path = Path(path_value)
        if not path.is_absolute():
            if ":" in path_value or ".." in path_value.replace("\\", "/").split("/"):
                return unavailable, 0
            path = root / path_value.replace("\\", "/")
        unlinked(path)
        path.resolve().relative_to(root.resolve())
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > min(remaining, IMAGE_LIMIT):
            return unavailable, 0
        with path.open("rb") as stream:
            data = stream.read(min(remaining, IMAGE_LIMIT) + 1)
        if len(data) > min(remaining, IMAGE_LIMIT) or sha256(data).hexdigest() != fingerprint:
            return unavailable, 0
        if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            return unavailable, 0
        width, height = struct.unpack(">II", data[16:24])
        if not (1 <= width <= 4096 and 1 <= height <= 4096 and width * height <= 8_388_608):
            return unavailable, 0
        if metadata.get("width") != width or metadata.get("height") != height:
            return unavailable, 0
        title = escape(str(metadata.get("name", "Screenshot checkpoint"))[:128], quote=True)
        encoded = base64.b64encode(data).decode("ascii")
        return (
            f'<figure><img src="data:image/png;base64,{encoded}" width="{width}" '
            f'height="{height}" alt="{title}" loading="lazy"><figcaption>{title} '
            f"&middot; {width} &times; {height}</figcaption></figure>",
            len(data),
        )
    except (OSError, ValueError, RuntimeError):
        return unavailable, 0
