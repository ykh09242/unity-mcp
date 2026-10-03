"""Adversarial plugin admission and result ownership regressions."""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect, WebSocketState

from core.config import config
from models.unity_response import normalize_unity_response
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted", "_retained_results"):
        monkeypatch.setattr(PluginHub, name, {}, raising=False)
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    monkeypatch.setattr(PluginHub, "REGISTRATION_TIMEOUT", 0.05, raising=False)
    return PluginRegistry()


@pytest.fixture
def client(isolated):
    @asynccontextmanager
    async def lifespan(app):
        PluginHub.configure(isolated)
        yield
        await PluginHub.shutdown()
    app = Starlette(routes=[WebSocketRoute("/plugin", PluginHub)], lifespan=lifespan)
    with TestClient(app) as wire:
        yield wire


def test_unregistered_connections_consume_global_capacity(client, monkeypatch):
    monkeypatch.setattr(PluginRegistry, "MAX_SESSIONS", 1)
    with client.websocket_connect("/plugin") as first:
        first.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/plugin") as rejected:
                rejected.receive_json()
        assert len(PluginHub._admitted) == 1
    assert PluginHub._admitted == {}


def test_silent_connection_has_registration_deadline(client):
    with client.websocket_connect("/plugin") as wire:
        wire.receive_json()
        with pytest.raises(WebSocketDisconnect) as closed:
            wire.receive_json()
        assert closed.value.code == 4408
    assert PluginHub._admitted == {}


def test_registration_must_be_first_message(client):
    with client.websocket_connect("/plugin") as wire:
        wire.receive_json()
        wire.send_json({"type": "pong", "session_id": "inert"})
        with pytest.raises(WebSocketDisconnect) as closed:
            wire.receive_json()
        assert closed.value.code == 4400


def test_registration_processing_obeys_same_deadline(client, monkeypatch):
    async def stalled(self, websocket, payload):
        await asyncio.Event().wait()
    monkeypatch.setattr(PluginHub, "_handle_register", stalled)
    with client.websocket_connect("/plugin") as wire:
        wire.receive_json()
        wire.send_json({"type": "register", "project_hash": "inert"})
        with pytest.raises(WebSocketDisconnect) as closed:
            wire.receive_json()
        assert closed.value.code == 4408
    assert PluginHub._admitted == {}


def test_raw_ceiling_rejects_before_json_decode(client, monkeypatch):
    monkeypatch.setattr(PluginHub, "MAX_RAW_MESSAGE_BYTES", 200, raising=False)
    with client.websocket_connect("/plugin") as wire:
        wire.receive_json()
        wire.send_text(json.dumps({"type": "register", "project_hash": "x" * 201}))
        with pytest.raises(WebSocketDisconnect) as closed:
            wire.receive_json()
        assert closed.value.code == 1009


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bytes", "depth", "nodes"])
async def test_invalid_result_replaced_with_small_error(isolated, monkeypatch, kind):
    PluginHub.configure(isolated)
    ws = AsyncMock()
    PluginHub._connections["owner"] = ws
    future = asyncio.get_running_loop().create_future()
    PluginHub._pending["command"] = {"future": future, "session_id": "owner", "user_id": "alice"}
    result = {"success": True, "data": "ordinary"}
    if kind == "bytes":
        monkeypatch.setattr(PluginHub, "MAX_RESULT_BYTES", 300, raising=False)
        result["data"] = "x" * 301
    elif kind == "depth":
        for _ in range(70):
            result = {"nested": result}
    else:
        result["data"] = [None] * 100_001
    hub = PluginHub({"type": "websocket"}, None, None)
    await hub._handle_command_result(ws, CommandResultMessage(id="command", result=result))
    assert future.done()
    reply = future.result()
    assert reply.get("success") is False, "Unbounded plugin results must never reach the response consumer"
    assert reply["data"]["reason"] == "result_payload_limit"
    assert len(json.dumps(reply)) < 400
    await PluginHub.shutdown()


def test_final_response_bound_before_normalization():
    response = {"success": True, "data": {"wide": [None] * 100_001}}
    reply = normalize_unity_response(response)
    assert reply["success"] is False, "The final response seam must independently bound oversized data"
    assert reply["data"]["reason"] == "response_payload_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["session", "user", "global"])
async def test_retained_results_budget_spans_delayed_consumers(isolated, monkeypatch, scope):
    from models.response_limits import ResponseOwner, response_owner, response_size
    PluginHub.configure(isolated)
    monkeypatch.setattr(config, "http_remote_hosted", True)
    first_owner = ResponseOwner()
    token = response_owner.set(first_owner)
    hub = PluginHub({"type": "websocket"}, None, None)
    result = {"success": True, "data": "base64-preview" * 100}
    charge = response_size(result)
    ceiling = {"session": "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
               "user": "MAX_RETAINED_RESULT_BYTES_PER_USER",
               "global": "MAX_RETAINED_RESULT_BYTES"}[scope]
    monkeypatch.setattr(PluginHub, ceiling, charge + 100)

    async def socket(session, user):
        await isolated.register(session, session, session, "test", user_id=user)
        ws = AsyncMock()
        ws.client_state = ws.application_state = WebSocketState.CONNECTED
        PluginHub._connections[session] = ws
        PluginHub._last_pong[session] = time.monotonic()
        async def answer(payload):
            await hub._handle_command_result(ws, CommandResultMessage(id=payload["id"], result=result))
        ws.send_json.side_effect = answer
        return ws

    await socket("a", "alice")
    second = "a" if scope == "session" else "b"
    if second == "b":
        await socket("b", "alice" if scope == "user" else "bob")
    try:
        assert (await PluginHub.send_command("a", "run_tests", {}))["success"]
        assert len(PluginHub._retained_results) == 1
        # A finished transport command is still owned by its delayed response consumer.
        reply = await PluginHub.send_command(second, "run_tests", {})
        assert reply["data"]["reason"] == "result_capacity"
        assert len(PluginHub._retained_results) == 1
        first_owner.release()
        assert (await PluginHub.send_command(second, "run_tests", {}))["success"]
    finally:
        first_owner.release()
        response_owner.reset(token)
        await PluginHub.shutdown()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_final_mcp_content_and_structured_copies_have_independent_limit(monkeypatch):
    from fastmcp import FastMCP, Client
    import transport.response_limit_middleware as middleware
    from models.response_limits import response_size
    server = FastMCP("inert-output-bound")
    server.add_middleware(middleware.ResponseLimitMiddleware())
    monkeypatch.setattr(middleware, "MAX_RESPONSE_BYTES", 35_768)
    result = {"success": True, "data": "x" * 1600}
    assert response_size(result, max_bytes=3000) is not None
    @server.tool
    async def preview() -> dict:
        return result
    async with Client(server) as sdk:
        reply = await sdk.call_tool("preview", raise_on_error=False)
    assert reply.is_error
    assert reply.structured_content["data"]["reason"] == "response_payload_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("binary", [False, True])
async def test_final_resource_text_and_base64_envelopes_have_independent_limit(monkeypatch, binary):
    from fastmcp import FastMCP, Client
    import transport.response_limit_middleware as middleware
    server = FastMCP("inert-resource-bound")
    server.add_middleware(middleware.ResponseLimitMiddleware())
    monkeypatch.setattr(middleware, "MAX_RESPONSE_BYTES", 35_768)
    @server.resource("inert://preview")
    async def preview():
        # Text escaping or binary base64 expands a plugin-normalized resource.
        return b"x" * 2500 if binary else "\u0001" * 1600
    async with Client(server) as sdk:
        reply = await sdk.read_resource("inert://preview")
    assert "response_payload_limit" in reply[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['{"data":' + '[' * 70 + '0' + ']' * 70 + '}',
                                    '{"data":[' + ','.join(['0'] * 100_001) + ']}'], ids=["depth", "nodes"])
async def test_raw_graph_rejected_before_json_decoder(isolated, monkeypatch, raw):
    import transport.plugin_hub as module
    def forbidden_decode(value):
        raise AssertionError("JSON decoder must not see an unsupported raw graph")
    monkeypatch.setattr(module.json, "loads", forbidden_decode)
    hub = PluginHub({"type": "websocket"}, None, None)
    ws = AsyncMock()
    assert await hub.decode(ws, {"text": raw}) is None
    ws.close.assert_awaited_once_with(code=1009, reason="Plugin message exceeds supported limits")


def test_ordinary_unicode_and_base64_response_remain_supported():
    from models.response_limits import response_size, bounded_json_text
    result = {"success": True, "data": {"preview": "a" * (4 * 1024 * 1024), "name": "테스트 🎮"}}
    assert response_size(result) is not None
    raw = json.dumps(result, ensure_ascii=False)
    assert bounded_json_text(raw, max_bytes=32 * 1024 * 1024, max_depth=64, max_nodes=100_000) == raw


@pytest.mark.asyncio
async def test_authenticated_socket_admission_is_atomic_and_recoverable(isolated, monkeypatch):
    from services.api_key_service import ApiKeyService, ValidationResult
    PluginHub.configure(isolated)
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(PluginRegistry, "MAX_SESSIONS_PER_USER", 1)
    async def validate(key, **kwargs):
        return ValidationResult(valid=True, user_id=key)
    monkeypatch.setattr(ApiKeyService, "_instance", SimpleNamespace(validate=validate))
    hub = PluginHub({"type": "websocket"}, None, None)
    def socket(user):
        ws = AsyncMock()
        ws.headers = {"X-API-Key": user}
        ws.state = SimpleNamespace()
        ws.client = SimpleNamespace(host="inert")
        ws.application_state = WebSocketState.CONNECTED
        return ws
    a, duplicate, b = socket("alice"), socket("alice"), socket("bob")
    await asyncio.gather(hub.on_connect(a), hub.on_connect(duplicate), hub.on_connect(b))
    assert sum(ws.accept.await_count for ws in (a, duplicate)) == 1
    b.accept.assert_awaited_once()
    # In a real dispatch finally the rejected socket is immediately removed.
    rejected = a if not a.accept.await_count else duplicate
    await hub.on_disconnect(rejected, 4429)
    accepted = duplicate if rejected is a else a
    await hub.on_disconnect(accepted, 1000)
    replacement = socket("alice")
    await hub.on_connect(replacement)
    replacement.accept.assert_awaited_once()
    await hub.on_disconnect(replacement, 1000)
    await hub.on_disconnect(b, 1000)
    assert PluginHub._admitted == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "cancel"])
async def test_connect_failure_releases_reserved_capacity(isolated, monkeypatch, failure):
    PluginHub.configure(isolated)
    hub = PluginHub({"type": "websocket"}, None, None)
    ws = AsyncMock()
    ws.state = SimpleNamespace()
    entered = asyncio.Event()
    async def failing(socket):
        entered.set()
        if failure == "exception":
            raise RuntimeError("inert welcome failure")
        await asyncio.Event().wait()
    monkeypatch.setattr(hub, "_connect_authenticated", failing)
    task = asyncio.create_task(hub.on_connect(ws))
    await entered.wait()
    if failure == "cancel":
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert PluginHub._admitted == {}


@pytest.mark.asyncio
async def test_duplicate_results_and_repeated_offenders_do_not_retain_payloads(isolated, monkeypatch):
    PluginHub.configure(isolated)
    hub = PluginHub({"type": "websocket"}, None, None)
    ws = AsyncMock()
    ws.state = SimpleNamespace()
    PluginHub._connections["owner"] = ws
    monkeypatch.setattr(PluginHub, "MAX_RESULT_BYTES", 100)
    for command in ("one", "two"):
        future = asyncio.get_running_loop().create_future()
        PluginHub._pending[command] = {"future": future, "session_id": "owner"}
        payload = CommandResultMessage(id=command, result={"data": "x" * 101})
        await hub._handle_command_result(ws, payload)
        await hub._handle_command_result(ws, payload)
        assert future.result()["data"]["reason"] == "result_payload_limit"
    assert PluginHub._retained_results == {}
    ws.close.assert_awaited_once_with(code=1009, reason="Repeated plugin result limit violation")
