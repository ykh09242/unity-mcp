"""Real SDK delivery and suspended WebSocket frame lifetime regressions."""

import asyncio
import gc
import json
import time
import weakref
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketState

from core.config import config
from transport.charge_ledger import ChargeLedger
from transport.models import CommandResultMessage
from models.response_limits import ResponseOwner, response_owner, response_size
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.response_limit_middleware import (
    ResponseLimitMiddleware,
    ResponseRetentionMiddleware,
    _http_response_owners,
    _owner_key,
)
from test_plugin_response_delivery import Wire, remote_delivery  # pylint: disable=unused-import


@pytest.mark.asyncio
@pytest.mark.parametrize("json_response", [True, False])
@pytest.mark.parametrize("post_exit", ["complete", "cancel"])
async def test_standalone_get_disconnect_preserves_blocked_post_charge(
    remote_delivery,
    monkeypatch,
    json_response,
    post_exit,
):
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
    monkeypatch.setattr(
        PluginHub, "MAX_RETAINED_RESULT_BYTES_PER_USER", response_size(result) + 100
    )

    async def answer(payload):
        await hub._handle_command_result(ws, CommandResultMessage(id=payload["id"], result=result))

    ws.send_json.side_effect = answer
    server = UnityMCP("inert-independent-http-streams")
    server.add_middleware(ResponseLimitMiddleware())

    @server.tool
    async def preview() -> dict:
        return await PluginHub.send_command("unity", "run_tests", {})

    app = server.http_app(transport="http", json_response=json_response)
    stream_task = blocked_task = None
    try:
        async with app.router.lifespan_context(app):
            init = Wire(
                app,
                "POST",
                "/mcp",
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "inert-lifetime", "version": "1"},
                    },
                },
            )
            await asyncio.wait_for(init.run(), 2)
            start = next(msg for msg in init.sent if msg["type"] == "http.response.start")
            headers = [
                (b"mcp-session-id", dict(start["headers"])[b"mcp-session-id"]),
                (b"mcp-protocol-version", b"2025-03-26"),
            ]
            initialized = Wire(
                app,
                "POST",
                "/mcp",
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers,
            )
            await asyncio.wait_for(initialized.run(), 2)
            call = {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "preview", "arguments": {}},
            }
            blocked = Wire(app, "POST", "/mcp", call, headers, blocked_id=9)
            blocked_task = asyncio.create_task(blocked.run())
            await asyncio.wait_for(blocked.blocked.wait(), 2)
            charge = PluginHub._retained_results.total_bytes
            assert charge > 0 and len(_http_response_owners) == 1
            for _ in range(2):
                stream = Wire(app, "GET", "/mcp", headers=headers)
                stream_task = asyncio.create_task(stream.run())
                await asyncio.wait_for(stream.changed.wait(), 2)
                stream_start = next(
                    msg for msg in stream.sent if msg["type"] == "http.response.start"
                )
                assert stream_start["status"] == 200
                assert dict(stream_start["headers"])[b"content-type"].startswith(
                    b"text/event-stream"
                )
                stream.disconnect.set()
                await asyncio.wait_for(stream_task, 2)
                assert not blocked_task.done() and not blocked.resume.is_set()
                assert PluginHub._retained_results.total_bytes == charge, (
                    "An unrelated GET disconnect released a still-blocked POST result"
                )
                assert len(_http_response_owners) == 1
            second = Wire(app, "POST", "/mcp", {**call, "id": 10}, headers)
            await asyncio.wait_for(second.run(), 2)
            assert b"result_capacity" in b"".join(msg.get("body", b"") for msg in second.sent)
            assert PluginHub._retained_results.total_bytes == charge
            if post_exit == "cancel":
                blocked_task.cancel()
                await asyncio.gather(blocked_task, return_exceptions=True)
            else:
                blocked.resume.set()
                await asyncio.wait_for(blocked_task, 2)
            assert PluginHub._retained_results == {} and _http_response_owners == {}
            recovered = Wire(app, "POST", "/mcp", {**call, "id": 11}, headers)
            await asyncio.wait_for(recovered.run(), 2)
            assert b"preview" in b"".join(msg.get("body", b"") for msg in recovered.sent)
            delete = Wire(app, "DELETE", "/mcp", headers=headers)
            await asyncio.wait_for(delete.run(), 2)
            assert (
                next(msg for msg in delete.sent if msg["type"] == "http.response.start")["status"]
                == 200
            )
    finally:
        for task in (stream_task, blocked_task):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await PluginHub.shutdown()


class TrackedText(str):
    """Weak-referenceable text lets the test observe raw frame collection."""


class TrackedDict(dict):
    """Preserve the actual decoder graph while observing its outer reference."""


@pytest.mark.asyncio
@pytest.mark.parametrize("session_header", [False, True])
async def test_generic_sse_get_without_legacy_endpoint_does_not_release_session_owners(
    session_header,
):
    session = "a" * 32
    scope = {
        "type": "http",
        "method": "GET",
        "state": {},
        "headers": [(b"mcp-session-id", session.encode())] if session_header else [],
        "query_string": b"" if session_header else f"session_id={session}".encode(),
    }
    ledger = {"blocked-post": 100}
    owner = ResponseOwner()
    owner.entries.append((ledger, "blocked-post"))
    key = _owner_key(scope, session, 9)
    _http_response_owners[key] = [owner]

    async def stream(owned_scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": (f'event: message\r\ndata: {{"session_id":"{session}"}}\r\n\r\n'.encode()),
                "more_body": True,
            }
        )

    try:
        await ResponseRetentionMiddleware(stream)(scope, AsyncMock(), AsyncMock())
        assert ledger == {"blocked-post": 100}
        assert _http_response_owners[key] == [owner]
    finally:
        owner.release()
        _http_response_owners.pop(key, None)


@pytest.fixture
def dispatch_hub(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted"):
        monkeypatch.setattr(PluginHub, name, {})
    monkeypatch.setattr(PluginHub, "_retained_results", ChargeLedger())
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    return PluginRegistry()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_result", [False, True])
@pytest.mark.parametrize("raw_mode", ["tracked_text", "text", "bytes"])
async def test_idle_dispatch_drops_raw_and_decoded_frames_after_handling(
    dispatch_hub,
    monkeypatch,
    invalid_result,
    raw_mode,
):
    PluginHub.configure(dispatch_hub)
    inbound, outbound = asyncio.Queue(), asyncio.Queue()
    waiting = asyncio.Event()
    references = []

    async def receive():
        if inbound.empty():
            waiting.set()
        return await inbound.get()

    async def send(message):
        await outbound.put(message)

    original_decode = PluginHub.decode

    async def decode(self, websocket, message):
        decoded = await original_decode(self, websocket, message)
        if isinstance(decoded, dict) and decoded.get("type") == "command_result":
            decoded = TrackedDict(decoded)
            references.append(weakref.ref(decoded))
        return decoded

    monkeypatch.setattr(PluginHub, "decode", decode)
    hub = PluginHub({"type": "websocket", "path": "/hub/plugin", "headers": []}, receive, send)
    await inbound.put({"type": "websocket.connect"})
    task = asyncio.create_task(hub.dispatch())
    owner = ResponseOwner()
    command = None
    try:
        await asyncio.wait_for(outbound.get(), 2)  # accept
        await asyncio.wait_for(outbound.get(), 2)  # welcome
        await inbound.put(
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "register", "project_hash": "inert"}),
            }
        )
        registered = json.loads((await asyncio.wait_for(outbound.get(), 2))["text"])
        session = registered["session_id"]
        token = response_owner.set(owner)
        try:
            command = asyncio.create_task(PluginHub.send_command(session, "run_tests", {}))
        finally:
            response_owner.reset(token)
        sent = json.loads((await asyncio.wait_for(outbound.get(), 2))["text"])
        payload = {
            "type": "command_result",
            "id": sent["id"],
            "result": {"success": True, "data": {"preview": "x" * 1024 * 1024}},
        }
        if invalid_result:
            payload.pop("id")  # Real on_receive catches Pydantic validation failure.
        raw = json.dumps(payload)
        if raw_mode == "tracked_text":
            raw = TrackedText(raw)
        elif raw_mode == "bytes":
            raw = raw.encode()
        raw_reference = weakref.ref(raw) if raw_mode == "tracked_text" else None
        waiting.clear()
        await inbound.put(
            {"type": "websocket.receive", "bytes" if raw_mode == "bytes" else "text": raw}
        )
        del raw, payload
        await asyncio.wait_for(waiting.wait(), 2)
        if not invalid_result:
            reply = await asyncio.wait_for(command, 2)
            assert len(reply["data"]["preview"]) == 1024 * 1024
            assert PluginHub._retained_results.total_bytes > 0
            del reply
            owner.release()
        gc.collect()
        assert PluginHub._retained_results.total_bytes == 0
        assert not task.done(), "The dispatcher must be suspended on its next receive"
        if raw_reference is not None:
            assert raw_reference() is None, (
                "Idle dispatch retains the previous raw JSON frame outside result accounting"
            )
        frame = task.get_coro().cr_frame
        assert frame.f_locals.get("message") is None and frame.f_locals.get("data") is None, (
            "Suspended dispatch must drop raw and decoded references for exact str/bytes paths too"
        )
        assert len(references) == 1 and references[0]() is None, (
            "Idle dispatch retains the previous decoded graph"
        )
    finally:
        owner.release()
        if command is not None and not command.done():
            command.cancel()
        if command is not None:
            await asyncio.gather(command, return_exceptions=True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await PluginHub.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_site", ["decode", "on_receive"])
async def test_exceptional_dispatch_drops_frame_locals_before_disconnect_cleanup(
    dispatch_hub, monkeypatch, failure_site
):
    PluginHub.configure(dispatch_hub)
    inbound = asyncio.Queue()
    cleanup_started, finish_cleanup = asyncio.Event(), asyncio.Event()
    cleanup = PluginHub.on_disconnect

    async def blocked_cleanup(self, websocket, code):
        cleanup_started.set()
        await finish_cleanup.wait()
        await cleanup(self, websocket, code)

    async def fail(*args):
        raise RuntimeError("inert frame failure")

    monkeypatch.setattr(PluginHub, "on_disconnect", blocked_cleanup)
    monkeypatch.setattr(PluginHub, failure_site, fail)
    await inbound.put({"type": "websocket.connect"})
    await inbound.put(
        {
            "type": "websocket.receive",
            "text": json.dumps({"type": "register", "project_hash": "inert"}),
        }
    )
    hub = PluginHub(
        {"type": "websocket", "path": "/hub/plugin", "headers": []}, inbound.get, AsyncMock()
    )
    task = asyncio.create_task(hub.dispatch())
    try:
        await asyncio.wait_for(cleanup_started.wait(), 2)
        frame = task.get_coro().cr_frame
        assert frame.f_locals.get("message") is None and frame.f_locals.get("data") is None, (
            "Dispatch exception path retains frame locals while disconnect cleanup is suspended"
        )
        finish_cleanup.set()
        with pytest.raises(RuntimeError, match="inert frame failure"):
            await asyncio.wait_for(task, 2)
    finally:
        finish_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await PluginHub.shutdown()
