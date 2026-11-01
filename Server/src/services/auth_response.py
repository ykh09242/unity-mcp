"""Bounded wire decoding and JSON validation for external authentication responses."""

from dataclasses import dataclass
import json
import math
import zlib

import httpx
from pydantic import JsonValue


class AuthResponseError(ValueError):
    """A provider response violates the supported authentication contract."""


@dataclass(frozen=True, slots=True)
class AuthResponse:
    valid: bool
    user_id: str | None
    metadata: dict[str, JsonValue] | None
    error: str | None


async def read_auth_body(response: httpx.Response, max_bytes: int) -> bytes:
    """Cap encoded input before decoding; never invoke HTTPX automatic inflation."""
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip", "deflate"}:
        raise AuthResponseError("unsupported content encoding")
    body = bytearray()
    async for chunk in response.aiter_raw():
        if len(chunk) > max_bytes - len(body):
            raise AuthResponseError("response byte limit exceeded")
        body.extend(chunk)
    if encoding == "identity":
        return bytes(body)
    try:
        inflater = zlib.decompressobj(31 if encoding == "gzip" else zlib.MAX_WBITS)
        try:
            decoded = inflater.decompress(body, max_bytes + 1)
        except zlib.error:
            if encoding != "deflate":
                raise
            # Match HTTPX compatibility with providers emitting raw DEFLATE.
            inflater = zlib.decompressobj(-zlib.MAX_WBITS)
            decoded = inflater.decompress(body, max_bytes + 1)
    except zlib.error as exc:
        raise AuthResponseError("invalid compressed response") from exc
    if len(decoded) > max_bytes:
        raise AuthResponseError("decoded response byte limit exceeded")
    if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise AuthResponseError("incomplete or trailing compressed response")
    return decoded


def parse_auth_body(body: bytes, metadata_max_bytes: int) -> AuthResponse:
    """Accept only bounded JSON objects with typed authentication fields."""
    try:
        data: JsonValue = json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AuthResponseError("invalid JSON") from exc
    work = [(data, 0)]
    nodes = 0
    while work:
        value, depth = work.pop()
        nodes += 1
        if nodes > 4096 or depth > 32:
            raise AuthResponseError("JSON graph limit exceeded")
        if isinstance(value, dict):
            work.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            work.extend((child, depth + 1) for child in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise AuthResponseError("nonfinite JSON number")
    if not isinstance(data, dict) or type(data.get("valid")) is not bool:
        raise AuthResponseError("invalid validation verdict")
    user_id = data.get("user_id")
    metadata = data.get("metadata")
    error = data.get("error", "Invalid API key")
    if user_id is not None and not isinstance(user_id, str):
        raise AuthResponseError("invalid user ID")
    if metadata is not None and not isinstance(metadata, dict):
        raise AuthResponseError("invalid metadata")
    if error is not None and not isinstance(error, str):
        raise AuthResponseError("invalid error")
    try:
        # Compact UTF-8 size is independent of provider whitespace/escape choices.
        encoded = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > metadata_max_bytes:
            raise AuthResponseError("metadata byte limit exceeded")
    except (ValueError, UnicodeError) as exc:
        raise AuthResponseError("invalid metadata encoding or size") from exc
    return AuthResponse(data["valid"], user_id, metadata, error)
