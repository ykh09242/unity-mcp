"""Read one bounded local JSON evidence document with no server dependencies."""

import json
import math
import os
import stat
from pathlib import Path
from typing import Final, TypeAlias

REPORT_LIMIT: Final = 16 * 1024 * 1024
JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None


class QueryComparisonError(ValueError):
    """The supplied evidence cannot establish a comparable pair of completed runs."""


def _object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _reject_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON constant")


def _check_depth(encoded: str) -> None:
    """Bound nesting before JSON recursion, respecting escaped characters inside strings."""
    depth, quoted, escaped = 0, False, False
    for char in encoded:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("JSON nesting exceeds 32 levels")
        elif char in "]}":
            depth -= 1


def _reject_remote_namespace(source: Path) -> None:
    """Reject Windows remote/device syntax before even a filesystem metadata lookup."""
    if os.fspath(source).replace("\\", "/").startswith(("//", "/??/")):
        raise ValueError("Remote or device input namespace")


def read_report(source: Path, label: str) -> dict[str, JsonValue]:
    """Read one bounded regular file; never follow links or paths embedded in reports."""
    try:
        _reject_remote_namespace(source)
        path = Path(os.path.abspath(source))
        _reject_remote_namespace(path)
        for ancestor in (*reversed(path.parents), path):
            attributes = ancestor.lstat()
            if stat.S_ISLNK(attributes.st_mode) or (
                getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("Linked or reparse input path")
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > REPORT_LIMIT:
            raise ValueError("Input is not a regular file within 16 MiB")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise ValueError("Input changed before reading")
            if opened.st_size > REPORT_LIMIT:
                raise ValueError("Opened input exceeds 16 MiB")
            encoded = stream.read(opened.st_size + 1)
        if len(encoded) != opened.st_size:
            raise ValueError("Input changed while reading")
        decoded = encoded.decode("utf-8-sig")
        _check_depth(decoded)
        value = json.loads(
            decoded,
            object_pairs_hook=_object,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
        if not isinstance(value, dict):
            raise ValueError("A single native report object is required")
        return value
    except (OSError, ValueError, RecursionError) as exc:
        raise QueryComparisonError(
            f"{label} report must be a readable, unlinked regular UTF-8 JSON file "
            "within 16 MiB and 32 levels"
        ) from exc
