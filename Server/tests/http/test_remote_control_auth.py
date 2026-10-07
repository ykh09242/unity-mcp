"""Exercise remote authentication across the actual MCP HTTP protocol boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Context
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.config import config
from services.api_key_service import ApiKeyService, ValidationResult


@pytest.fixture
def remote_app(monkeypatch):
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "api_key_validation_url", None)
    monkeypatch.setattr(config, "api_key_login_url", "https://auth.example/keys")
    monkeypatch.setenv("DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    validator = AsyncMock(
        side_effect=lambda key, **kwargs: ValidationResult(
            valid=key in ("alice-key", "bob-key"),
            user_id={"alice-key": "alice", "bob-key": "bob"}.get(key),
        )
    )
    monkeypatch.setattr(
        ApiKeyService, "_instance", SimpleNamespace(validate=validator, aclose=AsyncMock())
    )
    server = create_mcp_server(False)

    @server.resource("test://identity")
    async def identity(ctx: Context) -> str:
        return await ctx.get_state("user_id")

    return server.http_app(json_response=True), validator


@pytest.mark.parametrize(
    "method",
    [
        "initialize",
        "resources/list",
        "resources/templates/list",
        "resources/read",
        "tools/list",
        "tools/call",
        "ping",
    ],
)
@pytest.mark.parametrize("key", [None, "invalid-key"])
def test_all_mcp_methods_require_authentication(remote_app, method, key):
    app, _ = remote_app
    headers = {"X-API-Key": key} if key else {}
    response = TestClient(app).post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {},
        },
    )
    assert response.status_code == 401
    assert "resources" not in response.json()


def test_authentication_precedes_body_parsing_and_session_creation(remote_app):
    app, _ = remote_app
    response = TestClient(app).post("/mcp", content="not-json")
    assert response.status_code == 401
    assert "mcp-session-id" not in response.headers


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/mcp"),
        ("DELETE", "/mcp"),
        ("OPTIONS", "/mcp"),
        ("POST", "/health"),
        ("POST", "/api/auth/login-url"),
        ("GET", "/api/instances"),
    ],
)
def test_other_control_requests_require_authentication(remote_app, method, path):
    app, _ = remote_app
    assert TestClient(app).request(method, path).status_code == 401


def test_only_public_discovery_routes_are_available_without_a_key(remote_app):
    app, validator = remote_app
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/auth/login-url").json()["login_url"] == "https://auth.example/keys"
    validator.assert_not_awaited()


def test_duplicate_keys_and_query_credentials_are_rejected(remote_app):
    app, validator = remote_app
    client = TestClient(app)
    assert (
        client.post(
            "/mcp",
            headers=[
                ("X-API-Key", "alice-key"),
                ("X-API-Key", "bob-key"),
            ],
        ).status_code
        == 401
    )
    assert client.post("/mcp?api_key=alice-key").status_code == 401
    validator.assert_not_awaited()


@pytest.mark.parametrize(
    "result",
    [
        ValidationResult(valid=False),
        ValidationResult(valid=True),
        ValidationResult(valid=True, user_id=""),
        RuntimeError("validator unavailable"),
    ],
)
def test_validator_failures_are_closed(remote_app, result):
    app, validator = remote_app
    validator.side_effect = result if isinstance(result, Exception) else None
    validator.return_value = result
    assert TestClient(app).post("/mcp", headers={"X-API-Key": "alice-key"}).status_code == 401


def test_uninitialized_validator_is_closed(remote_app, monkeypatch):
    app, _ = remote_app
    monkeypatch.setattr(ApiKeyService, "_instance", None)
    assert TestClient(app).post("/mcp", headers={"X-API-Key": "alice-key"}).status_code == 401


def test_embedded_remote_app_requires_explicit_tls_proxy(monkeypatch):
    from main import UnityMCP

    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "http_behind_tls_proxy", False)
    with pytest.raises(ValueError, match="HTTPS/WSS proxy"):
        UnityMCP("unconfigured-remote").http_app()


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "invalid-key"}])
def test_plugin_upgrade_requires_authentication(remote_app, headers):
    app, _ = remote_app
    with pytest.raises(WebSocketDisconnect) as denied:
        with TestClient(app).websocket_connect("/hub/plugin", headers=headers):
            pass
    assert denied.value.code == 1008


def test_authenticated_session_catalogs_and_resource_identity(remote_app):
    app, validator = remote_app
    headers = {"X-API-Key": "alice-key", "Accept": "application/json, text/event-stream"}
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "auth-regression", "version": "1"},
                },
            },
        )
        assert response.status_code == 200, response.text
        assert "result" in response.json(), response.text
        headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
        headers["MCP-Protocol-Version"] = "2025-03-26"
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )
        assert response.status_code == 202

        for request_id, (method, params) in enumerate(
            [
                ("resources/list", {}),
                ("resources/templates/list", {}),
                ("tools/list", {}),
                ("tools/call", {"name": "manage_tools", "arguments": {"action": "list_groups"}}),
            ],
            start=2,
        ):
            response = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
            )
            assert response.status_code == 200, response.text
            assert "result" in response.json(), response.text
            assert not response.json()["result"].get("isError"), response.text

        # A session ID never substitutes for the key; identity comes from EACH request.
        for request_id, user in enumerate(("alice", "bob", "alice"), start=6):
            headers["X-API-Key"] = f"{user}-key"
            response = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "resources/read",
                    "params": {"uri": "test://identity"},
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["result"]["contents"][0]["text"] == user
        del headers["X-API-Key"]
        assert client.post("/mcp", headers=headers, content="not-json").status_code == 401
        validator.side_effect = lambda key, **kwargs: ValidationResult(valid=False)
        headers["X-API-Key"] = "alice-key"
        assert client.post("/mcp", headers=headers, content="not-json").status_code == 401
    ApiKeyService.get_instance().aclose.assert_awaited_once()
