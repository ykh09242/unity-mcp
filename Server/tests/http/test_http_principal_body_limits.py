"""Authenticated hosted callers share HTTP body capacity across credentials."""

import asyncio

import pytest
from starlette.requests import ClientDisconnect, Request

from tests.http.test_request_body_limit import invoke, scope_for
from tests.remote_credentials import remote_credentials
from tests.timing import await_signal
from transport.remote_auth_middleware import RemoteControlAuthMiddleware
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE
from transport.request_body_limit_middleware import RequestBodyLimitMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("pressure", ["bytes", "requests"])
async def test_authenticated_body_share_survives_eof_and_releases_on_cancel(monkeypatch, pressure):
    monkeypatch.setenv(
        "UNITY_MCP_HTTP_PRINCIPAL_RETAINED_BYTES", "4" if pressure == "bytes" else "16"
    )
    monkeypatch.setenv(
        "UNITY_MCP_HTTP_PRINCIPAL_RETAINED_REQUESTS", "1" if pressure == "requests" else "4"
    )
    entered = asyncio.Event()

    async def parser(scope, receive, send):
        await Request(scope, receive).body()
        if scope["path"] == "/held":
            entered.set()
            await asyncio.Event().wait()
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b""})

    ingress = RequestBodyLimitMiddleware(parser, max_body_size=16, max_ingress_bytes=6, hosted=True)
    app = RemoteControlAuthMiddleware(ingress)

    async def post(key, body, path="/"):
        sent, _ = await invoke(
            app,
            scope_for([(b"x-api-key", key.encode())], path),
            [
                {"type": "http.request", "body": body},
            ],
        )
        return sent[0]["status"]

    async with remote_credentials({"alice1": "alice", "alice2": "alice", "bob1": "bob"}):
        held = asyncio.create_task(post("alice1", b"four", "/held"))
        try:
            await await_signal(entered, "first retained hosted body", producers=(held,))
            assert ingress.active_readers == 0 and ingress.retained_bytes == 4
            assert await post("alice2", b"x") == 503
            assert await post("bob1", b"ok") == 200
            assert await post("bob1", b"big") == 503
        finally:
            held.cancel()
            await asyncio.gather(held, return_exceptions=True)
        assert ingress.retained_bytes == 0
        assert await post("alice2", b"four") == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["disconnect", "slow_rejection"])
async def test_partial_principal_upload_retains_and_releases_its_owner(monkeypatch, end):
    monkeypatch.setenv("UNITY_MCP_HTTP_PRINCIPAL_RETAINED_BYTES", "3")
    monkeypatch.setenv("UNITY_MCP_HTTP_PRINCIPAL_RETAINED_REQUESTS", "1")
    blocked, resume = asyncio.Event(), asyncio.Event()

    async def parser(scope, receive, send):
        await Request(scope, receive).body()
        await send({"type": "http.response.start", "status": 200})

    app = RequestBodyLimitMiddleware(parser, hosted=True)

    def scope(user):
        result = scope_for()
        result["state"][AUTHENTICATED_USER_STATE] = user
        return result

    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"123", "more_body": True}
        if end == "disconnect":
            blocked.set()
            await resume.wait()
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": b"4"}

    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 503
            blocked.set()
            await resume.wait()

    held = asyncio.create_task(app(scope("alice"), receive, send))
    chunks = [{"type": "http.request", "body": b"ok"}]
    try:
        await await_signal(blocked, "partial upload owns bytes", producers=(held,))
        assert app.retained_bytes == 3
        assert (await invoke(app, scope("alice"), chunks))[0][0]["status"] == 503
        assert (await invoke(app, scope("bob"), chunks))[0][0]["status"] == 200
    finally:
        resume.set()
        if end == "disconnect":
            with pytest.raises(ClientDisconnect):
                await held
        else:
            await held
    assert app.active_readers == app.retained_bytes == 0
    assert not app._principals
    assert (await invoke(app, scope("alice"), chunks))[0][0]["status"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("hosted", [False, True])
async def test_local_and_anonymous_routes_use_global_body_limits(monkeypatch, hosted):
    monkeypatch.setenv("UNITY_MCP_HTTP_PRINCIPAL_RETAINED_BYTES", "1")

    async def route(scope, receive, send):
        assert await Request(scope, receive).body() == b"local"
        await send({"type": "http.response.start", "status": 200})

    app = RequestBodyLimitMiddleware(route, max_ingress_bytes=5, hosted=hosted)
    scope = scope_for()
    if not hosted:
        scope["state"][AUTHENTICATED_USER_STATE] = "unused-local-state"
    sent, _ = await invoke(app, scope, [{"type": "http.request", "body": b"local"}])
    assert sent[0]["status"] == 200 and app.retained_bytes == 0 and not app._principals
