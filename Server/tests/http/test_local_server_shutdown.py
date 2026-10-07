"""Atomic local shutdown through real authenticated routes without a live runner."""

import asyncio
import signal
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect, WebSocketState
import uvicorn

from core.config import config
from transport.charge_ledger import ChargeLedger
from transport.models import RegisterMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


HEADERS = {"X-Unity-MCP-Token": "local-test-token"}
BODY = {"instance_token": "owned-test-launch"}
PATH = "/api/server/shutdown"


@pytest.fixture
def managed(monkeypatch):
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", HEADERS["X-Unity-MCP-Token"])
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted"):
        monkeypatch.setattr(PluginHub, name, {})
    monkeypatch.setattr(PluginHub, "_retained_results", ChargeLedger())
    monkeypatch.setattr(PluginHub, "_shutdown_requested", False)
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    app = create_mcp_server(False, managed_instance_token=BODY["instance_token"]).http_app()

    @asynccontextmanager
    async def lifespan(_app):
        PluginHub.configure(PluginRegistry())
        try:
            yield
        finally:
            await PluginHub.shutdown()

    app.router.lifespan_context = lifespan
    # Construct only: never serve, install handlers, bind a port, or signal a process.
    owner = uvicorn.Server(uvicorn.Config(app))
    owner.started = True
    monkeypatch.setattr(signal, "getsignal", lambda _sig: owner.handle_exit)
    with TestClient(app) as client:
        yield client, owner


def test_stop_first_closes_admission_before_owned_runner_exit(managed):
    client, owner = managed
    response = client.post(PATH, headers=HEADERS, json=BODY)
    assert response.json() == {"success": True, "status": "shutdown_requested"}
    assert owner.should_exit
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/hub/plugin", headers=HEADERS):
            pass
    assert closed.value.code == 1013
    assert PluginHub._admitted == {}
    assert PluginHub._connections == {}
    # Repeated owned requests acknowledge the same pending shutdown.
    assert client.post(PATH, headers=HEADERS, json=BODY).json() == response.json()


def test_admission_first_preserves_unregistered_editor(managed):
    client, owner = managed
    with client.websocket_connect("/hub/plugin", headers=HEADERS) as socket:
        socket.receive_json()
        assert PluginHub._connections == {}
        response = client.post(PATH, headers=HEADERS, json=BODY)
        assert response.status_code == 409
        assert response.json()["error"] == "sessions_active"
        assert not owner.should_exit
        assert not PluginHub._shutdown_requested
        socket.send_json({"type": "register", "project_hash": "survives", "project_name": "Editor"})
        assert socket.receive_json()["type"] == "registered"
        assert client.post(PATH, headers=HEADERS, json=BODY).status_code == 409
        assert not owner.should_exit


def test_inflight_unauthenticated_admission_prevents_shutdown(managed, monkeypatch):
    client, owner = managed

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        socket = SimpleNamespace(application_state=WebSocketState.CONNECTING)

        async def authenticate(_self, _socket):
            entered.set()
            await release.wait()
            socket.application_state = WebSocketState.DISCONNECTED

        monkeypatch.setattr(PluginHub, "_connect_authenticated", authenticate)
        hub = PluginHub({"type": "websocket"}, None, None)
        task = asyncio.create_task(hub.on_connect(socket))
        await entered.wait()
        return release, task

    release, task = client.portal.call(scenario)
    try:
        assert PluginHub._admitted
        assert client.post(PATH, headers=HEADERS, json=BODY).status_code == 409
        assert not owner.should_exit
    finally:
        client.portal.call(release.set)

        async def finish():
            await task

        client.portal.call(finish)
    assert PluginHub._admitted == {}


def test_inflight_registration_prevents_shutdown(managed, monkeypatch):
    client, owner = managed
    original = PluginHub._handle_register

    async def events():
        return asyncio.Event(), asyncio.Event()

    entered, release = client.portal.call(events)

    async def paused(self, socket, payload):
        entered.set()
        await release.wait()
        await original(self, socket, payload)

    monkeypatch.setattr(PluginHub, "_handle_register", paused)
    with client.websocket_connect("/hub/plugin", headers=HEADERS) as socket:
        socket.receive_json()
        socket.send_json({"type": "register", "project_hash": "registering"})
        client.portal.call(entered.wait)
        assert client.post(PATH, headers=HEADERS, json=BODY).status_code == 409
        assert not owner.should_exit
        client.portal.call(release.set)
        assert socket.receive_json()["type"] == "registered"


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_runner_failure_rolls_back_admission_reservation(managed, monkeypatch, failure):
    client, owner = managed

    def reject():
        if failure == "exception":
            raise RuntimeError("runner failed")
        return False

    monkeypatch.setattr(
        "transport.local_server_lifecycle.owned_runner_shutdown", lambda _request: reject
    )
    assert client.post(PATH, headers=HEADERS, json=BODY).status_code == 503
    assert not owner.should_exit
    assert not PluginHub._shutdown_requested
    with client.websocket_connect("/hub/plugin", headers=HEADERS) as socket:
        socket.receive_json()


@pytest.mark.parametrize("invalid_owner", ["handler", "application", "not_started"])
def test_unproven_runner_never_claims_closing_state(managed, monkeypatch, invalid_owner):
    client, owner = managed
    if invalid_owner == "handler":
        monkeypatch.setattr(signal, "getsignal", lambda _sig: signal.SIG_DFL)
    elif invalid_owner == "application":
        owner.config.app = object()
    else:
        owner.started = False
    assert client.post(PATH, headers=HEADERS, json=BODY).status_code == 503
    assert not PluginHub._shutdown_requested
    assert not owner.should_exit


@pytest.mark.parametrize(
    "headers,body,status",
    [
        ({}, BODY, 401),
        ({"X-Unity-MCP-Token": "wrong"}, BODY, 401),
        ({**HEADERS, "Origin": "null"}, BODY, 403),
        (HEADERS, {"instance_token": "wrong"}, 403),
        (HEADERS, {"instance_token": 123}, 400),
        (HEADERS, {**BODY, "force": True}, 400),
        (HEADERS, {}, 400),
        (HEADERS, {"instance_token": "x" * 2000}, 413),
    ],
)
def test_both_local_and_launch_auth_required(managed, headers, body, status):
    client, owner = managed
    assert client.post(PATH, headers=headers, json=body).status_code == status
    assert not owner.should_exit
    assert not PluginHub._shutdown_requested


def test_registration_directly_invoked_after_shutdown_is_rejected(managed):
    client, _owner = managed
    assert client.post(PATH, headers=HEADERS, json=BODY).status_code == 200
    socket = SimpleNamespace(state=SimpleNamespace(), close=AsyncMock())
    hub = PluginHub({"type": "websocket"}, None, None)
    client.portal.call(hub._handle_register, socket, RegisterMessage(project_hash="late"))
    socket.close.assert_awaited_once_with(code=1013, reason="Plugin server closing")
    assert PluginHub._connections == {}


def test_busy_admission_lock_has_bounded_shutdown_deadline(managed):
    client, owner = managed
    client.portal.call(PluginHub._lock.acquire)
    try:
        assert client.post(PATH, headers=HEADERS, json=BODY).status_code == 503
        assert not owner.should_exit
        assert not PluginHub._shutdown_requested
    finally:
        client.portal.call(PluginHub._lock.release)


def test_incomplete_request_body_has_bounded_deadline(managed):
    client, owner = managed

    async def scenario():
        async def stalled_body():
            yield b"{"
            await asyncio.Event().wait()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://testserver"
        ) as wire:
            return await wire.post(
                PATH,
                headers={**HEADERS, "Content-Type": "application/json"},
                content=stalled_body(),
            )

    response = client.portal.call(scenario)
    assert response.status_code == 408
    assert not owner.should_exit
    assert not PluginHub._shutdown_requested


def test_manual_launch_has_no_managed_shutdown_identity(managed):
    from main import create_mcp_server

    _client, owner = managed
    app = create_mcp_server(False).http_app()

    @asynccontextmanager
    async def no_startup(_app):
        yield

    app.router.lifespan_context = no_startup
    with TestClient(app) as manual:
        response = manual.post(PATH, headers=HEADERS, json=BODY)
    assert response.status_code == 503
    assert response.json()["error"] == "unmanaged_server"
    assert not owner.should_exit


def test_remote_hosted_app_does_not_expose_shutdown(managed, monkeypatch):
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    app = create_mcp_server(False, managed_instance_token=BODY["instance_token"]).http_app()
    assert PATH not in {getattr(route, "path", None) for route in app.routes}
