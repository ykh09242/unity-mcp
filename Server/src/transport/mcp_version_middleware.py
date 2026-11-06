"""Reject modern body metadata hidden behind a legacy HTTP version header."""

import json
from array import array
from collections.abc import Callable
from json.decoder import WHITESPACE
from typing import Final

from anyio.lowlevel import checkpoint
from fastmcp.server.auth.middleware import RequireAuthMiddleware
from fastmcp.server.http import StreamableHTTPASGIApp
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE

from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.routing import Route
from starlette._utils import get_route_path

from transport.json_decoder import JSONValue


def mcp_request_body_limit(app) -> Callable[[], int]:
    """Read the actual SDK route ceiling, including its lifespan-created manager."""
    for route in app.routes:
        if isinstance(route, Route) and route.path == app.state.path:
            endpoint = route.app.app if isinstance(route.app, RequireAuthMiddleware) else route.app
            if isinstance(endpoint, StreamableHTTPASGIApp):

                def current_limit(endpoint: StreamableHTTPASGIApp = endpoint) -> int:
                    manager = endpoint.session_manager
                    return (
                        manager.asgi_app.max_body_size
                        if manager is not None
                        else DEFAULT_MAX_REQUEST_BODY_SIZE
                    )

                return current_limit
    raise RuntimeError("Unsupported FastMCP streamable HTTP endpoint for ingress limiting")


_METADATA_PATHS = frozenset(
    {
        ("jsonrpc",),
        ("id",),
        ("method",),
        ("params",),
        ("params", "_meta"),
        ("params", "_meta", "io.modelcontextprotocol/protocolVersion"),
        ("params", "_meta", "io.modelcontextprotocol/clientCapabilities"),
    }
)
_METADATA_SCAN_STEPS: Final = 1024


async def _metadata(raw: bytearray) -> JSONValue:
    """Validate JSON iteratively, retaining only the fixed envelope field paths.

    The stdlib still owns scalar/escape/encoding semantics. Unknown object keys
    and array items are discarded as they are scanned, without a key memo or a
    container graph. Duplicate selected keys overwrite previous values normally.
    Container traversal stays in its owning request task and yields after bounded
    work, so cancellation exits the scan before the ingress reservation is freed.
    """
    await checkpoint()
    text = raw.decode(json.detect_encoding(raw), "surrogatepass")
    decoder = json.JSONDecoder()
    # Each unselected container costs one byte, even for deeply nested input.
    # Object states are first/next/after = 0/1/2; array states = 3/4/5.
    stack = array("B")
    selected_objects: dict[int, tuple[tuple[str, ...], dict[str, JSONValue]]] = {}
    index = 0

    def value(path: tuple[str, ...] | None) -> JSONValue:
        nonlocal index
        index = WHITESPACE.match(text, index).end()
        opening = text[index : index + 1]
        if opening in ("{", "["):
            index += 1
            stack.append(0 if opening == "{" else 3)
            if path is not None:
                if opening == "{":
                    selected: dict[str, JSONValue] = {}
                    selected_objects[len(stack)] = (path, selected)
                    return selected
                return []
            return None
        result, index = decoder.raw_decode(text, index)
        return result

    result = value(())
    steps = 0
    while stack:
        if steps == _METADATA_SCAN_STEPS:
            await checkpoint()
            steps = 0
        steps += 1
        state = stack[-1]
        is_object = state < 3
        index = WHITESPACE.match(text, index).end()
        token = text[index : index + 1]
        if token == ("}" if is_object else "]") and state in (0, 2, 3, 5):
            index += 1
            selected_objects.pop(len(stack), None)
            stack.pop()
            continue
        if state in (2, 5):
            if token != ",":
                raise ValueError("Expected JSON container delimiter")
            index += 1
            stack[-1] = 1 if is_object else 4
            continue
        key = ""
        if is_object:
            if token != '"':
                raise ValueError("Expected JSON object key")
            key, index = decoder.raw_decode(text, index)
            index = WHITESPACE.match(text, index).end()
            if text[index : index + 1] != ":":
                raise ValueError("Expected JSON key separator")
            index += 1
        selected_object = selected_objects.get(len(stack))
        path = (*selected_object[0], key) if selected_object is not None else None
        if path not in _METADATA_PATHS:
            path = None
        stack[-1] = 2 if is_object else 5
        item = value(path)
        if path is not None and selected_object is not None:
            selected_object[1][key] = item
    if WHITESPACE.match(text, index).end() != len(text):
        raise ValueError("Extra data after JSON value")
    return result


async def _mismatched_modern_id(raw: bytearray) -> str | int | None:
    """Inspect only a well-formed request; leave invalid envelopes to the SDK."""
    try:
        body = await _metadata(raw)
    except (ValueError, UnicodeError, RecursionError):
        return None
    match body:
        case {
            "jsonrpc": "2.0",
            "id": (str() | int()) as request_id,
            "method": str(),
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": str() as version,
                    "io.modelcontextprotocol/clientCapabilities": dict(),
                }
            },
        } if not isinstance(request_id, bool) and version in MODERN_PROTOCOL_VERSIONS:
            return request_id
    return None


class McpVersionMiddleware:
    """Bridge SDK 2.3's header-only era selection after auth and ingress limiting.

    Only known legacy-version POSTs at the configured MCP endpoint are inspected.
    A known modern body version must not evade HeaderMismatch validation by being
    routed to the SDK's legacy session path. All other traffic, including absent
    version headers, keeps the SDK's routing and validation semantics.
    """

    def __init__(self, app: ASGIApp, path: str) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or get_route_path(scope) != self.path
        ):
            await self.app(scope, receive, send)
            return
        versions = Headers(scope=scope).getlist("mcp-protocol-version")
        if len(versions) != 1 or versions[0] not in HANDSHAKE_PROTOCOL_VERSIONS:
            await self.app(scope, receive, send)
            return

        # The outer RequestBodyLimitMiddleware owns declared and actual byte limits.
        # Retain only body bytes: empty/tiny fragments must not accumulate Message
        # dictionaries independently of that bound. ASGI chunk boundaries do not
        # affect request semantics; preserve any terminating non-body signal once.
        body = bytearray()
        signal: Message | None = None
        replay_body = False
        complete = False
        try:
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    signal = message
                    del message
                    break
                body.extend(message.get("body", b""))
                replay_body = True
                complete = not message.get("more_body", False)
                del message
                if complete:
                    break
            request_id = await _mismatched_modern_id(body) if complete else None
            if request_id is not None:
                # Release the rejected body before potentially slow error delivery.
                body.clear()
                await JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32020,
                            "message": "MCP-Protocol-Version header does not match request metadata",
                        },
                    },
                    status_code=400,
                )(scope, receive, send)
                return

            async def replay_receive() -> Message:
                nonlocal replay_body, signal
                if replay_body:
                    replay_body = False
                    replayed: Message = {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": not complete,
                    }
                    body.clear()
                    return replayed
                if signal is not None:
                    replayed_signal = signal
                    signal = None
                    return replayed_signal
                return await receive()

            await self.app(scope, replay_receive, send)
        finally:
            body.clear()
            signal = None
