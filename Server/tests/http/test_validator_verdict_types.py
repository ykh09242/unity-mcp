"""External authentication verdicts must be literal JSON booleans."""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from services.api_key_service import ApiKeyService
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE, RemoteControlAuthMiddleware


@pytest.mark.parametrize("verdict, status", [
    (True, 200), (False, 401), ("false", 401), ("true", 401),
    (1, 401), ([True], 401), (None, 401),
])
def test_remote_http_authentication_requires_boolean_verdict(monkeypatch: pytest.MonkeyPatch, verdict: bool | str | int | list[bool] | None, status: int) -> None:
    # Given the real auth service backed by an HTTP transport with a fixture verdict.
    monkeypatch.setattr(ApiKeyService, "_instance", None)
    service = ApiKeyService(validation_url="https://auth.example/validate")
    real_client = httpx.AsyncClient

    def response_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"valid": verdict, "user_id": "fixture-user"})

    def client_factory(*, timeout: float, follow_redirects: bool, limits: httpx.Limits) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(response_handler), timeout=timeout,
                           follow_redirects=follow_redirects, limits=limits)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    async def identity(request: Request) -> JSONResponse:
        return JSONResponse({"user": request.scope["state"][AUTHENTICATED_USER_STATE]})

    app = Starlette(routes=[Route("/control", identity)], middleware=[Middleware(RemoteControlAuthMiddleware)])
    # When the actual HTTP authentication middleware processes a request.
    response = TestClient(app).get("/control", headers={"X-API-Key": "fixture-api-key"})
    # Then malformed truthy verdicts never authenticate or enter the verdict cache.
    assert response.status_code == status
    if verdict is not True and verdict is not False:
        assert service._cache == {}
    if verdict is True:
        assert response.json() == {"user": "fixture-user"}
