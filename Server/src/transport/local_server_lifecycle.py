"""Bounded local shutdown of the HTTP runner that owns this application."""

import asyncio
import secrets
import signal
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
import uvicorn

from transport.plugin_hub import PluginHub


class ShutdownRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    instance_token: str = Field(min_length=1, max_length=256)


def owned_runner_shutdown(request: Request) -> Callable[[], bool] | None:
    """Resolve FastMCP's active Uvicorn owner without sending an OS signal.

    Uvicorn installs its bound handle_exit while serving. Embedded runners that
    do not install that handler cannot establish ownership and fail closed.
    """
    handler = signal.getsignal(signal.SIGTERM)
    owner = getattr(handler, "__self__", None)
    if (
        not isinstance(owner, uvicorn.Server)
        or getattr(handler, "__func__", None) is not uvicorn.Server.handle_exit
        or owner.config.app is not request.app
        or not owner.started
    ):
        return None

    def request_exit() -> bool:
        # This synchronous flag update cannot yield between closing admission
        # and requesting the same owner's normal graceful shutdown loop.
        if signal.getsignal(signal.SIGTERM) != handler or owner.config.app is not request.app:
            return False
        owner.should_exit = True
        return True

    return request_exit


async def request_local_shutdown(request: Request, instance_token: str | None) -> JSONResponse:
    """LocalControlAuthMiddleware authenticates before this launch-identity check."""
    if not instance_token:
        return JSONResponse({"success": False, "error": "unmanaged_server"}, status_code=503)
    body = bytearray()
    try:
        async with asyncio.timeout(1.0):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 1024:
                    return JSONResponse(
                        {"success": False, "error": "body_too_large"}, status_code=413
                    )
                body.extend(chunk)
    except TimeoutError:
        return JSONResponse({"success": False, "error": "request_timeout"}, status_code=408)
    try:
        payload = ShutdownRequest.model_validate_json(body)
    except ValidationError:
        return JSONResponse({"success": False, "error": "invalid_request"}, status_code=400)
    if not secrets.compare_digest(payload.instance_token.encode(), instance_token.encode()):
        return JSONResponse(
            {"success": False, "error": "launch_identity_mismatch"}, status_code=403
        )
    callback = owned_runner_shutdown(request)
    if callback is None:
        return JSONResponse({"success": False, "error": "runner_unavailable"}, status_code=503)
    try:
        async with asyncio.timeout(0.5):
            status = await PluginHub.request_idle_shutdown(callback)
    except TimeoutError:
        return JSONResponse({"success": False, "error": "runner_unavailable"}, status_code=503)
    if status == "shutdown_requested":
        return JSONResponse({"success": True, "status": status})
    return JSONResponse(
        {"success": False, "error": status},
        status_code=409 if status == "sessions_active" else 503,
    )
