"""Exercise remote authentication across the actual MCP HTTP protocol boundary."""

import json
from unittest.mock import AsyncMock

import pytest
from fastmcp import Context
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.config import config
from services.api_key_service import ApiKeyService, ValidationResult


@pytest.fixture
def remote_app(monkeypatch, prepared_tool_factory):
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
            valid=key in ("alice-key", "alice-rotated", "bob-key"),
            user_id={"alice-key": "alice", "alice-rotated": "alice", "bob-key": "bob"}.get(key),
        )
    )
    service = ApiKeyService("https://fixture.invalid/validate", cache_ttl=0)
    monkeypatch.setattr(service, "_validate_external", validator)
    from fastmcp.tools.base import Tool

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Tool, "from_function", prepared_tool_factory)
        server = create_mcp_server(False)

    @server.resource("test://identity")
    async def identity(ctx: Context) -> str:
        return await ctx.get_state("user_id")

    @server.resource("test://routing")
    async def routing(ctx: Context) -> str:
        return json.dumps(
            {
                "instance": await ctx.get_state("unity_instance"),
                "session": await ctx.get_state("unity_session_id"),
            }
        )

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


def _open_session(client):
    headers = {"X-API-Key": "alice-key", "Accept": "application/json, text/event-stream"}
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
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert response.status_code == 202
    return headers


def test_session_termination_requires_the_initiating_user(remote_app):
    app, validator = remote_app
    ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    with TestClient(app) as client:
        headers = _open_session(client)
        denied = client.delete("/mcp", headers={**headers, "X-API-Key": "bob-key"})
        unknown = client.delete("/mcp", headers={**headers, "Mcp-Session-Id": "unknown"})
        assert denied.status_code == unknown.status_code == 404
        assert denied.json() == unknown.json()
        duplicate_headers = [*headers.items(), ("Mcp-Session-Id", "unknown")]
        assert client.delete("/mcp", headers=duplicate_headers).status_code == 404
        assert client.post("/mcp", headers=headers, json=ping).json()["result"] == {}
        validator.side_effect = lambda key, **kwargs: ValidationResult(
            valid=key == "alice-rotated", user_id="alice" if key == "alice-rotated" else None
        )
        assert client.delete("/mcp", headers=headers).status_code == 401
        rotated = {**headers, "X-API-Key": "alice-rotated"}
        assert client.delete("/mcp", headers=rotated).status_code == 200
        assert client.post("/mcp", headers=rotated, json=ping).status_code == 404


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_stateful_session_rejects_a_different_authenticated_user(remote_app, method):
    app, _ = remote_app
    ping = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    with TestClient(app) as client:
        headers = _open_session(client)
        denied = client.request(
            method, "/mcp", headers={**headers, "X-API-Key": "bob-key"}, json=ping
        )
        assert denied.status_code == 404
        assert client.post("/mcp", headers=headers, json=ping).json()["result"] == {}
        rotated = {**headers, "X-API-Key": "alice-rotated"}
        assert client.post("/mcp", headers=rotated, json=ping).json()["result"] == {}


def test_stateful_owner_preserves_selection_and_explicit_routing(remote_app, monkeypatch):
    from transport.models import SessionDetails, SessionList
    from transport.plugin_hub import PluginHub

    records = {
        "alice": {"a1": ("First", "1111"), "a2": ("Second", "2222")},
        "bob": {"b1": ("Other", "2222")},
    }

    async def sessions(user_id=None):
        return SessionList(
            sessions={
                sid: SessionDetails(
                    project=name, hash=project_hash, unity_version="6000", connected_at="now"
                )
                for sid, (name, project_hash) in records.get(user_id, {}).items()
            }
        )

    async def resolve(instance, *, user_id, **kwargs):
        for sid, (name, project_hash) in records[user_id].items():
            if instance == f"{name}@{project_hash}":
                return sid
        raise ValueError("Unknown instance")

    monkeypatch.setattr(PluginHub, "get_sessions", sessions)
    monkeypatch.setattr(PluginHub, "_resolve_session_id", resolve)
    app, _ = remote_app
    with TestClient(app) as client:
        headers = _open_session(client)

        def call(method, params, *, key="alice-key"):
            return client.post(
                "/mcp",
                headers={**headers, "X-API-Key": key},
                json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
            )

        def select(instance, key="alice-key"):
            return call(
                "tools/call",
                {"name": "set_active_instance", "arguments": {"instance": instance}},
                key=key,
            )

        def read_route(meta=None, key="alice-key"):
            params = {"uri": "test://routing"}
            if meta:
                params["_meta"] = {"unity_instance": meta}
            return call("resources/read", params, key=key)

        assert select("First@1111").json()["result"]["structuredContent"]["success"]
        debug = {"name": "debug_request_context", "arguments": {}}
        assert call("tools/call", debug, key="bob-key").status_code == 404
        assert select("Other@2222", key="bob-key").status_code == 404
        assert read_route(key="bob-key").status_code == 404
        default = json.loads(
            read_route(key="alice-rotated").json()["result"]["contents"][0]["text"]
        )
        assert default == {"instance": "First@1111", "session": "a1"}
        explicit = json.loads(read_route("Second@2222").json()["result"]["contents"][0]["text"])
        assert explicit == {"instance": "Second@2222", "session": "a2"}
        assert json.loads(read_route().json()["result"]["contents"][0]["text"]) == default


@pytest.mark.parametrize(("stateless", "version"), [(True, "2025-03-26"), (False, "2026-07-28")])
def test_sessionless_delete_keeps_transport_method_rejection(remote_app, stateless, version):
    app, _ = remote_app
    app = app.state.fastmcp_server.http_app(stateless_http=stateless)
    headers = {
        "X-API-Key": "alice-key",
        "Mcp-Protocol-Version": version,
        "Mcp-Session-Id": "unknown",
    }
    with TestClient(app) as client:
        assert client.delete("/mcp", headers=headers).status_code == 405


def test_sessionless_post_uses_current_identity_even_with_session_header(remote_app):
    app, _ = remote_app
    app = app.state.fastmcp_server.http_app(stateless_http=True, json_response=True)
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "X-API-Key": "bob-key",
                "Mcp-Session-Id": "unrelated",
                "Mcp-Protocol-Version": "2025-03-26",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "test://identity"},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["contents"][0]["text"] == "bob"


def test_invalid_protocol_version_keeps_sdk_error(remote_app):
    app, _ = remote_app
    with TestClient(app) as client:
        headers = _open_session(client)
        headers.update({"X-API-Key": "bob-key", "MCP-Protocol-Version": "2099-01-01"})
        response = client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "ping"}
        )
        headers.pop("Mcp-Session-Id")
        without_session = client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "ping"}
        )
        assert response.status_code == without_session.status_code
        assert response.json() == without_session.json()


def test_authenticated_session_catalogs_and_resource_identity(remote_app, monkeypatch):
    app, validator = remote_app
    service = ApiKeyService.get_instance()
    close = AsyncMock(wraps=service.aclose)
    monkeypatch.setattr(service, "aclose", close)
    with TestClient(app) as client:
        headers = _open_session(client)

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
        for request_id, key in enumerate(("alice-key", "alice-rotated"), start=6):
            headers["X-API-Key"] = key
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
            assert response.json()["result"]["contents"][0]["text"] == "alice"
        del headers["X-API-Key"]
        assert client.post("/mcp", headers=headers, content="not-json").status_code == 401
        validator.side_effect = lambda key, **kwargs: ValidationResult(valid=False)
        headers["X-API-Key"] = "alice-key"
        assert client.post("/mcp", headers=headers, content="not-json").status_code == 401
    close.assert_awaited_once()
    assert service._closed
    assert not ApiKeyService.is_initialized()
