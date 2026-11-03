"""Wire regressions for negotiated heartbeat challenges."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.config import config
from transport.models import PingMessage, PongMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "PING_INTERVAL", 0.05)
    monkeypatch.setattr(PluginHub, "PING_TIMEOUT", 0.3)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)

    @asynccontextmanager
    async def lifespan(app):
        PluginHub.configure(PluginRegistry())
        yield
        await PluginHub.shutdown()

    app = Starlette(routes=[WebSocketRoute("/hub/plugin", PluginHub)], lifespan=lifespan)
    with TestClient(app) as test_client:
        yield test_client


def test_negotiated_challenges_round_trip(client):
    with client.websocket_connect("/hub/plugin") as ws:
        welcome = ws.receive_json()
        assert "heartbeat_challenge_v1" in welcome["capabilities"]
        ws.send_json(
            {"type": "register", "project_hash": "new", "capabilities": ["heartbeat_challenge_v1"]}
        )
        registration = ws.receive_json()
        assert "heartbeat_challenge_v1" in registration["capabilities"]
        ws.send_json({"type": "ping", "challenge": "a" * 32})
        assert ws.receive_json() == {"type": "pong", "challenge": "a" * 32}
        ping = ws.receive_json()
        assert ping["type"] == "ping" and len(ping["challenge"]) == 32
        ws.send_json(
            {
                "type": "pong",
                "session_id": registration["session_id"],
                "challenge": ping["challenge"],
            }
        )
        assert ws.receive_json()["type"] == "ping"


def test_unsolicited_pongs_cannot_prevent_timeout(client):
    with client.websocket_connect("/hub/plugin") as ws:
        ws.receive_json()
        ws.send_json(
            {"type": "register", "project_hash": "new", "capabilities": ["heartbeat_challenge_v1"]}
        )
        session_id = ws.receive_json()["session_id"]
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.send_json({"type": "pong", "session_id": session_id, "challenge": "b" * 32})
                ws.receive_json()


def test_replayed_pong_cannot_keep_session_alive(client):
    with client.websocket_connect("/hub/plugin") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "register",
                "project_hash": "replay",
                "capabilities": ["heartbeat_challenge_v1"],
            }
        )
        session_id = ws.receive_json()["session_id"]
        first = ws.receive_json()["challenge"]
        ws.send_json({"type": "pong", "session_id": session_id, "challenge": first})
        second = ws.receive_json()["challenge"]
        assert second != first
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.send_json({"type": "pong", "session_id": session_id, "challenge": first})
                ws.receive_json()


def test_outstanding_challenge_survives_multiple_ping_intervals(client):
    with client.websocket_connect("/hub/plugin") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "register",
                "project_hash": "delayed",
                "capabilities": ["heartbeat_challenge_v1"],
            }
        )
        session_id = ws.receive_json()["session_id"]
        first = ws.receive_json()["challenge"]
        assert ws.receive_json()["challenge"] == first
        ws.send_json({"type": "pong", "session_id": session_id, "challenge": first})
        assert ws.receive_json()["challenge"] != first


def test_peer_pings_do_not_mask_lost_server_pongs(client):
    with client.websocket_connect("/hub/plugin") as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "register",
                "project_hash": "one-way",
                "capabilities": ["heartbeat_challenge_v1"],
            }
        )
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.send_json({"type": "ping", "challenge": "c" * 32})
                ws.receive_json()
                ws.receive_json()


def test_legacy_peer_receives_plain_ping(client):
    with client.websocket_connect("/hub/plugin") as ws:
        ws.receive_json()
        ws.send_json({"type": "register", "project_hash": "old"})
        session_id = ws.receive_json()["session_id"]
        ws.send_json({"type": "ping", "challenge": "d" * 32})
        assert ws.receive_json() == {"type": "ping"}
        ws.send_json({"type": "pong", "session_id": session_id})
        assert ws.receive_json() == {"type": "ping"}


@pytest.mark.asyncio
async def test_old_ping_loop_cannot_evict_replacement(monkeypatch):
    old = SimpleNamespace(send_json=AsyncMock())
    replacement = SimpleNamespace(send_json=AsyncMock())
    evict = AsyncMock()
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_connections", {"same-session": replacement})
    monkeypatch.setattr(PluginHub, "_last_pong", {"same-session": 0})
    monkeypatch.setattr(PluginHub, "_evict_connection", evict)
    monkeypatch.setattr(PluginHub, "PING_INTERVAL", 0.001)
    await PluginHub._ping_loop("same-session", old)
    evict.assert_not_awaited()
    old.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_socket_pong_cannot_refresh_replacement(monkeypatch):
    old = SimpleNamespace(
        state=SimpleNamespace(plugin_features=frozenset({"heartbeat_challenge_v1"}))
    )
    replacement = SimpleNamespace()
    registry = SimpleNamespace(touch=AsyncMock())
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_connections", {"same-session": replacement})
    monkeypatch.setattr(PluginHub, "_last_pong", {"same-session": 1.0})
    hub = PluginHub({"type": "websocket"}, receive=AsyncMock(), send=AsyncMock())
    await hub._handle_pong(old, PongMessage(session_id="same-session", challenge="a" * 32))
    assert PluginHub._last_pong["same-session"] == 1.0
    registry.touch.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_heartbeat_reply_does_not_hold_hub_lock(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_send(_message):
        entered.set()
        await release.wait()

    socket = SimpleNamespace(
        state=SimpleNamespace(
            plugin_session_id="first", plugin_features=frozenset({"heartbeat_challenge_v1"})
        ),
        send_json=blocked_send,
    )
    other = SimpleNamespace()
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_connections", {"first": socket, "second": other})
    hub = PluginHub({"type": "websocket"}, receive=AsyncMock(), send=AsyncMock())
    reply = asyncio.create_task(hub._handle_heartbeat_ping(socket, PingMessage(challenge="a" * 32)))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert await asyncio.wait_for(PluginHub._get_connection("second"), 1) is other
    finally:
        release.set()
        await reply
