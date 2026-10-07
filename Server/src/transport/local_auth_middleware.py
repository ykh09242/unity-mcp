"""Authenticate native local HTTP and WebSocket clients before routing or parsing."""

import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.local_auth import LOCAL_AUTH_HEADER


class LocalControlAuthMiddleware:
    """Require a launch token on every local control request and plugin upgrade."""

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self._token = token.encode("utf-8") if token else b""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Health is a public liveness probe, with no session or credential data.
        if scope["type"] == "http" and scope["method"] == "GET" and scope["path"] == "/health":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        status = 0
        error = ""
        # Native clients do not send browser provenance headers. Reject even
        # same-origin/null/empty origins and duplicate headers, regardless of token.
        if "origin" in headers or "sec-fetch-site" in headers:
            status = 403
            error = "Browser requests are not allowed on the local control plane"
        else:
            tokens = headers.getlist(LOCAL_AUTH_HEADER)
            if (
                not self._token
                or len(tokens) != 1
                or not secrets.compare_digest(tokens[0].encode("utf-8"), self._token)
            ):
                status, error = 401, "A valid local launch token is required"
            elif scope["type"] == "http" and scope["method"] in (
                "POST",
                "PUT",
                "PATCH",
            ):
                content_types = headers.getlist("content-type")
                if (
                    len(content_types) != 1
                    or content_types[0].split(";", 1)[0].strip().lower() != "application/json"
                ):
                    status, error = 415, "Content-Type must be application/json"

        if status:
            if scope["type"] == "websocket":
                # Closing before accept rejects the HTTP upgrade (403 on ASGI servers).
                await send({"type": "websocket.close", "code": 1008, "reason": error})
            else:
                response = JSONResponse({"success": False, "error": error}, status_code=status)
                await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
