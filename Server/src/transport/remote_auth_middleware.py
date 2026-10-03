"""Authenticate the entire remote MCP/control protocol before parsing or dispatch."""
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.constants import API_KEY_HEADER
from services.api_key_service import ApiKeyService

AUTHENTICATED_USER_STATE = "unity_mcp_authenticated_user_id"


class RemoteControlAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope["method"] == "GET" and scope["path"] in (
                "/health", "/api/auth/login-url"):
            await self.app(scope, receive, send)
            return

        user_id = None
        overloaded = False
        keys = Headers(scope=scope).getlist(API_KEY_HEADER)
        if len(keys) == 1 and keys[0] and ApiKeyService.is_initialized():
            try:
                peer = scope.get("client")
                result = await ApiKeyService.get_instance().validate(
                    keys[0], source_id=peer[0] if peer else "unknown",
                )
                overloaded = result.overloaded
                if result.valid and isinstance(result.user_id, str) and result.user_id:
                    user_id = result.user_id
            except Exception:
                pass  # Fail closed without logging credentials or validator response bodies.
        if user_id is None:
            if overloaded:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1013, "reason": "Authentication temporarily busy"})
                else:
                    await JSONResponse({"error": "Authentication temporarily busy"}, status_code=429,
                                       headers={"Retry-After": "1"})(scope, receive, send)
                return
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": "API key authentication required"})
            else:
                await JSONResponse({"error": "API key authentication required"}, status_code=401)(scope, receive, send)
            return

        # Copy request state; never persist identity in shared/session routing state.
        authenticated_scope = {**scope, "state": {**scope.get("state", {}), AUTHENTICATED_USER_STATE: user_id}}
        await self.app(authenticated_scope, receive, send)
