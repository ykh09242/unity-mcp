"""Explicit bounded assembly on the same connected MCP client that owns a handle."""

import base64
import hashlib
import json
import math
import re

import anyio

from models.response_limits import MAX_RESPONSE_BYTES

_PAGE_BYTES = 256 * 1024


async def assemble_response(
    call_tool, handle, *, max_bytes=1024 * 1024, max_pages=4, timeout_seconds=15, fields=None
):
    """Read and verify retained bytes, then optionally select top-level JSON fields.

    Pass an existing connected client's call_tool method and the original delivery
    handle. Limits are checked before retrieval and before decoding each page.
    Only read_response is called. The original result remains retained on failure.
    """
    if (
        type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_RESPONSE_BYTES
        or type(max_pages) is not int
        or not 1 <= max_pages <= 1024
        or type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 300
    ):
        raise ValueError("invalid_response_limits")
    if fields is not None and (
        not isinstance(fields, (list, tuple))
        or len(fields) > 64
        or any(not isinstance(field, str) or len(field) > 512 for field in fields)
    ):
        raise ValueError("invalid_response_fields")
    if not isinstance(handle, dict):
        raise ValueError("invalid_response_handle")
    response_id, digest, total = (
        handle.get("response_id"),
        handle.get("sha256"),
        handle.get("total_bytes"),
    )
    if (
        not isinstance(response_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", response_id) is None
        or not isinstance(digest, str)
        or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        or type(total) is not int
        or not 0 <= total <= max_bytes
        or max(1, (total + _PAGE_BYTES - 1) // _PAGE_BYTES) > max_pages
    ):
        raise ValueError("response_handle_exceeds_limits_or_is_invalid")
    payload = bytearray()
    with anyio.fail_after(timeout_seconds):
        for _ in range(max_pages):
            result = await call_tool(
                "read_response", {"response_id": response_id, "offset": len(payload)}
            )
            if not isinstance(result, dict):
                result = getattr(result, "structured_content", None)
            if not isinstance(result, dict) or result.get("success") is not True:
                raise ValueError("response_page_unavailable")
            page = result.get("data")
            if (
                not isinstance(page, dict)
                or page.get("response_id") != response_id
                or page.get("sha256") != digest
                or type(page.get("total_bytes")) is not int
                or page["total_bytes"] != total
                or type(page.get("offset")) is not int
                or page["offset"] != len(payload)
                or page.get("encoding") != "base64"
                or page.get("json_encoding") != "utf-8"
            ):
                raise ValueError("response_page_identity_mismatch")
            encoded = page.get("data_base64")
            expected = min(_PAGE_BYTES, total - len(payload))
            if not isinstance(encoded, str) or len(encoded) > 4 * ((expected + 2) // 3):
                raise ValueError("response_page_exceeds_limits")
            chunk = base64.b64decode(encoded, validate=True)
            end = len(payload) + len(chunk)
            next_offset = page.get("next_offset")
            if (
                len(chunk) != expected
                or (end < total and (type(next_offset) is not int or next_offset != end))
                or (end == total and next_offset is not None)
            ):
                raise ValueError("response_page_sequence_mismatch")
            payload.extend(chunk)
            if end == total:
                break
    if len(payload) != total or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("response_digest_mismatch")
    value = json.loads(payload.decode("utf-8"))
    if fields is None:
        return value
    if not isinstance(value, dict) or any(field not in value for field in fields):
        raise ValueError("response_field_unavailable")
    return {field: value[field] for field in fields}
