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
