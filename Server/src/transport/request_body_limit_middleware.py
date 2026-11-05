"""Bound HTTP ingress before request chunks reach framework body parsers."""

from dataclasses import dataclass
from typing import Final
from collections.abc import Callable, Mapping

import anyio
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette._utils import get_route_path
from core.config import positive_http_limit
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE

# Ingress capacity, not an increase to the smaller Unity command budget.
MAX_HTTP_REQUEST_BYTES: Final = 64 * 1024 * 1024
MAX_HTTP_INGRESS_BYTES: Final = 256 * 1024 * 1024
MAX_HTTP_BODY_READERS: Final = 32
HTTP_BODY_UPLOAD_SECONDS: Final = 30.0


@dataclass(slots=True)
class _PrincipalUsage:
    requests: int = 0
    bytes: int = 0


class _RequestBodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="HTTP request body exceeds the size limit")


class _IngressUnavailable(HTTPException):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code=status_code, detail="HTTP request body admission unavailable")


class RequestBodyLimitMiddleware:
    """Count bytes lazily, retaining no body and preserving response streaming."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_size: int = MAX_HTTP_REQUEST_BYTES,
        max_readers: int = MAX_HTTP_BODY_READERS,
        upload_seconds: float = HTTP_BODY_UPLOAD_SECONDS,
        max_ingress_bytes: int = MAX_HTTP_INGRESS_BYTES,
        route_body_limits: Mapping[str, Callable[[], int]] | None = None,
        hosted: bool = False,
    ) -> None:
        self.app = app
        self.max_body_size = max_body_size
        self.max_readers = max_readers
        self.upload_seconds = upload_seconds
        self.max_ingress_bytes = max_ingress_bytes
        self.active_readers = 0
        self.retained_bytes = 0
        self.hosted = hosted
        if hosted:
            self._principal_bytes = positive_http_limit(
                "HTTP_PRINCIPAL_RETAINED_BYTES", max(1, max_ingress_bytes // 2)
            )
            self._principal_requests = positive_http_limit(
                "HTTP_PRINCIPAL_RETAINED_REQUESTS", max(1, max_readers // 2)
            )
        self._principals: dict[str, _PrincipalUsage] = {}
        self.route_body_limits = route_body_limits or {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        route_limit = self.route_body_limits.get(get_route_path(scope))
        max_body_size = (
            min(self.max_body_size, route_limit()) if route_limit else self.max_body_size
        )
        lengths = [
            value for name, value in scope.get("headers", []) if name.lower() == b"content-length"
        ]
        ceiling = str(max_body_size).encode("ascii")
        invalid_length = False
        for value in lengths:
            value = value.strip()
            if not value or not value.isdigit():
                invalid_length = True
                continue
            # Compare decimal strings to avoid integer parsing/allocation on huge headers.
            value = value.lstrip(b"0") or b"0"
            if len(value) > len(ceiling) or (len(value) == len(ceiling) and value > ceiling):
                await JSONResponse(
                    {"error": "HTTP request body exceeds the size limit"}, status_code=413
                )(scope, receive, send)
                return
        if invalid_length or len(lengths) > 1:
            await JSONResponse({"error": "Invalid Content-Length header"}, status_code=400)(
                scope, receive, send
            )
            return

        total_size = 0
        response_started = False
        rejected = False
        admitted = False
        complete = False
        upload_scope = anyio.CancelScope()
        principal = scope.get("state", {}).get(AUTHENTICATED_USER_STATE) if self.hosted else None
        retained: _PrincipalUsage | None = None

        def admit_reader() -> bool:
            nonlocal admitted, retained
            if self.active_readers >= self.max_readers:
                return False
            if principal is not None:
                usage = self._principals.get(principal)
                if usage is None:
                    usage = self._principals[principal] = _PrincipalUsage()
                if usage.requests >= self._principal_requests:
                    return False
                retained = usage
                retained.requests += 1
            self.active_readers += 1
            admitted = True
            upload_scope.deadline = anyio.current_time() + self.upload_seconds
            return True

        def finish_reader() -> None:
            nonlocal admitted
            upload_scope.deadline = float("inf")
            if admitted:
                self.active_readers -= 1
                admitted = False

        async def reject_capacity() -> None:
            nonlocal rejected
            rejected = True
            finish_reader()
            if not response_started:
                await JSONResponse(
                    {"error": "HTTP request body capacity is exhausted"}, status_code=503
                )(scope, receive, send)
            raise _IngressUnavailable(503)

        async def limited_receive() -> Message:
            nonlocal total_size, rejected, admitted, complete
            if rejected:
                raise _RequestBodyTooLarge()
            # GET streams often wait for a disconnect without reading a body.
            # Admit those only when an actual nonempty body arrives.
            if not admitted and not complete and scope.get("method") != "GET":
                if not admit_reader():
                    await reject_capacity()
            message = await receive()
            if message["type"] == "http.request":
                if not admitted and not complete and message.get("body", b""):
                    if not admit_reader():
                        del message
                        await reject_capacity()
                size = len(message.get("body", b""))
                if size > max_body_size - total_size:
                    rejected = True
                    finish_reader()
                    # The rejected chunk is not needed during error delivery or its traceback.
                    del message
                    if not response_started:
                        await JSONResponse(
                            {"error": "HTTP request body exceeds the size limit"}, status_code=413
                        )(scope, receive, send)
                    raise _RequestBodyTooLarge()
                if size > self.max_ingress_bytes - self.retained_bytes or (
                    retained is not None and size > self._principal_bytes - retained.bytes
                ):
                    del message
                    await reject_capacity()
                total_size += size
                self.retained_bytes += size
                if retained is not None:
                    retained.bytes += size
                if not message.get("more_body", False):
                    complete = True
                    finish_reader()
            elif message["type"] == "http.disconnect":
                complete = True
                finish_reader()
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
            with upload_scope:
                try:
                    await self.app(scope, limited_receive, limited_send)
                except _IngressUnavailable:
                    if response_started:
                        raise
            if upload_scope.cancel_called and not complete:
                finish_reader()
                if response_started:
                    raise _IngressUnavailable(408)
                await JSONResponse(
                    {"error": "HTTP request body upload timed out"}, status_code=408
                )(scope, receive, send)
        except _RequestBodyTooLarge:
            if response_started:
                # A streaming response cannot be replaced once headers are sent.
                raise
        finally:
            finish_reader()
            self.retained_bytes -= total_size
            if retained is not None:
                retained.bytes -= total_size
                retained.requests -= 1
                if not retained.requests:
                    del self._principals[principal]
        if rejected and response_started:
            raise _RequestBodyTooLarge()
