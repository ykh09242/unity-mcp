"""Shared decoding and line-window rendering for Unity script reads."""

import base64
from typing import Any

from cli.utils.output import format_output


def script_contents(data: dict[str, Any]) -> str | None:
    """Read plain or base64 content, preserving a valid empty source."""
    contents = data.get("contents")
    if isinstance(contents, str):
        return contents
    if data.get("contentsEncoded") and isinstance(data.get("encodedContents"), str):
        try:
            return base64.b64decode(data["encodedContents"], validate=True).decode(
                "utf-8", "replace"
            )
        except ValueError:
            return None
    return None


def format_script_read(
    result: dict[str, Any], format_type: str, start_line: int | None, line_count: int | None
) -> str:
    """Render native windows, falling back to local slicing for older peers."""
    data = result.get("data")
    if not result.get("success") or not isinstance(data, dict):
        return format_output(result, format_type)
    contents = script_contents(data)
    if contents is None:
        return format_output(result, format_type)
    if not data.get("lineWindowApplied") and (start_line is not None or line_count is not None):
        start = (start_line or 1) - 1
        end = start + line_count if line_count is not None else None
        contents = "".join(contents.splitlines(keepends=True)[start:end])
    if format_type != "json":
        return contents
    data = {**data, "contents": contents}
    if data.get("contentsEncoded"):
        data["encodedContents"] = base64.b64encode(contents.encode("utf-8")).decode("ascii")
    return format_output({**result, "data": data}, format_type)
