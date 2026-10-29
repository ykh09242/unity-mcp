"""Authenticate remote requests and own connected credential authorization lifetimes."""

import asyncio
from contextlib import AsyncExitStack
from ipaddress import IPv4Address, IPv6Address, ip_address

import anyio
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.constants import API_KEY_HEADER
from core.config import config
from services.api_key_service import ApiKeyService
from services.credential_lease import CredentialLeaseError
from transport.socket_write_guard import (
    SOCKET_SEND_BOUNDARY_STATE,
    SocketSendBoundary,
    socket_write_guard,
)

AUTHENTICATED_USER_STATE = "unity_mcp_authenticated_user_id"
AUTHENTICATED_LEASE_STATE = "unity_mcp_authenticated_credential_lease"
VERIFIED_CLIENT_IP_HEADER = "X-Unity-MCP-Client-IP"


def _source_ip(value: str) -> IPv4Address | IPv6Address:
    """Normalize equivalent addresses without accepting lists, ports, or zone identifiers."""
    if "%" in value:
        raise ValueError("Scoped source IP is not allowed")
    address = ip_address(value)
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


class RemoteControlAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._trusted_proxy_networks = config.http_trusted_proxy_networks

    def _source_id(self, scope: Scope) -> str:
        peer = scope.get("client")
        if not peer:
            return "unknown"
        try:
            address = _source_ip(peer[0])
        except ValueError:
            # Non-IP peers cannot gain proxy trust (including ASGI test/Unix peers).
            return peer[0]
        if any(address in network for network in self._trusted_proxy_networks):
            values = Headers(scope=scope).getlist(VERIFIED_CLIENT_IP_HEADER)
            if len(values) != 1:
                raise ValueError("Trusted proxy must provide one verified client IP")
            return str(_source_ip(values[0]))
        return str(address)

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
            try:
                result = await ApiKeyService.get_instance().validate(
                    keys[0], source_id=self._source_id(scope)
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
        send_boundary = SocketSendBoundary()
        outer_boundary = scope.get("state", {}).get(SOCKET_SEND_BOUNDARY_STATE)
        if not isinstance(outer_boundary, SocketSendBoundary):
            outer_boundary = send_boundary

        async def guarded_receive():
            await lease.check()
            message = await receive()
            await lease.check()
            return message

        async def guarded_send(message):
            nonlocal started, websocket_closed
            await lease.check()
            lease.check_now()
            writer = socket_write_guard.get()
            if (
                scope["type"] == "websocket"
                and writer is not None
                and writer.matches(send_boundary, message)
            ):
                if not await writer.authorize():
                    return
                # Final target admission may itself renew the requester/plugin.
                # Recheck this outer credential without another yielding step.
                lease.check_now()
                writer.dispatch()
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
                **(
                    {SOCKET_SEND_BOUNDARY_STATE: outer_boundary}
                    if scope["type"] == "websocket"
                    else {}
                ),
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
