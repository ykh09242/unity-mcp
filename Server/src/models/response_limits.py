"""Shared pre-serialization bounds for plugin and consumer JSON responses."""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Iterator, MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, AnyUrl
from pydantic_core import SchemaSerializer, to_json

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_DEPTH = 64
MAX_RESPONSE_NODES = 100_000
MAX_RESPONSE_RETAINED_BYTES = 256 * 1024 * 1024
# Limit whole-document encoding to a small, conservatively bounded allocation.
_MAX_FAST_JSON_BYTES = 2 * 1024 * 1024
_MIN_LARGE_ASCII_CHARS = 256 * 1024
_ASCII_JSON_CHUNK_CHARS = 4096


@dataclass(slots=True)
class ResponseOwner:
    """Own result reservations through a request task's actual response cleanup."""

    entries: list[tuple[MutableMapping, str]] = field(default_factory=list)
    on_release: list[Callable[[], None]] = field(default_factory=list)
    copy_reservations: list[Callable[[ResponseOwner], bool]] = field(default_factory=list)
    released: bool = False

    def reserve_copy(self) -> ResponseOwner | None:
        """Admit every detached copy before allocation, rolling back on refusal."""
        if self.released or len(self.copy_reservations) != len(self.entries):
            return None
        copy_owner = ResponseOwner()
        for reserve in self.copy_reservations:
            if not reserve(copy_owner):
                copy_owner.release()
                return None
        return copy_owner

    def adopt(self, other: ResponseOwner) -> bool:
        """Move copy reservations to the consumer's existing delivery lifetime."""
        if self.released or other.released:
            other.release()
            return False
        self.entries.extend(other.entries)
        self.copy_reservations.extend(other.copy_reservations)
        self.on_release.extend(other.on_release)
        other.entries.clear()
        other.copy_reservations.clear()
        other.on_release.clear()
        other.released = True
        return True

    def release(self) -> None:
        self.released = True
        for mapping, key in self.entries:
            mapping.pop(key, None)
        self.entries.clear()
        self.copy_reservations.clear()
        for callback in self.on_release:
            callback()
        self.on_release.clear()


response_owner: ContextVar[ResponseOwner | None] = ContextVar("unity_response_owner", default=None)


def response_limit_error(reason: str = "response_payload_limit") -> dict[str, Any]:
    """Return a constant-sized error without retaining or echoing rejected data."""
    return {
        "success": False,
        "error": "Unity response exceeds supported limits",
        "data": {"reason": reason},
    }


def _model_wire_is_safe(model: BaseModel) -> bool:
    """Refuse extension hooks before a serializer can allocate an unbounded value."""
    model_type = type(model)
    decorators = model_type.__pydantic_decorators__
    if (
        model_type.model_dump is not BaseModel.model_dump
        or getattr(model_type.__get_pydantic_core_schema__, "__func__", None)
        is not BaseModel.__get_pydantic_core_schema__.__func__
        or type(model.__pydantic_serializer__) is not SchemaSerializer
        or decorators.field_serializers
        or decorators.model_serializers
        or decorators.computed_fields
    ):
        return False
    pending = [model_type.__pydantic_core_schema__]
    nodes = 0
    while pending:
        node = pending.pop()
        nodes += 1
        if nodes > MAX_RESPONSE_NODES:
            return False
        if type(node) is dict:
            if len(node) + len(pending) + nodes > MAX_RESPONSE_NODES:
                return False
            serializer = node.get("serialization")
            if serializer is not None:
                # Native Pydantic URL fields are present in standard MCP resource
                # models. Their serializer cannot expand an already bounded URL.
                function = serializer.get("function") if type(serializer) is dict else None
                owner = getattr(function, "__self__", None)
                if (
                    getattr(function, "__func__", None) is not AnyUrl.serialize_url.__func__
                    or getattr(owner, "__module__", None) != "pydantic.networks"
                ):
                    return False
            if node.get("computed_fields") or node.get("serialization_exclude_if"):
                return False
            pending.extend(
                child
                for key, child in node.items()
                if key not in {"metadata", "function", "serialization", "config"}
                and type(child) in (dict, list)
            )
        elif type(node) is list:
            if len(node) + len(pending) + nodes > MAX_RESPONSE_NODES:
                return False
            pending.extend(node)
    return True


def _model_wire_items(model: BaseModel) -> Iterator[tuple[str, Any]]:
    """Inspect emitted names and stored values without calling model properties."""
    for name, model_field in type(model).model_fields.items():
        value = model.__dict__.get(name)
        if model_field.exclude or value is None:
            continue
        yield model_field.serialization_alias or model_field.alias or name, value
    if model.__pydantic_extra__:
        # exclude_none applies to declared fields, but SDK dumps retain null extras.
        yield from model.__pydantic_extra__.items()


def _native_url_is_safe(value: AnyUrl) -> bool:
    """Accept native URL serialization without invoking a subclass string hook."""
    return type(value).__module__ == "pydantic.networks" and type(value).__str__ is AnyUrl.__str__


def _large_ascii_json_size(
    value: object, *, max_bytes: int, max_depth: int, max_nodes: int
) -> int | None:
    """Size exact builtin graphs using bounded native ASCII string chunks.

    The original visitor proves retained bounds and whole-graph eligibility
    first. Native serialization receives only exact strings, never numbers or
    models; their original representation and fallback semantics remain intact.
    Return max_bytes + 1 for proven overflow, None for unsupported/failed sizing.
    """
    total = nodes = 0
    overflow = False

    def visit(item: object, depth: int) -> bool:
        nonlocal total, nodes, overflow
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            return False
        item_type = type(item)
        if item_type is str:
            if not item.isascii():
                return False
            total += 2
            for offset in range(0, len(item), _ASCII_JSON_CHUNK_CHARS):
                total += (
                    len(
                        to_json(item[offset : offset + _ASCII_JSON_CHUNK_CHARS], ensure_ascii=False)
                    )
                    - 2
                )
                if total > max_bytes:
                    overflow = True
                    return False
        elif item_type is dict:
            total += 2 + 2 * len(item) + 2 * max(0, len(item) - 1)
            for key, child in item.items():
                if type(key) is not str or not visit(key, depth + 1) or not visit(child, depth + 1):
                    return False
        elif item_type is list or item_type is tuple:
            total += 2 + 2 * max(0, len(item) - 1)
            for child in item:
                if not visit(child, depth + 1):
                    return False
        elif item_type is bool:
            total += 4 if item else 5
        elif item is None:
            total += 4
        elif item_type is int:
            if item.bit_length() > 14_000:
                return False
            total += len(int.__repr__(item))
        elif item_type is float:
            if not math.isfinite(item):
                return False
            total += len(float.__repr__(item))
        else:
            return False
        if total > max_bytes:
            overflow = True
            return False
        return True

    try:
        if visit(value, 0):
            return total
        return max_bytes + 1 if overflow else None
    except (ValueError, RecursionError):
        return None


def response_size(
    value: Any,
    *,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_depth: int = MAX_RESPONSE_DEPTH,
    max_nodes: int = MAX_RESPONSE_NODES,
    max_retained: int = MAX_RESPONSE_RETAINED_BYTES,
) -> int | None:
    """Bound traversal and JSON serialization; return a conservative memory charge.

    The estimate includes Python objects, response copies and JSON working space.
    Container cycles, non-JSON values and nonfinite numbers fail closed. The
    bounded first pass precedes encoder allocation, including large scalar chunks.
    """
    retained = 1024
    nodes = 0
    encoded_bound: int | None = 0
    fast_limit = min(_MAX_FAST_JSON_BYTES, max_bytes)
    has_large_ascii = False
    exact_ascii_graph = True

    storage_nodes = 0
    storage_covered = False

    def retain_storage(item: Any, depth: int) -> bool:
        """Charge original model storage independently of the emitted node budget."""
        nonlocal retained, storage_nodes
        storage_nodes += 1
        if depth > max_depth or storage_nodes > max_nodes:
            return False
        retained += 128 + 2 * sys.getsizeof(item)
        if retained > max_retained:
            return False
        if isinstance(item, str):
            retained += 4 * len(item)
            return retained <= max_retained
        if isinstance(item, dict):
            return all(
                isinstance(key, str)
                and retain_storage(key, depth + 1)
                and retain_storage(child, depth + 1)
                for key, child in item.items()
            )
        if isinstance(item, (list, tuple)):
            return all(retain_storage(child, depth + 1) for child in item)
        if isinstance(item, BaseModel):
            return (
                retain_storage(item.__dict__, depth)
                and (
                    item.__pydantic_extra__ is None
                    or retain_storage(item.__pydantic_extra__, depth)
                )
                and (
                    item.__pydantic_private__ is None
                    or retain_storage(item.__pydantic_private__, depth)
                )
            )
        if isinstance(item, AnyUrl):
            return _native_url_is_safe(item) and retain_storage(str(item), depth)
        if isinstance(item, float):
            return math.isfinite(item)
        if isinstance(item, int):
            return item.bit_length() <= 14_000
        return item is None

    def visit(item: Any, depth: int) -> bool:
        nonlocal retained, nodes, encoded_bound, has_large_ascii, exact_ascii_graph, storage_covered
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            return False
        retained += 128 + 2 * sys.getsizeof(item)
        if retained > max_retained:
            return False
        item_type = type(item)
        if encoded_bound is not None:
            # Only exact builtins have predictable length/serialization. Models,
            # URLs and subclasses keep the existing streaming encoder behavior.
            if item_type is str:
                encoded_bound += 6 * len(item) + 2
            elif item_type is int:
                # 30103/100000 > log10(2); include the sign and zero.
                encoded_bound += item.bit_length() * 30103 // 100000 + 2
            elif item_type is float:
                encoded_bound += 32
            elif item_type is bool or item is None:
                encoded_bound += 5
            elif item_type is dict:
                encoded_bound += 2 + 4 * len(item)
            elif item_type is list or item_type is tuple:
                encoded_bound += 2 + 2 * len(item)
            else:
                encoded_bound = None
            if encoded_bound is not None and encoded_bound > fast_limit:
                encoded_bound = None
        # Common JSON nodes need no model/URL/subclass dispatch. Keep the
        # original ordered fallback below for user-defined types and their hooks.
        if item_type is str:
            length = len(item)
            if length > max_bytes:
                return False
            retained += 4 * length
            if retained > max_retained:
                return False
            ascii_only = item.isascii()
            if not ascii_only:
                exact_ascii_graph = False
            if length >= _MIN_LARGE_ASCII_CHARS and ascii_only:
                has_large_ascii = True
            return True
        if item_type is dict:
            return all(
                isinstance(key, str) and visit(key, depth + 1) and visit(child, depth + 1)
                for key, child in item.items()
            )
        if item_type is list or item_type is tuple:
            return all(visit(child, depth + 1) for child in item)
        if item_type is float:
            return math.isfinite(item)
        if item_type is int:
            return item.bit_length() <= 14_000
        if item_type is bool or item is None:
            return True
        exact_ascii_graph = False
        if isinstance(item, BaseModel):
            if not _model_wire_is_safe(item):
                return False
            # Excluded fields do not consume wire bytes, but their original
            # objects still remain retained until normalization/delivery ends.
            if not storage_covered:
                if not retain_storage(item.__dict__, depth):
                    return False
                if item.__pydantic_extra__ is not None and not retain_storage(
                    item.__pydantic_extra__, depth
                ):
                    return False
                if item.__pydantic_private__ is not None and not retain_storage(
                    item.__pydantic_private__, depth
                ):
                    return False
            previous_coverage = storage_covered
            storage_covered = True
            try:
                return all(
                    visit(key, depth + 1) and visit(child, depth + 1)
                    for key, child in _model_wire_items(item)
                )
            finally:
                storage_covered = previous_coverage
        if isinstance(item, AnyUrl):
            return _native_url_is_safe(item) and visit(str(item), depth)
        if isinstance(item, str):
            if len(item) > max_bytes:
                return False
            retained += 4 * len(item)
            return retained <= max_retained
        if isinstance(item, dict):
            return all(
                isinstance(key, str) and visit(key, depth + 1) and visit(child, depth + 1)
                for key, child in item.items()
            )
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
        if has_large_ascii and exact_ascii_graph:
            exact_bytes = _large_ascii_json_size(
                value, max_bytes=max_bytes, max_depth=max_depth, max_nodes=max_nodes
            )
            if exact_bytes is not None:
                if exact_bytes > max_bytes:
                    return None
                retained += exact_bytes
                return retained if retained <= max_retained else None
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            default=lambda model: (
                BaseModel.model_dump(model, by_alias=True, mode="json", exclude_none=True)
                if isinstance(model, BaseModel)
                else str(model)
            ),
        )
        # Leave room for strings/buffers and older C encoders' temporary chunks
        # and item tuples. A conservative bound selects a path, never a rejection.
        if (
            encoded_bound is not None
            and 8 * encoded_bound + 128 * nodes + 4096 <= max_retained - retained
        ):
            encoded = encoder.encode(value)
            encoded_bytes = (
                len(encoded)
                if type(encoded) is str and encoded.isascii()
                else len(encoded.encode("utf-8"))
            )
        else:
            for chunk in encoder.iterencode(value):
                # JSON encoder chunks are strings; ASCII bytes equal their length.
                encoded_bytes += len(chunk) if chunk.isascii() else len(chunk.encode("utf-8"))
                if encoded_bytes > max_bytes:
                    return None
        if encoded_bytes > max_bytes:
            return None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None
    retained += encoded_bytes
    return retained if retained <= max_retained else None


def bound_response(value: Any) -> Any:
    """Apply an independent bound before normalization or final serialization."""
    return response_limit_error() if response_size(value) is None else value


def bounded_json_text(
    raw: str | bytes | bytearray, *, max_bytes: int, max_depth: int, max_nodes: int
) -> str | None:
    """Reject large, deep or wide raw frames before allocating a decoded graph.

    Count containers and scalar starts outside strings. Skip long quoted values
    with string searches so base64 previews do not require a Python loop per byte.
    Full syntax validation remains the JSON decoder's responsibility.
    """
    if len(raw) > max_bytes:
        return None
    try:
        raw_type = type(raw)
        exact_bytes = raw_type is bytes or raw_type is bytearray
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        # Strict built-in decoding or exact ASCII proves the checked byte length.
        # Subclasses retain recounting because their hooks may change the content.
        known_byte_length = exact_bytes or (raw_type is str and text.isascii())
        if (
            not known_byte_length
            and sum(
                len(text[offset : offset + 65_536].encode("utf-8"))
                for offset in range(0, len(text), 65_536)
            )
            > max_bytes
        ):
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
