import asyncio
import importlib
import io
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

docs = importlib.import_module("services.tools.unity_docs")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inputs",
    [
        {"queries": ",".join(f"Type{i}" for i in range(9))},
        {"query": "x" * 257},
        {"queries": "x" * 2049},
    ],
    ids=["query-count", "query-length", "total-input"],
)
async def test_excess_input_is_rejected_before_fetch(monkeypatch, inputs):
    fetch = AsyncMock()
    monkeypatch.setattr(docs, "_fetch_url", fetch)
    result = await docs.unity_docs(SimpleNamespace(), action="lookup", **inputs)
    assert not result["success"]
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_queries_are_only_looked_up_once(monkeypatch):
    lookup = AsyncMock(return_value={"hits": []})
    monkeypatch.setattr(docs, "_lookup_single", lookup)
    result = await docs.unity_docs(None, action="lookup", queries="Physics, Physics,Physics")
    assert result["success"]
    assert lookup.await_count == 1


class Response(io.BytesIO):
    status = 200
    url = "https://docs.unity3d.com/Manual/test.html"

    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [False, True])
async def test_oversized_responses_are_bounded_and_closed(monkeypatch, declared):
    monkeypatch.setattr(docs, "MAX_RESPONSE_BYTES", 64)
    response = Response(b"x" * 100, {"Content-Length": "100"} if declared else {})
    monkeypatch.setattr(docs, "urlopen", lambda *a, **k: response)
    with pytest.raises(ConnectionError, match="size limit"):
        await docs._fetch_url_full(response.url)
    assert response.closed


@pytest.mark.asyncio
async def test_cancelled_fetch_holds_capacity_until_worker_exits(monkeypatch):
    started, release = threading.Event(), threading.Event()
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(docs, "_fetch_slots", slots)

    def blocking_open(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return Response()

    monkeypatch.setattr(docs, "urlopen", blocking_open)
    task = asyncio.create_task(docs._fetch_url_full(Response.url))
    try:
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ConnectionError, match="capacity"):
            await docs._fetch_url_full(Response.url)
    finally:
        release.set()
        for _ in range(1000):
            if slots.acquire(blocking=False):
                slots.release()
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("Fetch slot was not released")


@pytest.mark.asyncio
async def test_request_concurrency_and_deadline(monkeypatch):
    monkeypatch.setattr(docs, "_request_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(docs, "REQUEST_TIMEOUT_SECONDS", 0.03)
    started = asyncio.Event()

    async def slow(*args):
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(docs, "_get_doc", slow)
    first = asyncio.create_task(docs.unity_docs(None, action="get_doc", class_name="Physics"))
    await started.wait()
    busy = await docs.unity_docs(None, action="get_doc", class_name="Physics")
    assert not busy["success"] and "busy" in busy["message"]
    assert not (await first)["success"]


@pytest.mark.asyncio
async def test_lookup_limits_active_fetches_per_request(monkeypatch):
    active = maximum = 0
    lock = threading.Lock()

    def opened(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return Response()

    monkeypatch.setattr(docs, "urlopen", opened)
    result = await docs.unity_docs(None, action="lookup", queries="Physics,Camera,Transform")
    assert result["success"]
    assert maximum == 2
