"""Prove the version adapter preserves ASGI input and outer ingress protections."""

import gc
import json
import weakref

import pytest
from starlette.responses import Response

from tests.http.test_mcp_protocol_wire import LEGACY_VERSION, MODERN_VERSION, _body
from tests.http.test_rejected_request_body_lifetime import TrackedMessage
from tests.http.test_request_body_limit import (
    invoke,
    public_app,  # pylint: disable=unused-import
    scope_for,
)
from transport.mcp_version_middleware import McpVersionMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", ["wire-request", 0, 7])
@pytest.mark.parametrize("root_path", ["", "/mounted"])
async def test_mismatch_retains_request_id_at_configured_endpoint(request_id, root_path):
    # Given: an explicit custom MCP endpoint, possibly mounted below an ASGI root.
    request = {**_body("server/discover"), "id": request_id}
    raw = json.dumps(request).encode()
    scope = scope_for(
        [(b"mcp-protocol-version", LEGACY_VERSION.encode())], f"{root_path}/custom/mcp"
    )
    scope["root_path"] = root_path

    async def downstream(scope, receive, send):
        pytest.fail("A mismatched modern request reached SDK legacy dispatch")

    # When: a chunked body reaches the adapter through its configured route.
    sent, reads = await invoke(
        McpVersionMiddleware(downstream, path="/custom/mcp"),
        scope,
        [
            {"type": "http.request", "body": raw[:37], "more_body": True},
            {"type": "http.request", "body": raw[37:], "more_body": False},
        ],
    )

    # Then: the normative error preserves the exact string/integer ID, including zero.
    assert reads == 2
    assert sent[0]["status"] == 400
    error = json.loads(sent[1]["body"])
    assert error["jsonrpc"] == "2.0"
    assert error["id"] == request_id
    assert type(error["id"]) is type(request_id)
    assert error["error"]["code"] == -32020
    assert "result" not in error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"\xff",
        b"[1,2,3]",
        json.dumps(_body("initialize", version=LEGACY_VERSION)).encode(),
        json.dumps({**_body("server/discover"), "jsonrpc": "1.0"}).encode(),
        json.dumps({**_body("server/discover"), "id": None}).encode(),
        json.dumps({**_body("server/discover"), "id": True}).encode(),
        json.dumps({**_body("server/discover"), "params": {}}).encode(),
        b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
    ],
)
async def test_sdk_owned_envelopes_replay_body_bytes_and_continuation(raw):
    # Given: malformed, non-request or explicit legacy input that the SDK must own.
    scope = scope_for([(b"mcp-protocol-version", LEGACY_VERSION.encode())], "/mcp")
    chunks = [
        {"type": "http.request", "body": raw[:3], "more_body": True},
        {"type": "http.request", "body": raw[3:], "more_body": False},
    ]
    received = []

    async def downstream(actual_scope, receive, send):
        assert actual_scope is scope
        received.extend([await receive(), await receive()])
        await Response(status_code=204)(actual_scope, receive, send)

    # When: the adapter inspects then delegates this input.
    sent, reads = await invoke(McpVersionMiddleware(downstream, "/mcp"), scope, chunks)

    # Then: coalescing preserves bytes/completion and the original continuation.
    assert received[0] == {"type": "http.request", "body": raw, "more_body": False}
    assert received[1] == {"type": "http.disconnect"}
    assert reads == 3
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_disconnected_partial_body_is_replayed_without_parsing(monkeypatch):
    # Given: an incomplete body followed by the client's disconnect.
    scope = scope_for([(b"mcp-protocol-version", LEGACY_VERSION.encode())], "/mcp")
    chunks = [
        {"type": "http.request", "body": b'{"jsonrpc":', "more_body": True},
        {"type": "http.disconnect"},
    ]
    received = []

    def unexpected_parse(messages):
        pytest.fail("A disconnected partial body was parsed")

    monkeypatch.setattr("transport.mcp_version_middleware._mismatched_modern_id", unexpected_parse)

    async def downstream(scope, receive, send):
        received.extend([await receive(), await receive()])

    # When: the adapter observes the disconnect before a complete request.
    _, reads = await invoke(McpVersionMiddleware(downstream, "/mcp"), scope, chunks)

    # Then: it preserves partial bytes/completion and the original disconnect signal.
    assert reads == 2
    assert received[0] == chunks[0]
    assert received[1] is chunks[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope_type", "method", "path", "versions"),
    [
        ("websocket", "POST", "/mcp", [LEGACY_VERSION]),
        ("http", "GET", "/mcp", [LEGACY_VERSION]),
        ("http", "POST", "/api/command", [LEGACY_VERSION]),
        ("http", "POST", "/mcp", []),
        ("http", "POST", "/mcp", [MODERN_VERSION]),
        ("http", "POST", "/mcp", ["2099-01-01"]),
        ("http", "POST", "/mcp", [LEGACY_VERSION, LEGACY_VERSION]),
    ],
)
async def test_other_transport_and_header_routes_do_not_read_body(
    scope_type, method, path, versions
):
    # Given: traffic outside the one verified legacy-header routing gap.
    scope = scope_for(
        [(b"mcp-protocol-version", value.encode()) for value in versions],
        path,
        method,
        scope_type,
    )
    reached = []

    async def downstream(actual_scope, receive, send):
        reached.append(actual_scope)

    # When: the adapter handles the request.
    _, reads = await invoke(McpVersionMiddleware(downstream, "/mcp"), scope)

    # Then: it delegates the untouched scope without inspecting or consuming input.
    assert reached == [scope]
    assert reached[0] is scope
    assert reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["authentication", "declared", "streamed"])
async def test_production_auth_and_byte_limits_precede_version_parser(
    public_app, monkeypatch, rejection
):
    # Given: the production app with its real auth layer and a small ingress ceiling.
    app, headers, _ = public_app
    headers = [*headers, (b"mcp-protocol-version", LEGACY_VERSION.encode())]
    if rejection == "authentication":
        headers = [pair for pair in headers if pair[0] not in {b"x-api-key", b"x-unity-mcp-token"}]
        headers.append((b"content-length", b"999"))
    elif rejection == "declared":
        headers.append((b"content-length", b"257"))
    else:
        # A lying declaration cannot bypass the count of actual streamed bytes.
        headers.append((b"content-length", b"1"))

    def unexpected_parse(messages):
        pytest.fail("Rejected input reached the version body parser")

    monkeypatch.setattr("transport.mcp_version_middleware._mismatched_modern_id", unexpected_parse)

    # When: the app receives unauthenticated, declared-oversize or streamed-oversize input.
    async with app.router.lifespan_context(app):
        sent, reads = await invoke(
            app,
            scope_for(headers, "/mcp"),
            [
                {"type": "http.request", "body": b"x" * 128, "more_body": True},
                {"type": "http.request", "body": b"x" * 129, "more_body": False},
            ],
        )

    # Then: auth wins over length errors, and both size protections precede parsing.
    assert sent[0]["status"] == (401 if rejection == "authentication" else 413)
    assert reads == (2 if rejection == "streamed" else 0)


@pytest.mark.asyncio
async def test_rejected_mismatch_releases_original_body_before_error_send():
    # Given: a tracked message generated only on receive, without an external owner.
    references = []
    scope = scope_for([(b"mcp-protocol-version", LEGACY_VERSION.encode())], "/mcp")

    async def receive():
        message = TrackedMessage(
            type="http.request", body=json.dumps(_body("server/discover")).encode()
        )
        references.append(weakref.ref(message))
        return message

    async def downstream(scope, receive, send):
        pytest.fail("Rejected mismatch was delegated")

    async def send(message):
        if message["type"] == "http.response.start":
            # Inspect at the point a slow network write could otherwise retain input.
            gc.collect()
            assert message["status"] == 400
            assert references[0]() is None

    # When: the adapter rejects the mismatch and begins delivering its error.
    await McpVersionMiddleware(downstream, "/mcp")(scope, receive, send)

    # Then: no original raw body owner survives response delivery.
    assert len(references) == 1
    assert references[0]() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fragment", [b"", b"x"])
async def test_many_fragments_release_message_objects_during_collection(fragment):
    # Given: thousands of valid empty or tiny ASGI fragments, generated on demand.
    count = 4096
    references = []
    scope = scope_for([(b"mcp-protocol-version", LEGACY_VERSION.encode())], "/mcp")
    continuation = {"type": "http.disconnect", "fixture": "upstream-continuation"}

    async def receive():
        if references:
            assert references[-1]() is None, "Adapter retained a previous input message"
        if len(references) == count:
            return continuation
        message = TrackedMessage(
            type="http.request", body=fragment, more_body=len(references) < count - 1
        )
        references.append(weakref.ref(message))
        return message

    async def downstream(actual_scope, replay_receive, send):
        assert len(references) == count
        assert all(reference() is None for reference in references)
        replayed = await replay_receive()
        assert replayed == {"type": "http.request", "body": fragment * count, "more_body": False}
        assert await replay_receive() is continuation
        await Response(status_code=204)(actual_scope, replay_receive, send)

    sent = []

    async def send(message):
        sent.append(message)

    # When: the adapter receives arbitrarily fragmented input below the byte limit.
    await McpVersionMiddleware(downstream, "/mcp")(scope, receive, send)

    # Then: retained body storage depends on bytes, never on the number of messages.
    assert sent[0]["status"] == 204
    assert len(references) == count
    assert all(reference() is None for reference in references)


@pytest.mark.asyncio
@pytest.mark.parametrize("fragments", [[], [b"", b"abc", b"", b"def"]])
@pytest.mark.parametrize("signal_type", ["http.disconnect", "http.fixture_extension"])
async def test_interrupted_fragments_preserve_signal_and_receive_continuation(
    monkeypatch, fragments, signal_type
):
    # Given: zero or several partial body messages followed by a non-body signal.
    scope = scope_for([(b"mcp-protocol-version", LEGACY_VERSION.encode())], "/mcp")
    signal = {"type": signal_type, "fixture": "original-signal"}
    continuation = {"type": "http.disconnect", "fixture": "original-continuation"}
    incoming = iter(
        [
            *(
                {"type": "http.request", "body": fragment, "more_body": True}
                for fragment in fragments
            ),
            signal,
            continuation,
        ]
    )

    async def receive():
        return next(incoming)

    def unexpected_parse(raw):
        pytest.fail("A body interrupted by a non-body signal was parsed")

    monkeypatch.setattr("transport.mcp_version_middleware._mismatched_modern_id", unexpected_parse)

    async def downstream(actual_scope, replay_receive, send):
        if fragments:
            assert await replay_receive() == {
                "type": "http.request",
                "body": b"".join(fragments),
                "more_body": True,
            }
        assert await replay_receive() is signal
        assert await replay_receive() is continuation

    async def send(message):
        pytest.fail("Interrupted input unexpectedly produced a response")

    # When: collection stops at the first non-body event.
    await McpVersionMiddleware(downstream, "/mcp")(scope, receive, send)

    # Then: no synthetic complete body or discarded/duplicated signal remains.
    assert next(incoming, None) is None


@pytest.mark.asyncio
async def test_accepted_body_buffer_is_released_at_downstream_handoff(monkeypatch):
    # Given: a tracked accumulator and an SDK-owned body that must be replayed.
    references = []
    raw = b"x" * 4096
    scope = scope_for([(b"mcp-protocol-version", LEGACY_VERSION.encode())], "/mcp")

    class TrackedBuffer(bytearray):
        def __init__(self):
            super().__init__()
            references.append(weakref.ref(self))

    monkeypatch.setattr("transport.mcp_version_middleware.bytearray", TrackedBuffer, raising=False)

    async def downstream(actual_scope, receive, send):
        replayed = await receive()
        assert replayed["body"] == raw
        assert len(references) == 1
        assert len(references[0]()) == 0, "Adapter retained a duplicate body after replay handoff"

    # When: the downstream SDK receives the reconstructed complete body.
    await invoke(
        McpVersionMiddleware(downstream, "/mcp"),
        scope,
        [{"type": "http.request", "body": raw, "more_body": False}],
    )

    # Then: the accumulator itself is also released when the request exits.
    assert references[0]() is None
