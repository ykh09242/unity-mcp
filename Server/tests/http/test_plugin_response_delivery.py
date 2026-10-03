"""Installed SDK HTTP/SSE response ownership under actual blocked ASGI sends."""
import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketState

from core.config import config
from models.response_limits import response_size
from services.api_key_service import ApiKeyService, ValidationResult
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.response_limit_middleware import ResponseLimitMiddleware, _http_response_owners


def test_response_envelope_parser_preserves_identity_and_fails_closed():
    from transport.response_limit_middleware import _frame_id, _owner_key
    assert _frame_id(b'{"jsonrpc":"2.0","id":9,"result":{}}') == 9
    assert _frame_id(b'event: message\r\ndata: {"jsonrpc":"2.0","id":"9","result":{}}\r\n\r\n') == "9"
    for body in (b'{"jsonrpc":"2.0","method":"notification","params":{"id":9,"result":{}}}',
                 b'{"jsonrpc":"2.0","id":9,', b'{"id":9,"jsonrpc":"2.0","result":{}}'):
        assert _frame_id(body) is None
    from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE
    alice = {"state": {AUTHENTICATED_USER_STATE: "alice"}}
    bob = {"state": {AUTHENTICATED_USER_STATE: "bob"}}
    assert _owner_key(alice, "session", 9) != _owner_key(alice, "session", "9")
    assert _owner_key(alice, "session", 9) != _owner_key(bob, "session", 9)


def test_ambiguous_response_identity_retains_all_owners_until_stream_cleanup():
    from models.response_limits import ResponseOwner
    from transport.response_limit_middleware import _release_http_owner
    ledger = {"one": 1, "two": 2}
    first, second = ResponseOwner(), ResponseOwner()
    first.entries.append((ledger, "one"))
    second.entries.append((ledger, "two"))
    key = ("alice", "session", int, "hashed-id")
    _http_response_owners[key] = [first, second]
    _release_http_owner(key)
    assert len(ledger) == 2
    _release_http_owner(key, closing=True)
    assert ledger == {}
    assert key not in _http_response_owners


@pytest.mark.asyncio
async def test_final_limit_reserves_escaped_long_request_id(monkeypatch):
    from fastmcp import FastMCP, Client
    import transport.response_limit_middleware as module
    from fastmcp.tools.base import ToolResult
    server = FastMCP("inert-long-id-bound")
    guard = module.ResponseLimitMiddleware()
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 50_000)
    # Worst supported ID is 4096 characters, each escaping to six JSON bytes.
    request_id = "\u0001" * 4096
    ordinary = ToolResult(content="x" * 19_000)
    async def next_call(context):
        return ordinary
    reply = await guard.on_call_tool(None, next_call)
    assert reply.is_error
    wire = {"jsonrpc": "2.0", "id": request_id, "result": {
        "content": [item.model_dump() for item in reply.content],
        "structuredContent": reply.structured_content, "isError": reply.is_error}}
    assert len(json.dumps(wire).encode()) < 50_000


@pytest.mark.asyncio
async def test_sdk_translated_exception_after_result_acquisition_releases_owner():
    from models.response_limits import response_owner
    request = SimpleNamespace(scope={"headers": [(b"mcp-session-id", b"session")], "state": {}})
    rc = SimpleNamespace(request=request, request_id="9", _srctx=SimpleNamespace(request_id=9))
    context = SimpleNamespace(fastmcp_context=SimpleNamespace(request_context=rc, session_id="session"))
    ledger = {"command": 1000}
    async def failing(context):
        response_owner.get().entries.append((ledger, "command"))
        raise ValueError("x" * 100_000)
    # Like SDK JSON-RPC dispatch, this caller catches the error and returns
    # normally, so relying only on the task's failed/cancelled status is unsafe.
    try:
        await ResponseLimitMiddleware().on_message(context, failing)
    except ValueError as error:
        assert str(error) == "Unity response could not be completed"
    else:
        pytest.fail("Producer exception must remain an error")
    assert ledger == {}
    assert _http_response_owners == {}


class Wire:
    """ASGI only: no sockets, HTTP clients, DNS or Unity process."""
    def __init__(self, app, method, url, body=None, headers=(), blocked_id=None):
        parts = urlsplit(url)
        self.app = app
        self.body = json.dumps(body).encode() if body is not None else b""
        self.scope = {"type": "http", "http_version": "1.1", "scheme": "http", "method": method,
                      "path": parts.path, "raw_path": parts.path.encode(), "query_string": parts.query.encode(),
                      "headers": [(b"host", b"testserver"), (b"x-api-key", b"alice-key"),
                                  (b"accept", b"application/json, text/event-stream"),
                                  (b"content-type", b"application/json"), *headers],
                      "client": ("127.0.0.1", 100), "server": ("testserver", 80)}
        self.sent = []
        self.received = False
        self.disconnect = asyncio.Event()
        self.blocked = asyncio.Event()
        self.resume = asyncio.Event()
        self.changed = asyncio.Event()
        self.blocked_id = blocked_id

    async def receive(self):
        if not self.received:
            self.received = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        await self.disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(self, message):
        self.sent.append(message)
        self.changed.set()
        body = message.get("body", b"")
        if body and self.blocked_id is not None:
            marker = b'"id":' + str(self.blocked_id).encode()
            if marker in body.replace(b" ", b"") and b'"result"' in body:
                self.blocked.set()
                await self.resume.wait()

    async def run(self):
        await self.app(self.scope, self.receive, self.send)

    async def until_body(self, needle):
        async def wait():
            while True:
                for message in self.sent:
                    body = message.get("body", b"")
                    if needle in body:
                        return body
                self.changed.clear()
                await self.changed.wait()
        return await asyncio.wait_for(wait(), 2)


@pytest.fixture
def remote_delivery(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted", "_retained_results"):
        monkeypatch.setattr(PluginHub, name, {})
    monkeypatch.setattr(ApiKeyService, "_instance", SimpleNamespace(
        validate=AsyncMock(return_value=ValidationResult(valid=True, user_id="alice")), aclose=AsyncMock()))
    _http_response_owners.clear()
    yield
    _http_response_owners.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("query", [False, True])
async def test_real_sdk_retains_results_until_http_or_sse_delivery(remote_delivery, monkeypatch, transport, cancel, query):
    from main import UnityMCP
    registry = PluginRegistry()
    PluginHub.configure(registry)
    await registry.register("unity", "inert", "inert", "test", user_id="alice")
    ws = AsyncMock()
    ws.client_state = ws.application_state = WebSocketState.CONNECTED
    PluginHub._connections["unity"] = ws
    PluginHub._last_pong["unity"] = time.monotonic()
    hub = PluginHub({"type": "websocket"}, None, None)
    result = {"success": True, "data": {"preview": "a" * 4000}}
    monkeypatch.setattr(PluginHub, "MAX_RETAINED_RESULT_BYTES_PER_USER", response_size(result) + 100)
    async def answer(payload):
        await hub._handle_command_result(ws, CommandResultMessage(id=payload["id"], result=result))
    ws.send_json.side_effect = answer
    server = UnityMCP("inert-delivery")
    server.add_middleware(ResponseLimitMiddleware())
    @server.tool
    async def preview() -> dict:
        return await PluginHub.send_command("unity", "run_tests", {})
    @server.tool
    async def failing() -> dict:
        from mcp import McpError
        from mcp.types import ErrorData
        await PluginHub.send_command("unity", "run_tests", {})
        raise McpError(ErrorData(code=-32603, message="Inert producer failure"))
    app = server.http_app(transport=transport, json_response=True)
    stream = stream_task = blocked_task = None
    try:
        async with app.router.lifespan_context(app):
            headers = []
            path = "/mcp?trace=1" if query else "/mcp"
            if transport == "sse":
                stream = Wire(app, "GET", "/sse", blocked_id=9)
                stream_task = asyncio.create_task(stream.run())
                endpoint = await stream.until_body(b"event: endpoint")
                path = endpoint.decode().split("data: ", 1)[1].splitlines()[0]
            initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "inert-delivery", "version": "1"}}}
            init = Wire(app, "POST", path, initialize)
            await asyncio.wait_for(init.run(), 2)
            if transport == "http":
                start = next(message for message in init.sent if message["type"] == "http.response.start")
                session = dict(start["headers"])[b"mcp-session-id"]
                headers = [(b"mcp-session-id", session), (b"mcp-protocol-version", b"2025-03-26")]
            else:
                await stream.until_body(b'"id":1')
            initialized = Wire(app, "POST", path, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
            await asyncio.wait_for(initialized.run(), 2)
            failed = Wire(app, "POST", path, {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                         "params": {"name": "failing", "arguments": {}}}, headers)
            await asyncio.wait_for(failed.run(), 2)
            if transport == "sse":
                await stream.until_body(b'"id":8')
            assert PluginHub._retained_results == {}, "SDK-translated producer errors must release charged results"
            assert _http_response_owners == {}
            call = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "preview", "arguments": {}}}
            blocked = Wire(app, "POST", path, call, headers, blocked_id=9 if transport == "http" else None)
            blocked_task = asyncio.create_task(blocked.run())
            consumer = blocked if transport == "http" else stream
            await asyncio.wait_for(consumer.blocked.wait(), 2)
            if transport == "sse":
                await asyncio.wait_for(blocked_task, 2)  # POST202 completes before GET sends.
            assert PluginHub._pending == {}
            assert len(PluginHub._retained_results) == 1, "Slow final consumer must retain the plugin result reservation"
            assert len(_http_response_owners) == 1
            second = Wire(app, "POST", path, {**call, "id": 10}, headers)
            await asyncio.wait_for(second.run(), 2)
            if transport == "http":
                assert b"result_capacity" in b"".join(msg.get("body", b"") for msg in second.sent)
            assert len(PluginHub._retained_results) == 1
            if cancel:
                if transport == "http":
                    blocked_task.cancel()
                    await asyncio.gather(blocked_task, return_exceptions=True)
                else:
                    stream.disconnect.set()
                    await asyncio.wait_for(stream_task, 2)
            else:
                consumer.resume.set()
                if transport == "http":
                    await asyncio.wait_for(blocked_task, 2)
                else:
                    await stream.until_body(b'"id":10')
            for _ in range(10):
                if not PluginHub._retained_results:
                    break
                await asyncio.sleep(0)
            assert PluginHub._retained_results == {}
            assert _http_response_owners == {}
            if not cancel:
                recovered = Wire(app, "POST", path, {**call, "id": 11}, headers)
                await asyncio.wait_for(recovered.run(), 2)
                if transport == "sse":
                    assert b"preview" in await stream.until_body(b'"id":11')
                else:
                    assert b"preview" in b"".join(msg.get("body", b"") for msg in recovered.sent)
    finally:
        if stream is not None:
            stream.disconnect.set()
        for task in (stream_task, blocked_task):
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        await PluginHub.shutdown()
