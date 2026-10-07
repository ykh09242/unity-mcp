"""Exercise lazy ingress limits using real FastMCP apps and controlled ASGI streams."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import anyio
import httpx
import pytest
from starlette.requests import Request

from core.config import config
from services.api_key_service import ApiKeyService, ValidationResult
from transport.models import SessionDetails, SessionList
from transport.plugin_hub import PluginHub
from transport.request_body_limit_middleware import RequestBodyLimitMiddleware


LIMIT = 256


def scope_for(headers=(), path="/", method="POST", scope_type="http"):
    return {
        "type": scope_type,
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": list(headers),
        "root_path": "",
        "server": ("testserver", 80),
        "client": ("fixture", 1),
        "state": {},
    }


async def invoke(app, scope, chunks=()):
    sent = []
    reads = 0
    iterator = iter(chunks)

    async def receive():
        nonlocal reads
        reads += 1
        return next(iterator, {"type": "http.disconnect"})

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent, reads


def test_declared_lengths_are_rejected_before_app_or_receive():
    async def scenario():
        reached = AsyncMock()
        app = RequestBodyLimitMiddleware(reached, max_body_size=LIMIT)
        for values, status in [
            ([b"257"], 413),
            ([b"9" * 10000], 413),
            ([b"1", b"257"], 413),
            ([b"257", b"1"], 413),
            ([b"1", b"1"], 400),
            ([b"1, 1"], 400),
            ([b"-1"], 400),
            ([b"+1"], 400),
            ([b""], 400),
            ([b"abc"], 400),
        ]:
            sent, reads = await invoke(app, scope_for([(b"content-length", v) for v in values]))
            assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [status]
            assert reads == 0
        reached.assert_not_awaited()

    anyio.run(scenario)


@pytest.mark.parametrize(
    "headers", [[], [(b"content-length", b"1")], [(b"transfer-encoding", b"chunked")]]
)
def test_actual_stream_is_counted_without_forwarding_overflow(headers):
    async def scenario():
        forwarded = []

        async def parser(scope, receive, send):
            try:
                while True:
                    message = await receive()
                    forwarded.append(message)
            except Exception:
                # Same catch-and-reply pattern as FastMCP and /api/command.
                await send({"type": "http.response.start", "status": 500})
                await send({"type": "http.response.body", "body": b"inner failure"})

        sent, reads = await invoke(
            RequestBodyLimitMiddleware(parser, LIMIT),
            scope_for(headers),
            [
                {"type": "http.request", "body": b"x" * 128, "more_body": True},
                {"type": "http.request", "body": b"x" * 128, "more_body": True},
                {"type": "http.request", "body": b"x", "more_body": True},
                {"type": "http.request", "body": b"unread", "more_body": False},
            ],
        )
        assert reads == 3
        assert sum(len(m["body"]) for m in forwarded) == LIMIT
        assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]
        assert b"inner failure" not in b"".join(m.get("body", b"") for m in sent)

    anyio.run(scenario)


def test_passthrough_disconnect_websocket_lifespan_and_streaming_response():
    async def scenario():
        disconnect = {"type": "http.disconnect"}
        received = []

        async def app(scope, receive, send):
            received.append(await receive())
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            await send({"type": "http.response.body", "body": b"last", "more_body": False})

        for scope_type in ["http", "websocket", "lifespan"]:
            sent, reads = await invoke(
                RequestBodyLimitMiddleware(app, LIMIT),
                scope_for(scope_type=scope_type),
                [disconnect],
            )
            assert received[-1] is disconnect
            assert reads == 1
            assert [m.get("body") for m in sent[1:]] == [b"first", b"last"]

        async def no_read(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"event"})

        sent, reads = await invoke(
            RequestBodyLimitMiddleware(no_read, LIMIT), scope_for(method="GET")
        )
        assert reads == 0
        assert sent[0]["status"] == 200

    anyio.run(scenario)


def test_exact_byte_ceiling_and_padded_decimal_length_are_allowed():
    async def scenario():
        async def app(scope, receive, send):
            body = await Request(scope, receive).body()
            assert len(body) == LIMIT
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"ok"})

        sent, reads = await invoke(
            RequestBodyLimitMiddleware(app, LIMIT),
            scope_for(
                [
                    (b"content-length", b"000256"),
                ]
            ),
            [
                {"type": "http.request", "body": b"x" * 128, "more_body": True},
                {"type": "http.request", "body": b"x" * 128, "more_body": False},
            ],
        )
        assert sent[0]["status"] == 200 and reads == 2

    anyio.run(scenario)


def test_started_response_is_never_replaced_and_cancellation_propagates():
    async def scenario():
        sent = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            try:
                await receive()
            except Exception:
                await send({"type": "http.response.start", "status": 500})

        async def receive():
            return {"type": "http.request", "body": b"x" * (LIMIT + 1)}

        async def send(message):
            sent.append(message)

        with pytest.raises(Exception, match="HTTP request body exceeds"):
            await RequestBodyLimitMiddleware(app, LIMIT)(scope_for(), receive, send)
        assert [m["status"] for m in sent] == [200]

        async def cancelled(scope, receive, send):
            raise anyio.get_cancelled_exc_class()()

        with pytest.raises(anyio.get_cancelled_exc_class()):
            await RequestBodyLimitMiddleware(cancelled, LIMIT)(scope_for(), receive, send)

    anyio.run(scenario)


@pytest.fixture(params=[False, True], ids=["local", "remote"])
def public_app(request, monkeypatch):
    import main

    remote = request.param
    monkeypatch.setattr(main, "MAX_HTTP_REQUEST_BYTES", LIMIT, raising=False)
    monkeypatch.setattr(config, "http_remote_hosted", remote)
    monkeypatch.setattr(config, "http_behind_tls_proxy", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "local_auth_token", "fixture-token")
    monkeypatch.setattr(config, "api_key_validation_url", None)
    validator = AsyncMock(return_value=ValidationResult(valid=True, user_id="fixture-user"))
    monkeypatch.setattr(
        ApiKeyService, "_instance", SimpleNamespace(validate=validator, aclose=AsyncMock())
    )
    headers = (
        [(b"x-api-key", b"fixture-key")] if remote else [(b"x-unity-mcp-token", b"fixture-token")]
    )
    headers += [
        (b"content-type", b"application/json"),
        (b"accept", b"application/json, text/event-stream"),
    ]
    app = main.create_mcp_server(False).http_app(json_response=True, stateless_http=True)
    return app, headers, remote


@pytest.mark.parametrize("declaration", [None, b"1", b"257", b"duplicate", b"invalid"])
def test_public_mcp_oversize_never_materializes_or_parses(public_app, monkeypatch, declaration):
    app, headers, _ = public_app
    completed_bodies = []
    original = Request.body

    async def body(request):
        result = await original(request)
        completed_bodies.append(result)
        return result

    monkeypatch.setattr(Request, "body", body)
    if declaration == b"duplicate":
        headers += [(b"content-length", b"1"), (b"content-length", b"257")]
    elif declaration is not None:
        headers += [(b"content-length", declaration)]

    async def scenario():
        async with app.router.lifespan_context(app):
            with anyio.fail_after(5):
                sent, reads = await invoke(
                    app,
                    scope_for(headers, "/mcp"),
                    [
                        {"type": "http.request", "body": b"x" * 128, "more_body": True},
                        {"type": "http.request", "body": b"x" * 129, "more_body": True},
                        {"type": "http.request", "body": b"unread", "more_body": False},
                    ],
                )
        assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [
            400 if declaration == b"invalid" else 413
        ]
        assert reads == (2 if declaration in [None, b"1"] else 0)
        assert completed_bodies == []

    anyio.run(scenario)


def test_public_auth_health_and_normal_initialize(public_app):
    app, headers, _ = public_app
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "fixture", "version": "1"},
        },
    }

    async def scenario():
        denied, reads = await invoke(app, scope_for([(b"content-length", b"999")], "/mcp"))
        assert denied[0]["status"] == 401 and reads == 0
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                assert (await client.get("/health")).status_code == 200
                response = await client.post(
                    "/mcp", headers=[(k.decode(), v.decode()) for k, v in headers], json=payload
                )
                assert response.status_code == 200, response.text
                assert response.json()["id"] == 1 and "result" in response.json()

    anyio.run(scenario)


def test_public_local_control_limit_and_normal_command(public_app, monkeypatch):
    app, headers, remote = public_app
    if remote:
        return  # Remote deployment intentionally has no CLI control route.
    sessions = AsyncMock(return_value=SessionList(sessions={}))
    monkeypatch.setattr(PluginHub, "get_sessions", sessions)

    async def scenario():
        for lengths in [[], [(b"content-length", b"1")], [(b"content-length", b"257")]]:
            sent, _ = await invoke(
                app,
                scope_for(headers + lengths, "/api/command"),
                [
                    {"type": "http.request", "body": b"x" * 129, "more_body": True},
                    {"type": "http.request", "body": b"x" * 128, "more_body": False},
                ],
            )
            assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]
        sessions.assert_not_awaited()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/command",
                headers=[(k.decode(), v.decode()) for k, v in headers],
                json={"type": "read_console"},
            )
            assert response.status_code == 503
            assert "No Unity instances" in response.json()["error"]
        sessions.assert_awaited_once()
        sessions.return_value = SessionList(
            sessions={
                "fixture-session": SessionDetails(
                    project="fixture",
                    hash="fixture-hash",
                    unity_version="6000",
                    connected_at="now",
                )
            }
        )
        dispatch = AsyncMock(return_value={"success": True, "data": "fixture-result"})
        monkeypatch.setattr(PluginHub, "send_command", dispatch)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/command",
                headers=[(k.decode(), v.decode()) for k, v in headers],
                json={"type": "read_console"},
            )
            assert response.status_code == 200
            assert response.json() == {"success": True, "data": "fixture-result"}
        dispatch.assert_awaited_once_with("fixture-session", "read_console", {})

    anyio.run(scenario)


def test_baseline_probe_public_valid_oversize(public_app, monkeypatch):
    """Same tiny valid JSON payload on baseline and fixed public HTTP surfaces."""
    app, headers, remote = public_app
    baseline = os.environ.get("H_BASELINE") == "1"
    monkeypatch.setattr(PluginHub, "get_sessions", AsyncMock(return_value=SessionList(sessions={})))
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "fixture", "version": "1"},
        },
        "padding": "x" * LIMIT,
    }

    async def scenario():
        async with app.router.lifespan_context(app):
            for path in ["/mcp"] if remote else ["/mcp", "/api/command"]:
                body = json.dumps(
                    payload if path == "/mcp" else {"type": "read_console", "padding": "x" * LIMIT}
                ).encode()
                for lengths in [
                    [],
                    [(b"content-length", b"1")],
                    [(b"content-length", str(len(body)).encode())],
                ]:
                    with anyio.fail_after(5):
                        sent, reads = await invoke(
                            app,
                            scope_for(headers + lengths, path),
                            [
                                {"type": "http.request", "body": body[:128], "more_body": True},
                                {"type": "http.request", "body": body[128:], "more_body": False},
                            ],
                        )
                    expected = (200 if path == "/mcp" else 503) if baseline else 413
                    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [
                        expected
                    ]
                    assert reads == (2 if baseline or not lengths or lengths[0][1] == b"1" else 0)
            if not remote:
                monkeypatch.setattr(
                    PluginHub,
                    "get_sessions",
                    AsyncMock(
                        return_value=SessionList(
                            sessions={
                                "fixture-session": SessionDetails(
                                    project="fixture",
                                    hash="fixture-hash",
                                    unity_version="6000",
                                    connected_at="now",
                                ),
                            }
                        )
                    ),
                )
                dispatch = AsyncMock(return_value={"success": True, "data": "fixture-result"})
                monkeypatch.setattr(PluginHub, "send_command", dispatch)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    response = await client.post(
                        "/api/command",
                        headers=[(k.decode(), v.decode()) for k, v in headers],
                        json={"type": "read_console"},
                    )
                    assert response.status_code == 200
                    assert response.json() == {"success": True, "data": "fixture-result"}
                dispatch.assert_awaited_once_with("fixture-session", "read_console", {})

    anyio.run(scenario)


def test_public_legacy_sse_preserves_stream_and_limits_message_body(public_app):
    """Hold a real in-process SSE session while posting its HTTP messages."""
    import main

    _, headers, _ = public_app
    baseline = os.environ.get("H_BASELINE") == "1"
    app = main.create_mcp_server(False).http_app(transport="sse")

    async def scenario():
        endpoint_ready = anyio.Event()
        response_ready = anyio.Event()
        disconnect = anyio.Event()
        wire = []
        endpoint = []

        async def receive():
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            wire.append(message)
            body = message.get("body", b"")
            if b"event: endpoint" in body:
                endpoint.append(
                    next(
                        line[6:] for line in body.decode().splitlines() if line.startswith("data: ")
                    )
                )
                endpoint_ready.set()
            if b"event: message" in body and b'"id":1' in body:
                response_ready.set()

        async with app.router.lifespan_context(app):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(app, scope_for(headers, "/sse", "GET"), receive, send)
                with anyio.fail_after(5):
                    await endpoint_ready.wait()
                    target = urlsplit(endpoint[0])
                    message_scope = scope_for(headers, target.path)
                    message_scope["query_string"] = target.query.encode()
                    sent, reads = await invoke(
                        app,
                        message_scope,
                        [
                            {"type": "http.request", "body": b"x" * 128, "more_body": True},
                            {"type": "http.request", "body": b"x" * 129, "more_body": False},
                        ],
                    )
                    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [
                        400 if baseline else 413
                    ]
                    assert reads == 2
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "fixture", "version": "1"},
                        },
                    }
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                    ) as client:
                        response = await client.post(
                            endpoint[0],
                            headers=[(k.decode(), v.decode()) for k, v in headers],
                            json=payload,
                        )
                        assert response.status_code == 202
                    await response_ready.wait()
                disconnect.set()
        # FastMCP's legacy SSE endpoint returns an empty Response after the
        # stream disconnects, yielding two starts on both baseline and fixed apps.
        assert [m["status"] for m in wire if m["type"] == "http.response.start"] == [200, 200]
        assert any(b'"result"' in m.get("body", b"") for m in wire)

    anyio.run(scenario)
