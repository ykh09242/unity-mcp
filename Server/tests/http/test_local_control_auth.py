"""Regression tests for browser access to the local Unity control plane."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.config import config
from transport.models import SessionDetails, SessionList
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.fixture
def local_client(monkeypatch):
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", "test-launch-token")
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted", "_retained_results"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    app = create_mcp_server(False).http_app()

    @asynccontextmanager
    async def lifespan(app):
        # Exercise real admission state without Unity discovery or telemetry.
        PluginHub.configure(PluginRegistry())
        try:
            yield
        finally:
            await PluginHub.shutdown()

    app.router.lifespan_context = lifespan
    with TestClient(app) as client:
        yield client


def test_command_without_token_is_rejected_before_body_parsing(
    local_client, monkeypatch
):
    # Given an unauthenticated request whose body cannot be parsed as JSON.
    sessions = AsyncMock()
    monkeypatch.setattr(PluginHub, "get_sessions", sessions)
    # When it reaches the actual REST route.
    response = local_client.post("/api/command", content="not-json")
    # Then no parser or Unity session selection is reached.
    assert response.status_code == 401
    sessions.assert_not_awaited()


def test_browser_origin_is_rejected_before_command_dispatch(local_client):
    # Given a browser-origin request, even with a current token.
    headers = {
        "Origin": "https://attacker.example",
        "X-Unity-MCP-Token": "test-launch-token",
    }
    # When it submits a simple request.
    response = local_client.post("/api/command", headers=headers, content="{}")
    # Then the browser request is denied before routing.
    assert response.status_code == 403


def test_browser_websocket_cannot_impersonate_plugin(local_client):
    # Given a browser that cannot set the application-owned token header.
    # When it attempts a plugin handshake, then it is rejected before acceptance.
    with pytest.raises(WebSocketDisconnect):
        with local_client.websocket_connect(
            "/hub/plugin", headers={"Origin": "https://attacker.example"}
        ):
            pass


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/instances"),
        ("GET", "/api/custom-tools"),
        ("POST", "/api/command"),
        ("POST", "/mcp"),
        ("GET", "/mcp"),
        ("DELETE", "/mcp"),
        ("OPTIONS", "/api/command"),
    ],
)
@pytest.mark.parametrize("token", [None, "old-launch-token"])
def test_all_control_routes_require_current_token(local_client, method, path, token):
    headers = {"X-Unity-MCP-Token": token} if token else {}
    response = local_client.request(method, path, headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "header,value",
    [
        ("Origin", "null"),
        ("Origin", ""),
        ("Origin", "http://127.0.0.1:8080"),
        ("Sec-Fetch-Site", "cross-site"),
        ("Sec-Fetch-Site", "same-site"),
        ("Sec-Fetch-Site", "same-origin"),
        ("Sec-Fetch-Site", "none"),
        ("Sec-Fetch-Site", ""),
    ],
)
def test_browser_provenance_is_denied_even_with_token(local_client, header, value):
    response = local_client.get(
        "/api/instances",
        headers={
            "X-Unity-MCP-Token": "test-launch-token",
            header: value,
        },
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"],
)
def test_non_json_command_is_denied_before_parsing(local_client, content_type):
    headers = {"X-Unity-MCP-Token": "test-launch-token"}
    if content_type:
        headers["Content-Type"] = content_type
    response = local_client.post("/api/command", headers=headers, content="invalid")
    assert response.status_code == 415


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Unity-MCP-Token": "old-launch-token"},
        {"X-Unity-MCP-Token": "test-launch-token", "Origin": "null"},
        {"X-Unity-MCP-Token": "test-launch-token", "Origin": "http://127.0.0.1:8080"},
        {"X-Unity-MCP-Token": "test-launch-token", "Sec-Fetch-Site": "same-origin"},
    ],
)
def test_plugin_handshake_rejects_missing_stale_or_browser_credentials(
    local_client, headers
):
    with pytest.raises(WebSocketDisconnect):
        with local_client.websocket_connect("/hub/plugin", headers=headers):
            pass


def test_authenticated_native_plugin_receives_welcome(local_client):
    with local_client.websocket_connect(
        "/hub/plugin", headers={"X-Unity-MCP-Token": "test-launch-token"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "welcome"


def test_authenticated_command_selects_requested_session(local_client, monkeypatch):
    # Given two sessions; the requested project is deliberately not the first.
    sessions = SessionList(
        sessions={
            name: SessionDetails(
                project=name,
                hash=f"{name}-hash",
                unity_version="6000",
                connected_at="now",
            )
            for name in ("first", "intended")
        }
    )
    monkeypatch.setattr(PluginHub, "get_sessions", AsyncMock(return_value=sessions))
    dispatch = AsyncMock(return_value={"success": True, "data": "intended-response"})
    monkeypatch.setattr(PluginHub, "send_command", dispatch)
    # When the authenticated caller commands its chosen project.
    response = local_client.post(
        "/api/command",
        headers={
            "X-Unity-MCP-Token": "test-launch-token",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={
            "type": "read_console",
            "params": {"count": 1},
            "unity_instance": "intended@intended-hash",
        },
    )
    # Then only the intended session receives the operation.
    assert response.json() == {"success": True, "data": "intended-response"}
    dispatch.assert_awaited_once_with("intended", "read_console", {"count": 1})


def test_query_string_cannot_substitute_for_token_header(local_client):
    response = local_client.get("/api/instances?token=test-launch-token")
    assert response.status_code == 401


def test_duplicate_token_headers_are_rejected(local_client):
    response = local_client.get(
        "/api/instances",
        headers=[
            ("X-Unity-MCP-Token", "test-launch-token"),
            ("X-Unity-MCP-Token", "old-launch-token"),
        ],
    )
    assert response.status_code == 401


def test_health_does_not_disclose_token(local_client):
    response = local_client.get("/health")
    assert response.status_code == 200
    assert "test-launch-token" not in response.text


def test_uninitialized_local_auth_fails_closed(monkeypatch):
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", None)
    response = TestClient(create_mcp_server(False).http_app()).get("/api/instances")
    assert response.status_code == 401


def test_explicit_host_policy_preserves_local_authentication(monkeypatch):
    from main import UnityMCP

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", "test-launch-token")
    app = UnityMCP("host-policy-regression").http_app(
        json_response=True,
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=["trusted.example"],
        allowed_origins=["https://trusted.example"],
    )
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "host-policy-regression", "version": "1"},
        },
    }
    headers = {
        "X-Unity-MCP-Token": "test-launch-token",
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(app, base_url="http://trusted.example") as client:
        response = client.post("/mcp", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        assert "result" in response.json()
        assert client.post("/mcp", json=payload).status_code == 401
        assert client.post(
            "/mcp", headers={**headers, "Host": "untrusted.example"}, json=payload,
        ).status_code == 421
        assert client.post(
            "/mcp", headers={**headers, "Origin": "https://trusted.example"},
            json=payload,
        ).status_code == 403
