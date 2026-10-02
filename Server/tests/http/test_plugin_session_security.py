"""Exercise plugin registration limits through the real WebSocket endpoint."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.config import config
from transport.models import RegisterMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.fixture
def plugin_client(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    registry = PluginRegistry()

    @asynccontextmanager
    async def lifespan(app):
        PluginHub.configure(registry)
        yield

    app = Starlette(routes=[WebSocketRoute("/hub/plugin", PluginHub)], lifespan=lifespan)
    with TestClient(app) as client:
        yield client


def test_second_registration_closes_socket_and_removes_session(plugin_client):
    with plugin_client.websocket_connect("/hub/plugin") as ws:
        assert ws.receive_json()["type"] == "welcome"
        ws.send_json({"type": "register", "project_hash": "first"})
        assert ws.receive_json()["type"] == "registered"

        ws.send_json({"type": "register", "project_hash": "second"})

        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4409
    assert PluginHub._connections == {}
    assert PluginHub._ping_tasks == {}
    assert PluginHub._last_pong == {}
    assert plugin_client.portal.call(PluginHub._registry.list_sessions) == {}


@pytest.mark.parametrize("field, value", [
    ("project_hash", "x" * 257),
    ("project_name", "x" * 257),
    ("unity_version", "x" * 65),
    ("project_path", "x" * 4097),
], ids=["hash", "name", "version", "path"])
def test_registration_rejects_oversized_fields(field, value):
    with pytest.raises(ValidationError):
        RegisterMessage(**{"project_hash": "valid", field: value})


@pytest.mark.asyncio
async def test_registry_quota_allows_reconnect_but_rejects_new_instance(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(PluginRegistry, "MAX_SESSIONS_PER_USER", 1)
    registry = PluginRegistry()
    await registry.register("first", "A", "hash-a", "6000", user_id="user-a")

    with pytest.raises(ValueError, match="limit"):
        await registry.register("second", "B", "hash-b", "6000", user_id="user-a")

    replacement, evicted = await registry.register(
        "replacement", "A", "hash-a", "6000", user_id="user-a")
    assert replacement.session_id == "replacement"
    assert evicted == "first"
    await registry.register("other-user", "B", "hash-b", "6000", user_id="user-b")


@pytest.mark.asyncio
async def test_disconnect_removes_all_aliases_and_pending_commands(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    registry = PluginRegistry()
    ws = AsyncMock()
    hub = PluginHub({"type": "websocket"}, receive=AsyncMock(), send=AsyncMock())
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong"):
        monkeypatch.setattr(PluginHub, name, {})
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    futures = []
    for sid in ("first", "second"):
        await registry.register(sid, sid, sid, "6000")
        PluginHub._connections[sid] = ws
        PluginHub._last_pong[sid] = 1
        future = asyncio.get_running_loop().create_future()
        PluginHub._pending[sid] = {"session_id": sid, "future": future}
        futures.append(future)

    await hub.on_disconnect(ws, 1000)

    assert PluginHub._connections == PluginHub._pending == PluginHub._last_pong == {}
    assert await registry.list_sessions() == {}
    assert all(future.exception() is not None for future in futures)


@pytest.mark.asyncio
async def test_global_session_limit_applies_across_users(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(PluginRegistry, "MAX_SESSIONS", 1)
    registry = PluginRegistry()
    await registry.register("first", "A", "a", "6000", user_id="user-a")

    with pytest.raises(ValueError, match="limit"):
        await registry.register("second", "B", "b", "6000", user_id="user-b")
