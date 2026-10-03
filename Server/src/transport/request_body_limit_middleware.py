"""Bound HTTP ingress before request chunks reach framework body parsers."""

from typing import Final

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


# Ingress capacity, not an increase to the smaller Unity command budget.
MAX_HTTP_REQUEST_BYTES: Final = 64 * 1024 * 1024


class _RequestBodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="HTTP request body exceeds the size limit")


class RequestBodyLimitMiddleware:
    """Count bytes lazily, retaining no body and preserving response streaming."""

    def __init__(self, app: ASGIApp, max_body_size: int = MAX_HTTP_REQUEST_BYTES) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        lengths = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        ceiling = str(self.max_body_size).encode("ascii")
        invalid_length = False
        for value in lengths:
            value = value.strip()
            if not value or not value.isdigit():
                invalid_length = True
                continue
            # Compare decimal strings to avoid integer parsing/allocation on huge headers.
            value = value.lstrip(b"0") or b"0"
            if len(value) > len(ceiling) or (len(value) == len(ceiling) and value > ceiling):
                await JSONResponse({"error": "HTTP request body exceeds the size limit"}, status_code=413)(scope, receive, send)
                return
        if invalid_length or len(lengths) > 1:
            await JSONResponse({"error": "Invalid Content-Length header"}, status_code=400)(scope, receive, send)
            return

        total_size = 0
        response_started = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal total_size, rejected
            if rejected:
                raise _RequestBodyTooLarge()
            message = await receive()
            if message["type"] == "http.request":
                size = len(message.get("body", b""))
                if size > self.max_body_size - total_size:
                    rejected = True
                    if not response_started:
                        await JSONResponse({"error": "HTTP request body exceeds the size limit"}, status_code=413)(scope, receive, send)
                    raise _RequestBodyTooLarge()
                total_size += size
            return message

        async def limited_send(message: Message) -> None:
            nonlocal response_started
            # MCP and local handlers may catch receive errors and send an error
            # response. Never overwrite the 413 already emitted at the boundary.
            if rejected:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except _RequestBodyTooLarge:
            if response_started:
                # A streaming response cannot be replaced once headers are sent.
                raise
        if rejected and response_started:
            raise _RequestBodyTooLarge()
