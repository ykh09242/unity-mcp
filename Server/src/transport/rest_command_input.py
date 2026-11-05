"""Admit local REST command JSON before expanding it into Python containers."""

from codecs import getincrementaldecoder
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import IntEnum
from json import detect_encoding, loads
from sys import getsizeof
from threading import Lock

from starlette.requests import Request
from starlette.responses import JSONResponse

from models.response_limits import MAX_RESPONSE_RETAINED_BYTES, bounded_json_profile
from core.config import positive_http_limit
from services.tools.response_work import response_work
from services.execution_receipt_identity import ReceiptOptions
from transport.execution_receipts import rest_receipt_options
from transport.local_project_context import ContextCommand, ReceiptCommand
from transport.plugin_hub import PluginHub
from transport.request_body_limit_middleware import MAX_HTTP_REQUEST_BYTES


class InputFailure(IntEnum):
    INVALID = 400
    LIMIT = 413
    BUSY = 503

    def response(self) -> JSONResponse:
        return JSONResponse(
            {
                "success": False,
                "error": {
                    self.INVALID: "Invalid REST command envelope",
                    self.LIMIT: "REST command input exceeds supported limits",
                    self.BUSY: "REST command input capacity is exhausted",
                }[self],
                "data": {"dispatch": "not_dispatched"},
            },
            status_code=self.value,
        )


@dataclass(frozen=True, slots=True)
class ReceiptedCommand:
    body: ReceiptCommand | ContextCommand
    options: ReceiptOptions


RestEnvelope = dict | ContextCommand | ReceiptedCommand


def _invalid_constant(_: str):
    raise ValueError("invalid JSON constant")


def _scan_source(
    raw: bytes, reserve: Callable[[int], InputFailure | None]
) -> bytes | str | InputFailure:
    encoding = detect_encoding(raw)
    if encoding == "utf-8" and raw.isascii():
        return raw
    # A late multibyte character can widen an entire decoder buffer. Decode in
    # bounded pieces so queued or ordinary Unicode inputs need no worst-case
    # whole-body widening reservation. Charge the pieces and join before allocation.
    decoder = getincrementaldecoder(encoding)()
    pieces = []
    raw_size = getsizeof(raw)
    occupied = raw_size + 256
    chars = 0
    view = memoryview(raw)
    for start in range(0, len(raw), 65536):
        end = min(start + 65536, len(raw))
        failure = reserve(occupied + 16 * (end - start) + 4096)
        if failure:
            return failure
        piece = decoder.decode(view[start:end], final=end == len(raw))
        pieces.append(piece)
        occupied += getsizeof(piece) + 16
        chars += len(piece)
    failure = reserve(occupied + 4 * chars + 256)
    if failure:
        return failure
    text = "".join(pieces)
    pieces.clear()
    # The profile recounts UTF-8 sizes with at most a 64-Ki-character slice
    # and its encoding alive at once.
    failure = reserve(raw_size + getsizeof(text) + 8 * min(chars, 65536) + 4096)
    return failure or text


def _decode_envelope(text: str, guarded: bool) -> RestEnvelope | InputFailure:
    try:
        body = loads(text, parse_constant=_invalid_constant)
        if not isinstance(body, dict):
            return InputFailure.INVALID
        name = body.get("type")
        params = body.get("params", {})
        target = body.get("unity_instance")
        if (
            not isinstance(name, str)
            or len(name) > 256
            or not name.strip()
            or not isinstance(params, dict)
            or target is not None
            and (not isinstance(target, str) or not 0 < len(target) <= 256)
        ):
            return InputFailure.INVALID
        if name == "execute_custom_tool":
            tool_name = params.get("tool_name") or params.get("name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                return InputFailure.INVALID
            for key in ("parameters", "params"):
                if key in params and not isinstance(params[key], dict):
                    return InputFailure.INVALID
        if "execution" in body:
            validated = (ContextCommand if guarded else ReceiptCommand).model_validate(body)
            return ReceiptedCommand(validated, rest_receipt_options(validated))
        if guarded and name != "get_execution_receipt":
            return ContextCommand.model_validate(body)
        return body
    except (ValueError, RecursionError):
        # Decoder and validation exceptions retain the rejected text/graph.
        return InputFailure.INVALID


class RestCommandInput:
    def __init__(self):
        self.max_request = positive_http_limit(
            "REST_PARSE_REQUEST_BYTES", MAX_RESPONSE_RETAINED_BYTES
        )
        self.max_total = positive_http_limit(
            "REST_PARSE_TOTAL_BYTES", 2 * MAX_RESPONSE_RETAINED_BYTES
        )
        self.retained_bytes = 0
        self._lock = Lock()

    @asynccontextmanager
    async def parse(
        self, request: Request, *, guarded: bool
    ) -> AsyncIterator[RestEnvelope | InputFailure]:
        raw = await request.body()
        if len(raw) > MAX_HTTP_REQUEST_BYTES:
            yield InputFailure.LIMIT
            return
        reserved = 0

        def reserve(total: int) -> InputFailure | None:
            nonlocal reserved
            if total > self.max_request:
                return InputFailure.LIMIT
            with self._lock:
                change = total - reserved
                if change > self.max_total - self.retained_bytes:
                    return InputFailure.BUSY
                self.retained_bytes += change
                reserved = total
            return None

        def decode() -> RestEnvelope | InputFailure:
            try:
                # Preserve the byte encodings accepted by the previous Request.json().
                source = _scan_source(raw, reserve)
                if isinstance(source, InputFailure):
                    return source
                profile = bounded_json_profile(
                    source,
                    max_bytes=MAX_HTTP_REQUEST_BYTES,
                    max_depth=PluginHub.MAX_COMMAND_PAYLOAD_DEPTH,
                    max_nodes=PluginHub.MAX_COMMAND_PAYLOAD_NODES,
                    profile_string_widening=True,
                )
            except UnicodeError:
                return InputFailure.INVALID
            if profile is None:
                return InputFailure.LIMIT
            if not profile.complete_strings:
                return InputFailure.INVALID
            # All decoded strings can use four bytes per character. For the one
            # string currently widening, allow its pieces and overlapping old/new
            # writer allocations (including overallocation) before calling loads.
            graph = (
                getsizeof(raw)
                + getsizeof(profile.text)
                + 4 * len(profile.text)
                + 512 * profile.nodes
                + 256
            )
            failure = reserve(graph + 12 * profile.max_widening_string)
            if failure is not None:
                return failure
            body = _decode_envelope(profile.text, guarded)
            reserve(graph)  # The decoder's transient string buffers have gone.
            return body

        try:
            failure = reserve(5 * len(raw) + 256)
            if failure is not None:
                yield failure
                return
            yield await response_work(decode, large=len(raw) >= 64 * 1024)
        finally:
            # response_work drains a started worker even under repeated cancellation.
            with self._lock:
                self.retained_bytes -= reserved
