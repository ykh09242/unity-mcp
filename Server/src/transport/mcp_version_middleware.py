"""Reject modern body metadata hidden behind a legacy HTTP version header."""

import json

from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _mismatched_modern_id(raw: bytearray) -> str | int | None:
    """Inspect only a well-formed request; leave invalid envelopes to the SDK."""
    try:
        body = json.loads(raw)
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
            or scope["path"].removeprefix(scope.get("root_path", "")) != self.path
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
            request_id = _mismatched_modern_id(body) if complete else None
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
