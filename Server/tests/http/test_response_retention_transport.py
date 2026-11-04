"""Real SDK ownership across modern POSTs and configured legacy SSE endpoints."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fastmcp
import pytest
import pytest_asyncio
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketState

from tests.http.test_mcp_protocol_wire import MODERN_VERSION, _body
from tests.http.test_plugin_response_delivery import (
    Wire,
    remote_delivery,  # pylint: disable=unused-import
)
from tests.timing import await_signal, watchdog_timeout
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
from transport.response_limit_middleware import ResponseLimitMiddleware, _http_response_owners


@pytest_asyncio.fixture
async def plugin_result_delivery(remote_delivery):
    """Use production command admission/completion with only the Unity socket faked."""
    registry = PluginRegistry()
    PluginHub.configure(registry)
    await registry.register("unity", "inert", "inert", "test", user_id="alice")
    ws = AsyncMock()
    ws.client_state = ws.application_state = WebSocketState.CONNECTED
    ws.state = SimpleNamespace(user_id="alice")
    plugin_lease = (await remote_delivery.validate("plugin-key")).lease
    assert plugin_lease is not None
    setattr(ws.state, AUTHENTICATED_LEASE_STATE, plugin_lease)
    PluginHub._connections["unity"] = ws
    PluginHub._last_pong["unity"] = time.monotonic()
    hub = PluginHub({"type": "websocket"}, None, None)

    async def answer(payload):
        await hub._handle_command_result(
            ws,
            CommandResultMessage(
                id=payload["id"], result={"success": True, "data": {"preview": "a" * 10_000}}
            ),
        )

    ws.send_json.side_effect = answer
    try:
        yield
    finally:
        await PluginHub.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("json_response", [True, False])
@pytest.mark.parametrize("query", [False, True])
@pytest.mark.parametrize("first_exit", ["complete", "cancel"])
@pytest.mark.parametrize(
    ("transport", "path", "mount"),
    [
        ("http", "/mcp", ""),
        ("streamable-http", "/custom/control", "/mounted"),
        ("http", "/messages/", ""),
    ],
)
async def test_modern_duplicate_ids_release_each_http_request(
    plugin_result_delivery, json_response, query, first_exit, transport, path, mount
):
    from main import UnityMCP

    # Given: both modern requests enter before either response owner is registered.
    server = UnityMCP("inert-modern-duplicate-id")
    server.add_middleware(ResponseLimitMiddleware())
    entered = asyncio.Barrier(2)

    @server.tool
    async def preview() -> dict:
        await entered.wait()
        return await PluginHub.send_command("unity", "run_tests", {})

    http_app = server.http_app(transport=transport, path=path, json_response=json_response)
    app = Starlette(routes=[Mount(mount, app=http_app)]) if mount else http_app
    url = mount + path + ("?session_id=review-only" if query else "")
    call = {**_body("tools/call", {"name": "preview", "arguments": {}}), "id": 9}
    headers = [
        (b"mcp-protocol-version", MODERN_VERSION.encode()),
        (b"mcp-method", b"tools/call"),
        (b"mcp-name", b"preview"),
    ]
    wires = [Wire(app, "POST", url, call, headers, blocked_id=9) for _ in range(2)]
    tasks = []
    try:
        async with http_app.router.lifespan_context(http_app):
            tasks = [asyncio.create_task(wire.run()) for wire in wires]
            for wire in wires:
                await await_signal(wire.blocked, "modern response blocked", producers=tasks)
            charge = PluginHub._retained_results.total_bytes
            assert charge > 0 and len(PluginHub._retained_results) == 2
            assert sum(map(len, _http_response_owners.values())) == 2

            # When: one actual ASGI delivery completes/cancels while the other stays blocked.
            if first_exit == "cancel":
                tasks[0].cancel()
                await asyncio.gather(tasks[0], return_exceptions=True)
            else:
                wires[0].resume.set()
                await asyncio.wait_for(tasks[0], watchdog_timeout())

            # Then: only that request's reservation is released, and final delivery drains both.
            assert not tasks[1].done()
            first_remaining = len(PluginHub._retained_results)
            first_charge = PluginHub._retained_results.total_bytes
            first_owners = sum(map(len, _http_response_owners.values()))
            wires[1].resume.set()
            await asyncio.wait_for(tasks[1], watchdog_timeout())
            assert PluginHub._retained_results.total_bytes == 0
            assert _http_response_owners == {}
            assert first_remaining == first_owners == 1
            assert 0 < first_charge < charge
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("json_response", [True, False])
async def test_rejected_modern_duplicate_cannot_release_blocked_post(
    plugin_result_delivery, json_response
):
    from main import UnityMCP

    server = UnityMCP("inert-late-duplicate-id")
    server.add_middleware(ResponseLimitMiddleware())

    @server.tool
    async def preview() -> dict:
        return await PluginHub.send_command("unity", "run_tests", {})

    app = server.http_app(transport="http", path="/mcp", json_response=json_response)
    url = "/mcp?session_id=shared"
    call = {**_body("tools/call", {"name": "preview", "arguments": {}}), "id": 9}
    headers = [
        (b"mcp-protocol-version", MODERN_VERSION.encode()),
        (b"mcp-method", b"tools/call"),
        (b"mcp-name", b"preview"),
    ]
    original = Wire(app, "POST", url, call, headers, blocked_id=9)
    duplicate = Wire(app, "POST", url, call, headers)
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(original.run())
        try:
            await await_signal(original.blocked, "original response blocked", producers=[task])
            charge = PluginHub._retained_results.total_bytes
            assert charge > 0

            await asyncio.wait_for(duplicate.run(), watchdog_timeout())
            error_frame = await duplicate.until_body(b'"error"')
            assert b'"id":9' in error_frame.replace(b" ", b"")
            assert not task.done()
            assert PluginHub._retained_results.total_bytes == charge
            assert sum(map(len, _http_response_owners.values())) == 1
        finally:
            original.resume.set()
            await asyncio.wait_for(task, watchdog_timeout())
        assert PluginHub._retained_results.total_bytes == 0
        assert _http_response_owners == {}


class PostCompletionGate:
    """Hold a completed legacy POST until its real stream writer has a charged result."""

    def __init__(self, app: ASGIApp, release: asyncio.Event) -> None:
        self.app = app
        self.release = release

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)
        if scope["type"] == "http" and b"hold_post=1" in scope.get("query_string", b""):
            await self.release.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_exit", ["complete", "disconnect"])
@pytest.mark.parametrize(
    ("message_path", "stream_path", "mount"),
    [("/messages/", "/sse", ""), ("/custom/inbox/", "/events", "/mounted")],
)
async def test_legacy_post_202_retains_result_until_configured_stream_delivery(
    plugin_result_delivery, monkeypatch, stream_exit, message_path, stream_path, mount
):
    from main import UnityMCP

    # Given: a real legacy SSE app, including a nondefault mounted message endpoint.
    monkeypatch.setattr(fastmcp.settings, "message_path", message_path)
    server = UnityMCP("inert-legacy-post-retention")
    server.add_middleware(ResponseLimitMiddleware())

    @server.tool
    async def preview() -> dict:
        return await PluginHub.send_command("unity", "run_tests", {})

    release_post = asyncio.Event()
    http_app = server.http_app(
        transport="sse",
        path=stream_path,
        middleware=[Middleware(PostCompletionGate, release=release_post)],
    )
    # The built app keeps its own endpoint even if process defaults later change.
    monkeypatch.setattr(fastmcp.settings, "message_path", "/another-app/messages/")
    app = Starlette(routes=[Mount(mount, app=http_app)]) if mount else http_app
    stream = Wire(app, "GET", mount + stream_path, blocked_id=9)
    stream_task = post_task = None
    try:
        async with http_app.router.lifespan_context(http_app):
            stream_task = asyncio.create_task(stream.run())
            endpoint = await stream.until_body(b"event: endpoint")
            post_url = endpoint.decode().split("data: ", 1)[1].splitlines()[0]
            assert post_url.startswith(mount + message_path + "?session_id=")
            initialize = Wire(
                app,
                "POST",
                post_url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "inert-legacy-post", "version": "1"},
                    },
                },
            )
            await asyncio.wait_for(initialize.run(), watchdog_timeout())
            await stream.until_body(b'"id":1')
            initialized = Wire(
                app,
                "POST",
                post_url,
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
            )
            await asyncio.wait_for(initialized.run(), watchdog_timeout())
            post = Wire(
                app,
                "POST",
                post_url + "&hold_post=1",
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "preview", "arguments": {}},
                },
            )
            post_task = asyncio.create_task(post.run())
            await await_signal(stream.blocked, "legacy response blocked", producers=[stream_task])
            charge = PluginHub._retained_results.total_bytes
            assert charge > 0 and sum(map(len, _http_response_owners.values())) == 1

            # When: POST 202 finishes while its result is still blocked on the GET stream.
            release_post.set()
            await asyncio.wait_for(post_task, watchdog_timeout())

            # Then: POST completion cannot release the stream's result reservation.
            assert (
                next(msg for msg in post.sent if msg["type"] == "http.response.start")["status"]
                == 202
            )
            assert PluginHub._retained_results.total_bytes == charge
            assert sum(map(len, _http_response_owners.values())) == 1
            if stream_exit == "disconnect":
                stream.disconnect.set()
            else:
                stream.resume.set()
                # A subsequent response proves the prior frame was accepted by ASGI.
                ping = Wire(app, "POST", post_url, {"jsonrpc": "2.0", "id": 10, "method": "ping"})
                await asyncio.wait_for(ping.run(), watchdog_timeout())
                await stream.until_body(b'"id":10')
                assert PluginHub._retained_results.total_bytes == 0
                assert _http_response_owners == {}
                stream.disconnect.set()
            await asyncio.wait_for(stream_task, watchdog_timeout())
            assert PluginHub._retained_results.total_bytes == 0
            assert _http_response_owners == {}
    finally:
        stream.disconnect.set()
        release_post.set()
        for task in (post_task, stream_task):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
