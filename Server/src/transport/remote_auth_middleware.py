"""Authenticate remote requests and own connected credential authorization lifetimes."""

import asyncio
from contextlib import AsyncExitStack

import anyio
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.constants import API_KEY_HEADER
from services.api_key_service import ApiKeyService
from services.credential_lease import CredentialLeaseError

AUTHENTICATED_USER_STATE = "unity_mcp_authenticated_user_id"
AUTHENTICATED_LEASE_STATE = "unity_mcp_authenticated_credential_lease"


class RemoteControlAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if (
            scope["type"] == "http"
            and scope["method"] == "GET"
            and scope["path"] in ("/health", "/api/auth/login-url")
        ):
            await self.app(scope, receive, send)
            return

        lease = None
        overloaded = False
        keys = Headers(scope=scope).getlist(API_KEY_HEADER)
        if len(keys) == 1 and keys[0] and ApiKeyService.is_initialized():
            peer = scope.get("client")
            try:
                result = await ApiKeyService.get_instance().validate(
                    keys[0], source_id=peer[0] if peer else "unknown"
                )
            except (RuntimeError, OSError, ValueError):
                result = None
            if result is not None:
                overloaded = result.overloaded
                if result.valid:
                    lease = result.lease
        # Do not retain request credentials in callback closures or authenticated state.
        del keys
        if lease is None:
            await self._reject(scope, receive, send, overloaded)
            return

        started = False
        websocket_closed = False

        async def guarded_receive():
            await lease.check()
            message = await receive()
            await lease.check()
            return message

        async def guarded_send(message):
            nonlocal started, websocket_closed
            await lease.check()
            lease.check_now()
            if message["type"] in ("http.response.start", "websocket.accept"):
                # A suspended writer may already have crossed the protocol boundary.
                started = True
            if message["type"] == "websocket.close":
                websocket_closed = True
            await send(message)
            await lease.check()

        authenticated_scope = {
            **scope,
            "state": {
                **scope.get("state", {}),
                AUTHENTICATED_USER_STATE: lease.user_id,
                AUTHENTICATED_LEASE_STATE: lease,
            },
        }
        # Keep the actual ASGI owner charged through policy handling, including a
        # cancellation-resistant writer after the guarded app has stopped.
        async with AsyncExitStack() as lifetime:
            try:
                await lifetime.enter_async_context(lease.owner_lifetime())
                await lease.check()
                await self.app(authenticated_scope, guarded_receive, guarded_send)
                await lease.check()
            except CredentialLeaseError as error:
                overloaded = error.overloaded
            except asyncio.CancelledError:
                if not lease.retired:
                    raise
            else:
                return
            # An established HTTP stream must end, never become a second 401 response
            # or a successful subscription completion. Outer ASGI owns socket closure.
            if not started:
                if scope["type"] == "websocket" and websocket_closed:
                    return
                with anyio.CancelScope(shield=True):
                    await self._reject(scope, receive, send, overloaded)
            elif scope["type"] == "websocket" and not websocket_closed:
                with anyio.move_on_after(1, shield=True):
                    await send(
                        {
                            "type": "websocket.close",
                            "code": 1008,
                            "reason": "Remote authorization ended",
                        }
                    )

    @staticmethod
    async def _reject(scope, receive, send, overloaded):
        if scope["type"] == "websocket":
            await send(
                {
                    "type": "websocket.close",
                    "code": 1013 if overloaded else 1008,
                    "reason": "Authentication temporarily busy"
                    if overloaded
                    else "API key authentication required",
                }
            )
        else:
            await JSONResponse(
                {
                    "error": "Authentication temporarily busy"
                    if overloaded
                    else "API key authentication required"
                },
                status_code=429 if overloaded else 401,
                headers={"Retry-After": "1"} if overloaded else None,
            )(scope, receive, send)
