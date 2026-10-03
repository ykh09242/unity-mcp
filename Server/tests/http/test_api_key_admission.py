"""Bound unauthenticated public HTTP/WebSocket validation using real HTTPX."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

from services.api_key_service import ApiKeyService
import services.api_key_service as api_module
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE, RemoteControlAuthMiddleware


@pytest_asyncio.fixture
async def admission(monkeypatch):
    actual_client = httpx.AsyncClient
    release = asyncio.Event()
    release.set()
    requests, clients = [], []
    cancelled = []
    verdicts = []
    cancellation_gate = [None]
    now = [0.0]
    monkeypatch.setattr(api_module, "time", SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0]))

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body["api_key"])
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.append(body["api_key"])
            if cancellation_gate[0] is not None:
                await cancellation_gate[0].wait()
            raise
        reply = verdicts.pop(0) if verdicts else httpx.Response(200, json={"valid": True, "user_id": "owned-user"})
        if isinstance(reply, Exception):
            raise reply
        return reply

    def factory(**kwargs):
        client = actual_client(transport=httpx.MockTransport(handler), trust_env=False, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    service = ApiKeyService("https://owned.invalid/validate", cache_ttl=10)
    monkeypatch.setattr(ApiKeyService, "_instance", service)
    service.MAX_INFLIGHT_VALIDATIONS = 2
    service.MAX_VALIDATION_WAITERS = 4
    service.MAX_WAITERS_PER_KEY = 3
    service.MAX_SOURCE_WAITERS = 2
    service.MAX_SOURCE_BUCKETS = 16
    service.SOURCE_BURST = 20.0
    service.SOURCE_REFILL_PER_SECOND = 4.0
    service.SOURCE_IDLE_TTL = 30.0
    service.MAX_KEY_LENGTH = 64

    async def protected(request):
        return JSONResponse({"user_id": request.scope["state"][AUTHENTICATED_USER_STATE]})

    async def websocket(socket):
        await socket.accept()
        await socket.send_json({"user_id": socket.scope["state"][AUTHENTICATED_USER_STATE]})
        await socket.close()

    app = RemoteControlAuthMiddleware(Starlette(routes=[Route("/protected", protected), WebSocketRoute("/protected", websocket)]))

    async def http(key, source="owned-peer"):
        transport = httpx.ASGITransport(app=app, client=(source, 12345))
        async with actual_client(transport=transport, base_url="http://owned.invalid", trust_env=False) as client:
            return await client.get("/protected", headers={"X-API-Key": key, "X-Forwarded-For": "forged-peer"})

    async def ws(key, source="owned-peer"):
        messages = []
        scope = {"type": "websocket", "asgi": {"version": "3.0"}, "path": "/protected", "raw_path": b"/protected",
            "scheme": "ws", "query_string": b"", "headers": [(b"x-api-key", key.encode("ascii"))],
            "client": (source, 12345), "server": ("owned.invalid", 80), "subprotocols": []}

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            messages.append(message)

        await app(scope, receive, send)
        return messages

    harness = SimpleNamespace(service=service, http=http, ws=ws, release=release, requests=requests,
        clients=clients, cancelled=cancelled, verdicts=verdicts, now=now, cancellation_gate=cancellation_gate)
    yield harness
    release.set()
    await settle()
    close = getattr(service, "aclose", None)
    if callable(close):
        await close()
    for client in clients:
        await client.aclose()


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_oversized_key_rejected_before_http_or_websocket_validation(admission):
    response = await admission.http("x" * 65)
    ws = await admission.ws("x" * 65)
    assert response.status_code == 401
    assert ws == [{"type": "websocket.close", "code": 1008, "reason": "API key authentication required"}]
    assert not admission.requests and not admission.clients


@pytest.mark.asyncio
async def test_unique_misses_are_fail_fast_at_global_capacity(admission):
    admission.release.clear()
    tasks = [asyncio.create_task(admission.http("unique-" + str(i), "peer-" + str(i))) for i in range(6)]
    await settle()
    started, rejected = len(admission.requests), sum(task.done() for task in tasks)
    admission.release.set()
    responses = await asyncio.gather(*tasks)
    assert started == 2 and rejected == 4
    assert sorted(response.status_code for response in responses) == [200, 200, 429, 429, 429, 429]
    assert all(response.headers.get("retry-after") == "1" for response in responses if response.status_code == 429)
    assert (await admission.http("recovered", "another-peer")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_same_digest_is_coalesced_and_waiters_are_bounded(admission, valid):
    if not valid:
        admission.verdicts.append(httpx.Response(401))
    admission.release.clear()
    tasks = [asyncio.create_task(admission.http("shared", "peer-" + str(i))) for i in range(8)]
    await settle()
    started, rejected = len(admission.requests), sum(task.done() for task in tasks)
    admission.release.set()
    responses = await asyncio.gather(*tasks)
    assert started == 1 and rejected == 5
    assert sum(response.status_code == (200 if valid else 401) for response in responses) == 3
    assert len(admission.clients) == 1
    assert (await admission.http("shared", "fresh-peer")).status_code == (200 if valid else 401)
    assert len(admission.requests) == 1


@pytest.mark.asyncio
async def test_global_waiters_bounded_across_shared_validations(admission):
    admission.service.MAX_WAITERS_PER_KEY = 10
    admission.release.clear()
    tasks = [asyncio.create_task(admission.http("key-" + str(i % 2), "peer-" + str(i))) for i in range(8)]
    await settle()
    started, rejected = len(admission.requests), sum(task.done() for task in tasks)
    admission.release.set()
    responses = await asyncio.gather(*tasks)
    assert started == 2 and rejected == 4
    assert sum(response.status_code == 200 for response in responses) == 4


@pytest.mark.asyncio
async def test_actual_peer_limits_ignore_forwarded_header_and_allow_other_peer(admission):
    admission.service.MAX_INFLIGHT_VALIDATIONS = 8
    admission.service.MAX_VALIDATION_WAITERS = 8
    admission.release.clear()
    tasks = [asyncio.create_task(admission.http("source-" + str(i))) for i in range(5)]
    tasks.append(asyncio.create_task(admission.http("other", "other-peer")))
    await settle()
    started, rejected = len(admission.requests), sum(task.done() for task in tasks)
    admission.release.set()
    responses = await asyncio.gather(*tasks)
    assert started == 3 and rejected == 3
    assert responses[-1].status_code == 200


@pytest.mark.asyncio
async def test_source_rate_and_source_map_are_bounded_and_recover(admission):
    admission.service.SOURCE_BURST = 2.0
    admission.service.SOURCE_REFILL_PER_SECOND = 1.0
    responses = [await admission.http("rate-" + str(i)) for i in range(4)]
    assert [response.status_code for response in responses] == [200, 200, 429, 429]
    admission.now[0] = 1.0
    assert (await admission.http("rate-recovery")).status_code == 200
    # Cached credentials do not compete with random-key miss admission.
    assert (await admission.http("rate-0")).status_code == 200
    admission.service.MAX_SOURCE_BUCKETS = 2
    assert (await admission.http("table-one", "new-peer")).status_code == 200
    assert (await admission.http("table-reject", "third-peer")).status_code == 429
    admission.now[0] = 40.0
    assert (await admission.http("table-recovery", "third-peer")).status_code == 200


@pytest.mark.asyncio
async def test_public_websocket_admission_and_valid_identity(admission):
    admission.service.SOURCE_BURST = 1.0
    first = await admission.ws("websocket-one")
    second = await admission.ws("websocket-two")
    assert first[0]["type"] == "websocket.accept"
    assert json.loads(first[1]["text"]) == {"user_id": "owned-user"}
    assert second[0]["type"] == "websocket.close" and second[0]["code"] == 1013
    assert len(admission.requests) == 1


@pytest.mark.asyncio
async def test_one_waiter_cancel_keeps_shared_request_last_waiter_cancels(admission):
    admission.release.clear()
    first = asyncio.create_task(admission.http("cancel-shared", "first-peer"))
    second = asyncio.create_task(admission.http("cancel-shared", "second-peer"))
    await settle()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    assert not admission.cancelled
    admission.release.set()
    assert (await second).status_code == 200
    assert admission.requests == ["cancel-shared"]
    admission.release.clear()
    last = asyncio.create_task(admission.http("cancel-last", "third-peer"))
    await settle()
    last.cancel()
    await asyncio.gather(last, return_exceptions=True)
    await settle()
    assert admission.cancelled == ["cancel-last"]
    assert not admission.service._inflight
    admission.release.set()
    assert (await admission.http("after-cancel", "fourth-peer")).status_code == 200


@pytest.mark.asyncio
async def test_pooled_client_retries_transients_cache_and_shutdown(admission):
    admission.verdicts.extend([httpx.ReadTimeout("owned timeout"), httpx.Response(200, json={"valid": True, "user_id": "owned-user"})])
    assert (await admission.http("retry")).status_code == 200
    assert admission.requests == ["retry", "retry"] and len(admission.clients) == 1
    assert (await admission.http("retry")).status_code == 200
    admission.verdicts.append(httpx.Response(503))
    assert (await admission.http("transient")).status_code == 401
    assert (await admission.http("transient")).status_code == 200
    admission.verdicts.append(httpx.Response(401))
    assert (await admission.http("negative")).status_code == 401
    before = len(admission.requests)
    assert (await admission.http("negative")).status_code == 401
    assert len(admission.requests) == before
    admission.now[0] = 11.0
    assert (await admission.http("retry")).status_code == 200
    assert len(admission.clients) == 1
    await admission.service.aclose()
    assert admission.clients[0].is_closed
    assert (await admission.http("closed")).status_code == 401


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_validation_and_releases_capacity(admission):
    admission.release.clear()
    task = asyncio.create_task(admission.http("shutdown", "peer"))
    await settle()
    await admission.service.aclose()
    await asyncio.gather(task, return_exceptions=True)
    assert admission.cancelled == ["shutdown"]
    assert admission.clients[0].is_closed
    assert not admission.service._inflight


@pytest.mark.asyncio
async def test_level_cancellation_cannot_strand_waiters_or_inflight(admission):
    import anyio
    admission.release.clear()
    async with anyio.create_task_group() as group:
        group.start_soon(admission.http, "level-cancel", "peer")
        await settle()
        group.cancel_scope.cancel()
    await settle()
    assert not admission.service._inflight
    assert admission.service._validation_waiters == 0
    assert all(source.waiters == 0 for source in admission.service._sources.values())
    admission.release.set()
    assert (await admission.http("level-recovery", "other-peer")).status_code == 200


@pytest.mark.asyncio
async def test_last_waiter_keeps_slot_until_real_cancel_cleanup_even_repeated_cancel(admission):
    admission.service.MAX_INFLIGHT_VALIDATIONS = 1
    admission.release.clear()
    gate = asyncio.Event()
    admission.cancellation_gate[0] = gate
    task = asyncio.create_task(admission.http("slow-cancel", "peer"))
    await settle()
    task.cancel()
    await settle()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(admission.service._inflight) == 1
    assert (await admission.http("while-cancelling", "other-peer")).status_code == 429
    gate.set()
    await settle()
    assert not admission.service._inflight
    admission.release.set()
    assert (await admission.http("cancel-recovery", "other-peer")).status_code == 200


@pytest.mark.asyncio
async def test_shutdown_racing_admitted_request_cannot_create_an_unowned_client(admission):
    admission.release.clear()
    request = asyncio.create_task(admission.http("admitted-before-close", "peer"))
    shutdown = asyncio.create_task(admission.service.aclose())
    await asyncio.gather(request, shutdown, return_exceptions=True)
    assert all(client.is_closed for client in admission.clients)
    assert admission.service._client is None
    assert not admission.service._inflight
