"""Shared pre-serialization bounds for plugin and consumer JSON responses."""
from __future__ import annotations

import json
import math
import sys
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, AnyUrl

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_DEPTH = 64
MAX_RESPONSE_NODES = 100_000
MAX_RESPONSE_RETAINED_BYTES = 256 * 1024 * 1024


@dataclass(slots=True)
class ResponseOwner:
    """Own result reservations through a request task's actual response cleanup."""
    entries: list[tuple[dict, str]] = field(default_factory=list)
    on_release: list[Callable[[], None]] = field(default_factory=list)

    def release(self) -> None:
        for mapping, key in self.entries:
            mapping.pop(key, None)
        self.entries.clear()
        for callback in self.on_release:
            callback()
        self.on_release.clear()


response_owner: ContextVar[ResponseOwner | None] = ContextVar("unity_response_owner", default=None)


def response_limit_error(reason: str = "response_payload_limit") -> dict[str, Any]:
    """Return a constant-sized error without retaining or echoing rejected data."""
    return {"success": False, "error": "Unity response exceeds supported limits",
            "data": {"reason": reason}}


def response_size(value: Any, *, max_bytes: int = MAX_RESPONSE_BYTES,
                  max_depth: int = MAX_RESPONSE_DEPTH,
                  max_nodes: int = MAX_RESPONSE_NODES,
                  max_retained: int = MAX_RESPONSE_RETAINED_BYTES) -> int | None:
    """Bound traversal and JSON serialization; return a conservative memory charge.

    The estimate includes Python objects, response copies and JSON working space.
    Container cycles, non-JSON values and nonfinite numbers fail closed. The
    bounded first pass precedes encoder allocation, including large scalar chunks.
    """
    retained = 1024
    nodes = 0

    def visit(item: Any, depth: int) -> bool:
        nonlocal retained, nodes
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            return False
        retained += 128 + 2 * sys.getsizeof(item)
        if retained > max_retained:
            return False
        if isinstance(item, BaseModel):
            return visit(item.__dict__, depth)
        if isinstance(item, AnyUrl):
            return visit(str(item), depth)
        if isinstance(item, str):
            if len(item) > max_bytes:
                return False
            retained += 4 * len(item)
            return retained <= max_retained
        if isinstance(item, dict):
            return all(isinstance(key, str) and visit(key, depth + 1)
                       and visit(child, depth + 1) for key, child in item.items())
        if isinstance(item, (list, tuple)):
            return all(visit(child, depth + 1) for child in item)
        if isinstance(item, float):
            return math.isfinite(item)
        if isinstance(item, int):
            # Avoid arbitrarily large integer-to-decimal conversions.
            return item.bit_length() <= 14_000
        return item is None

    if not visit(value, 0):
        return None
    encoded_bytes = 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False,
                                      default=lambda model: model.__dict__ if isinstance(model, BaseModel) else str(model)).iterencode(value):
            encoded_bytes += len(chunk.encode("utf-8"))
            if encoded_bytes > max_bytes:
                return None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None
    retained += encoded_bytes
    return retained if retained <= max_retained else None


def bound_response(value: Any) -> Any:
    """Apply an independent bound before normalization or final serialization."""
    return response_limit_error() if response_size(value) is None else value


def bounded_json_text(raw: str | bytes, *, max_bytes: int, max_depth: int,
                      max_nodes: int) -> str | None:
    """Reject large, deep or wide raw frames before allocating a decoded graph.

    Count containers and scalar starts outside strings. Skip long quoted values
    with string searches so base64 previews do not require a Python loop per byte.
    Full syntax validation remains the JSON decoder's responsibility.
    """
    if len(raw) > max_bytes:
        return None
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if sum(len(text[offset:offset + 65_536].encode("utf-8"))
               for offset in range(0, len(text), 65_536)) > max_bytes:
            return None
    except UnicodeError:
        return None
    index = depth = nodes = 0
    scalar = False
    while index < len(text):
        char = text[index]
        if char == '"':
            nodes += 1
            end = index + 1
            while True:
                end = text.find('"', end)
                if end < 0:
                    return None
                slash = end - 1
                while slash > index and text[slash] == "\\":
                    slash -= 1
                if (end - slash - 1) % 2 == 0:
                    break
                end += 1
            index = end
            scalar = False
        elif char in "[{":
            depth += 1
            nodes += 1
            scalar = False
            if depth > max_depth:
                return None
        elif char in "]}":
            depth -= 1
            scalar = False
        elif char in ",:" or char.isspace():
            scalar = False
        elif not scalar:
            nodes += 1
            scalar = True
        if nodes > max_nodes:
            return None
        index += 1
    return text
