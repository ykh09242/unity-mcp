"""Exercise production ingress limits before selective version inspection."""

import json
import tracemalloc
from unittest.mock import AsyncMock

import anyio
import pytest
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE

from tests.http.test_mcp_protocol_wire import LEGACY_VERSION, MODERN_VERSION, _body
from tests.http.test_request_body_limit import invoke, public_app, scope_for  # pylint: disable=unused-import
from transport.mcp_version_middleware import _mismatched_modern_id
from transport.mcp_version_middleware import McpVersionMiddleware
from transport.models import SessionDetails, SessionList
from transport.plugin_hub import PluginHub


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("root_path", ["", "/mounted"])
@pytest.mark.parametrize("path", ["/mcp", "/custom/mcp"])
async def test_sdk_limit_precedes_metadata_inspection(
    public_app, monkeypatch, declared, root_path, path
):
    app, headers, _ = public_app
    if path != "/mcp":
        app = app.state.fastmcp_server.http_app(path=path, json_response=True, stateless_http=True)
    # The outer local API allowance is deliberately greater than the SDK ceiling.
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "RequestBodyLimitMiddleware":
            middleware.kwargs["max_body_size"] = 64 * 1024 * 1024
    headers = [*headers, (b"mcp-protocol-version", LEGACY_VERSION.encode())]
    size = DEFAULT_MAX_REQUEST_BODY_SIZE + 1
    if declared:
        headers.append((b"content-length", str(size).encode()))

    def inspect(_raw):
        pytest.fail("SDK-oversize input reached metadata inspection")

    monkeypatch.setattr("transport.mcp_version_middleware._mismatched_modern_id", inspect)
    scope = scope_for(headers, root_path + path)
    scope["root_path"] = root_path
    async with app.router.lifespan_context(app):
        sent, reads = await invoke(
            app,
            scope,
            [
                {"type": "http.request", "body": b"", "more_body": True},
                {"type": "http.request", "body": b" " * (size - 1), "more_body": True},
                {"type": "http.request", "body": b" ", "more_body": False},
            ],
        )
    assert sent[0]["status"] == 413
    assert reads == (0 if declared else 3)


@pytest.mark.parametrize("element", [b"{}", b"[]", b'{"unused": []}'])
def test_wide_irrelevant_containers_do_not_expand_into_object_graph(element):
    prefix = json.dumps(_body("server/discover")).encode()[:-1]
    raw = bytearray(prefix + b',"irrelevant":[' + b",".join([element] * 100_000) + b"]}")
    tracemalloc.start()
    try:
        assert _mismatched_modern_id(raw) == "wire-request"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Input decoding and scalar temporaries are allowed; container count must not
    # create a retained Python graph (6-25 MiB for these sub-2 MiB inputs).
    assert peak < len(raw) * 2 + 128 * 1024


@pytest.mark.asyncio
async def test_configured_sdk_ceiling_does_not_reduce_local_api_allowance(public_app, monkeypatch):
    app, headers, remote = public_app
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "RequestBodyLimitMiddleware":
            middleware.kwargs["max_body_size"] = 4096
    route = next(route for route in app.routes if getattr(route, "path", None) == app.state.path)
    endpoint = route.app.endpoint if remote else route.app
    raw = b" " * 1024
    headers = [*headers, (b"mcp-protocol-version", LEGACY_VERSION.encode())]

    def inspect(_raw):
        pytest.fail("Configured SDK-oversize input reached metadata inspection")

    monkeypatch.setattr("transport.mcp_version_middleware._mismatched_modern_id", inspect)
    async with app.router.lifespan_context(app):
        endpoint.session_manager.asgi_app.max_body_size = 512
        sent, _ = await invoke(
            app, scope_for(headers, "/mcp"), [{"type": "http.request", "body": raw}]
        )
        assert sent[0]["status"] == 413
        # This sibling route is outside the MCP endpoint even when it receives
        # the same legacy header and a body larger than the configured SDK cap.
        sent, _ = await invoke(
            app, scope_for(headers, "/api/unknown"), [{"type": "http.request", "body": raw}]
        )
        assert sent[0]["status"] == 404
        if not remote:
            sessions = SessionList(
                sessions={
                    "only": SessionDetails(
                        project="Game", hash="aaaa1111", unity_version="6000", connected_at="now"
                    )
                }
            )
            monkeypatch.setattr(PluginHub, "get_sessions", AsyncMock(return_value=sessions))
            dispatch = AsyncMock(return_value={"success": True})
            monkeypatch.setattr(PluginHub, "send_command", dispatch)
            raw = json.dumps({"type": "manage_scene", "params": {"payload": "x" * 1024}}).encode()
            sent, _ = await invoke(
                app, scope_for(headers, "/api/command"), [{"type": "http.request", "body": raw}]
            )
            assert sent[0]["status"] == 200
            dispatch.assert_awaited_once()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-32"])
@pytest.mark.parametrize("request_id", [0, -17, True, False, 1.5, None, [], {}, "요청😀", "\ud800"])
def test_selected_metadata_preserves_scalar_and_encoding_semantics(encoding, request_id):
    body = {**_body("server/discover"), "id": request_id}
    raw = bytearray(json.dumps(body).encode(encoding))
    expected = (
        request_id
        if isinstance(request_id, (str, int)) and not isinstance(request_id, bool)
        else None
    )
    actual = _mismatched_modern_id(raw)
    assert actual == expected
    assert type(actual) is type(expected)


@pytest.mark.parametrize(
    "suffix, expected",
    [
        ('"id":9', 9),
        ('"id":true', None),
        ('"method":{}', None),
        ('"jsonrpc":"1.0"', None),
        ('"params":null', None),
        ('"params":{"_meta":null}', None),
        (
            '"params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"'
            + MODERN_VERSION
            + '","io.modelcontextprotocol/clientCapabilities":{}}}',
            "wire-request",
        ),
        ('"extra":[{"id":9}, [], NaN, Infinity, -Infinity, 1e999]', "wire-request"),
        ('"extra":[1,]', None),
        ('"extra":{"key":1,}', None),
        ('"extra":{"key" 1}', None),
        ('"extra":[01]', None),
        ('"extra":[true false]', None),
        ('"extra":"\\q"', None),
    ],
)
def test_duplicate_keys_and_irrelevant_syntax_follow_json_semantics(suffix, expected):
    raw = bytearray((json.dumps(_body("server/discover"))[:-1] + "," + suffix + "}").encode())
    assert _mismatched_modern_id(raw) == expected


@pytest.mark.parametrize("depth", [1, 64, 512])
def test_deep_unrelated_containers_remain_legal(depth):
    raw = bytearray(
        json.dumps(_body("server/discover")).encode()[:-1]
        + b',"other":'
        + b"[" * depth
        + b"0"
        + b"]" * depth
        + b"}"
    )
    assert json.loads(raw)["id"] == "wire-request"
    assert _mismatched_modern_id(raw) == "wire-request"


def test_deep_irrelevant_syntax_uses_compact_bookkeeping():
    depth = 100_000
    raw = bytearray(
        json.dumps(_body("server/discover")).encode()[:-1]
        + b',"other":'
        + b"[" * depth
        + b"0"
        + b"]" * depth
        + b"}"
    )
    tracemalloc.start()
    try:
        assert _mismatched_modern_id(raw) == "wire-request"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < len(raw) * 2 + 128 * 1024


@pytest.mark.asyncio
async def test_cancelled_partial_collection_does_not_inspect_or_dispatch(monkeypatch):
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b'{"jsonrpc":', "more_body": True}
        raise anyio.get_cancelled_exc_class()()

    async def downstream(scope, receive, send):
        pytest.fail("Cancelled request was dispatched")

    async def send(message):
        pytest.fail("Cancellation produced a response")

    def inspect(_raw):
        pytest.fail("Cancelled request was inspected")

    monkeypatch.setattr("transport.mcp_version_middleware._mismatched_modern_id", inspect)
    with pytest.raises(anyio.get_cancelled_exc_class()):
        await McpVersionMiddleware(downstream, "/mcp")(
            scope_for([(b"mcp-protocol-version", LEGACY_VERSION.encode())], "/mcp"), receive, send
        )
