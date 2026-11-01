"""External authentication is bounded at the actual HTTPX byte-stream boundary."""

import asyncio
import gzip
import json
import zlib
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

from services.api_key_service import ApiKeyService

RESPONSE_LIMIT = 64 * 1024
METADATA_LIMIT = 16 * 1024
KEY = "fixture-client-key-private"


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, failure=None, block=False):
        self.chunks = chunks
        self.failure = failure
        self.block = block
        self.started = asyncio.Event()
        self.closed = False
        self.yielded = 0

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk
        if self.failure:
            raise self.failure
        if self.block:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest_asyncio.fixture
async def auth_service(monkeypatch):
    monkeypatch.setattr(ApiKeyService, "_instance", None)
    service = ApiKeyService("https://fixture.invalid/validate")
    requests = []
    responses = []

    def handle(request):
        requests.append(request)
        return responses.pop(0)

    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    yield service, requests, responses
    await service.aclose()


def response(body, *, headers=None, status=200):
    stream = TrackedStream(body if isinstance(body, list) else [body])
    return httpx.Response(status, headers=headers, stream=stream), stream


@pytest.mark.asyncio
async def test_exact_response_limit_and_cache_hit(auth_service):
    service, requests, responses = auth_service
    body = b'{"valid":true,"user_id":"fixture-user"}'
    body += b" " * (RESPONSE_LIMIT - len(body))
    reply, stream = response(body)
    responses.append(reply)
    assert (await service.validate(KEY)).valid
    assert (await service.validate(KEY)).valid
    assert len(requests) == 1 and stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}])
async def test_wire_overflow_closes_early_without_cache(auth_service, headers):
    service, requests, responses = auth_service
    for _ in range(2):
        reply, stream = response([b" " * RESPONSE_LIMIT, b"x", b"unread"], headers=headers)
        responses.append(reply)
        result = await service.validate(KEY)
        assert not result.valid and not result.cacheable
        assert stream.closed and stream.yielded == 2
    assert len(requests) == 2 and not service._cache


@pytest.mark.asyncio
@pytest.mark.parametrize("unicode_text", [False, True])
@pytest.mark.parametrize("overflow", [False, True])
async def test_metadata_utf8_byte_limit(auth_service, unicode_text, overflow):
    service, _, responses = auth_service
    # {"x":""} accounts for eight canonical UTF-8 bytes.
    count = METADATA_LIMIT - 8
    value = "a" * count
    if unicode_text:
        value = "한" * (count // 3) + "a" * (count % 3)
    metadata = {"x": value + ("a" if overflow else "")}
    body = json.dumps(
        {"valid": True, "user_id": "fixture-user", "metadata": metadata}, ensure_ascii=False
    ).encode()
    reply, stream = response(body)
    responses.append(reply)
    result = await service.validate(KEY)
    assert result.valid is (not overflow)
    assert result.cacheable is (not overflow)
    assert stream.closed
    if overflow:
        assert not service._cache
    else:
        assert result.metadata == metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate", "raw-deflate"])
@pytest.mark.parametrize("overflow", [False, True])
async def test_bounded_compression_compatibility(auth_service, encoding, overflow):
    service, requests, responses = auth_service
    body = b'{"valid":true,"user_id":"fixture-user"}'
    body += b" " * (RESPONSE_LIMIT - len(body) + int(overflow))
    encoded = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)
    if encoding == "raw-deflate":
        encoded = encoded[2:-4]
    reply, stream = response(
        encoded, headers={"Content-Encoding": "gzip" if encoding == "gzip" else "deflate"}
    )
    responses.append(reply)
    result = await service.validate(KEY)
    assert result.valid is (not overflow)
    assert result.cacheable is (not overflow)
    assert stream.closed
    assert requests[0].headers["Accept-Encoding"] == "gzip, deflate, identity"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"null",
        b"{",
        b'{"valid":1}',
        b'{"valid":true,"metadata":[]}',
        b'{"valid":true,"metadata":"secret"}',
        b'{"valid":true,"user_id":[]}',
        b'{"valid":false,"error":{}}',
        b'{"valid":true,"metadata":{"x":NaN}}',
        b'{"valid":true,"metadata":{"x":"\\ud800"}}',
        b'{"valid":true,"metadata":{"x":' + b"[" * 40 + b"0" + b"]" * 40 + b"}}",
        b'{"valid":true,"metadata":{"x":[' + b"0," * 5000 + b"0]}}",
    ],
)
async def test_malformed_response_is_redacted_and_noncacheable(auth_service, body):
    service, _, responses = auth_service
    reply, stream = response(body)
    responses.append(reply)
    with patch("services.api_key_service.logger") as logger:
        result = await service.validate(KEY)
    assert not result.valid and not result.cacheable
    assert KEY not in result.error and "secret" not in result.error
    assert KEY not in str(logger.mock_calls) and "secret" not in str(logger.mock_calls)
    assert stream.closed and not service._cache


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["br", "gzip, deflate", "gzip", "deflate"])
async def test_invalid_encoding_is_noncacheable(auth_service, encoding):
    service, _, responses = auth_service
    reply, stream = response(
        b"invalid compressed private payload", headers={"Content-Encoding": encoding}
    )
    responses.append(reply)
    result = await service.validate(KEY)
    assert not result.valid and not result.cacheable and stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("damage", ["truncate", "trailing", "second-member"])
async def test_incomplete_or_trailing_compression_is_rejected(auth_service, encoding, damage):
    service, _, responses = auth_service
    body = b'{"valid":true,"user_id":"fixture-user"}'
    encoded = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)
    match damage:
        case "truncate":
            encoded = encoded[:-1]
        case "trailing":
            encoded += b"private trailing data"
        case "second-member":
            encoded += encoded
    reply, stream = response(encoded, headers={"Content-Encoding": encoding})
    responses.append(reply)
    result = await service.validate(KEY)
    assert not result.valid and not result.cacheable and stream.closed
    assert not service._cache


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 503])
async def test_status_rejection_closes_without_consuming_body(auth_service, status):
    service, _, responses = auth_service
    reply, stream = response(b" " * (RESPONSE_LIMIT + 1), status=status)
    responses.append(reply)
    result = await service.validate(KEY)
    assert not result.valid and result.cacheable is (status == 401)
    assert stream.closed and stream.yielded == 0


@pytest.mark.asyncio
async def test_read_timeout_closes_attempt_before_retry(auth_service):
    service, requests, responses = auth_service
    failed = TrackedStream([b"{"], failure=httpx.ReadTimeout("private provider error"))
    reply, success = response(b'{"valid":true,"user_id":"fixture-user"}')
    responses.extend([httpx.Response(200, stream=failed), reply])
    assert (await service.validate(KEY)).valid
    assert len(requests) == 2 and failed.closed and success.closed


@pytest.mark.asyncio
async def test_cancellation_closes_response_and_releases_work(auth_service):
    service, _, responses = auth_service
    stream = TrackedStream([b"{"], block=True)
    responses.append(httpx.Response(200, stream=stream))
    task = asyncio.create_task(service.validate(KEY))
    await stream.started.wait()
    await service.aclose()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed and not service._inflight and not service._cache


@pytest.mark.asyncio
async def test_progressing_loopback_response_has_one_shared_total_deadline(auth_service):
    service, _, _ = auth_service
    service.REVALIDATION_TIMEOUT = 0.15
    service.MAX_INFLIGHT_VALIDATIONS = 1
    started, disconnected = asyncio.Event(), asyncio.Event()
    requests, handlers = [], set()
    chunks = []

    async def provider(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        disconnect = None
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            key = json.loads(await reader.readexactly(length))["api_key"]
            requests.append(key)
            body = b'{"valid":true,"user_id":"fixture-user"}'
            if key != KEY:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
                return
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            await writer.drain()
            started.set()
            disconnect = asyncio.create_task(reader.read())
            # Each chunk arrives within HTTPX's read timeout, but the body as a
            # whole takes far longer than the shared authentication deadline.
            for _ in range(100):
                done, _ = await asyncio.wait({disconnect}, timeout=0.01)
                if done:
                    disconnected.set()
                    return
                writer.write(b"1\r\n \r\n")
                await writer.drain()
                chunks.append(1)
            writer.write(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            disconnected.set()
        finally:
            if disconnect is not None:
                disconnect.cancel()
                await asyncio.gather(disconnect, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(provider, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    await service._client.aclose()
    service._client = httpx.AsyncClient(timeout=0.08, trust_env=False)
    # Only this hermetic test replaces the production HTTPS endpoint after
    # construction, so the real HTTPX socket transport can exercise slow reads.
    service._validation_url = f"http://127.0.0.1:{port}/validate"
    waiters = [asyncio.create_task(service.validate(KEY))]
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        waiters.append(asyncio.create_task(service.validate(KEY)))
        done, pending = await asyncio.wait(waiters, timeout=0.45)
        assert not pending, "progressing auth bodies must not hold cold validation indefinitely"
        assert len(done) == 2 and requests == [KEY] and len(chunks) > 1
        for task in waiters:
            result = task.result()
            assert not result.valid and not result.cacheable
            assert result.error == "Auth service timeout"
        await asyncio.wait_for(disconnected.wait(), timeout=0.5)
        assert not service._cache and not service._inflight
        assert (await service.validate("unrelated-key")).valid
        # A deadline failure is transient, so the original key can retry too.
        assert (await service.validate(KEY + "-retry")).valid
    finally:
        for task in waiters:
            task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        await service.aclose()
        server.close()
        await server.wait_closed()
        for task in tuple(handlers):
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


@pytest.mark.asyncio
async def test_total_deadline_includes_retry_backoff(auth_service):
    service, requests, responses = auth_service
    service.REVALIDATION_TIMEOUT = 0.03
    failed = TrackedStream([b"{"], failure=httpx.ReadTimeout("fixture timeout"))
    reply, success = response(b'{"valid":true,"user_id":"fixture-user"}')
    responses.extend([httpx.Response(200, stream=failed), reply])
    result = await service.validate(KEY)
    assert not result.valid and not result.cacheable
    assert result.error == "Auth service timeout"
    assert len(requests) == 1 and failed.closed and not success.closed
    await asyncio.gather(
        *(flight.task for flight in service._inflight.values()), return_exceptions=True
    )
    assert not service._cache and not service._inflight
    assert (await service.validate(KEY)).valid
    assert len(requests) == 2 and success.closed


@pytest.mark.asyncio
async def test_deadline_holds_slot_and_fences_late_positive_until_worker_exits(auth_service):
    service, _, responses = auth_service
    service.REVALIDATION_TIMEOUT = 0.03
    service.MAX_INFLIGHT_VALIDATIONS = 1
    release = asyncio.Event()
    cancelled = asyncio.Event()

    class DelayedCancellationStream(TrackedStream):
        async def __aiter__(self):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        pass
            yield b'{"valid":true,"user_id":"fixture-user"}'

    stream = DelayedCancellationStream([])
    responses.append(httpx.Response(200, stream=stream))
    waiter = asyncio.create_task(service.validate(KEY))
    try:
        done, _ = await asyncio.wait({waiter}, timeout=0.3)
        assert done, "deadline rejection must not wait for provider cancellation cleanup"
        assert not waiter.result().valid and not waiter.result().cacheable
        await cancelled.wait()
        assert len(service._inflight) == 1 and not stream.closed
        assert (await service.validate("unrelated-key")).overloaded
        release.set()
        await asyncio.gather(*(flight.task for flight in service._inflight.values()))
        assert stream.closed and not service._inflight
        assert not service._cache and not service._lease_records
        reply, _ = response(b'{"valid":true,"user_id":"fixture-user"}')
        responses.append(reply)
        assert (await service.validate("unrelated-key")).valid
    finally:
        release.set()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
