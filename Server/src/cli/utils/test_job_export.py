"""Stream a validated test export into an explicit create-new local artifact."""

import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4
from collections.abc import Callable

from cli.utils.play_scenario_analysis_io import AnalysisError, safe_local_path


def write_export(output: Path, fetch: Callable[[str | None], dict]) -> Path:
    path = safe_local_path(output)
    if path.exists():
        raise AnalysisError("Export destination already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe_local_path(path.with_name("." + path.name + "." + uuid4().hex + ".tmp"))
    digest = hashlib.sha256()
    cursor = None
    identity = None
    count = 0
    seen = set()
    try:
        with temporary.open("xb") as stream:
            while True:
                reply = fetch(cursor)
                if not reply.get("success"):
                    raise AnalysisError("Native test export is unavailable")
                chunk = reply.get("data", {})
                current = (chunk.get("generation"), chunk.get("sha256"), chunk.get("total_bytes"))
                if (
                    not isinstance(current[0], str)
                    or not isinstance(current[1], str)
                    or not re.fullmatch("[a-f0-9]{64}", current[1])
                    or type(current[2]) is not int
                    or not 0 <= current[2] <= 32 * 1024 * 1024
                    or identity is not None
                    and current != identity
                ):
                    raise AnalysisError("Test export identity changed or is invalid")
                identity = current
                text = chunk.get("content")
                if not isinstance(text, str):
                    raise AnalysisError("Invalid test export chunk")
                encoded = text.encode("utf-8", errors="strict")
                count += len(encoded)
                if len(encoded) > 65536 or count > current[2]:
                    raise AnalysisError("Test export exceeds its byte limit")
                stream.write(encoded)
                digest.update(encoded)
                next_cursor = chunk.get("next_cursor")
                if chunk.get("has_more") is False:
                    if next_cursor is not None:
                        raise AnalysisError("Invalid terminal export cursor")
                    break
                if (
                    chunk.get("has_more") is not True
                    or not encoded
                    or not isinstance(next_cursor, str)
                    or len(next_cursor) > 2048
                    or len(seen) >= 1024
                    or next_cursor in seen
                ):
                    raise AnalysisError("Invalid test export cursor")
                seen.add(next_cursor)
                cursor = next_cursor
            if count != identity[2] or digest.hexdigest() != identity[1]:
                raise AnalysisError("Test export byte count or digest mismatch")
        safe_local_path(path)
        safe_local_path(temporary)
        # Hard-link publication fails if a destination appeared; it never overwrites it.
        os.link(temporary, path)
        temporary.unlink()
        return path
    finally:
        if temporary.exists():
            safe_local_path(temporary).unlink()
