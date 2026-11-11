"""Exercise dependency behaviors used by the reviewed SDK runtime."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.context import StateValue
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ArgTransform
from key_value.aio.stores.memory import MemoryStore
import pytest
from sse_starlette.sse import EventSourceResponse
from starlette.types import Message


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_transformed_tool_mutable_default_is_fresh_for_each_call(mode):
    # Given: a transformed tool whose default container is modified by its body.
    def append_item(items: list[str]) -> dict[str, list[str]]:
        items.append("owned")
        return {"items": items}

    server = FastMCP("reviewed-transform-default")
    transformed = Tool.from_tool(
        append_item, name="append_item", transform_args={"items": ArgTransform(default=[])}
    )
    server.add_tool(transformed)

    # When: both supported SDK modes invoke it repeatedly without an explicit list.
    async with Client(server, mode=mode, cache=False) as client:
        first = await client.call_tool("append_item")
        second = await client.call_tool("append_item")

    # Then: defaults never accumulate values or alter the published input schema.
    assert first.structured_content == second.structured_content == {"items": ["owned"]}
    assert transformed.parameters["properties"]["items"]["default"] == []


@pytest.mark.asyncio
async def test_memory_store_preserves_eviction_collection_and_snapshot_semantics():
    # Given: the SDK's memory backend uses cachetools TLRU with a finite collection.
    store = MemoryStore(max_entries_per_collection=2)
    original = {"items": ["original"]}
    await store.put("first", original, collection="one")
    await store.put("second", {"value": 2}, collection="one")
    await store.put("first", {"value": "isolated"}, collection="two")

    # When: callers mutate input/output values and refresh one entry's LRU order.
    original["items"].append("changed")
    retrieved = await store.get("first", collection="one")
    assert retrieved == {"items": ["original"]}
    retrieved["items"].append("changed again")
    await store.put("third", {"value": 3}, collection="one")

    # Then: snapshots and collections stay isolated, and only the oldest key leaves.
    assert await store.get("first", collection="one") == {"items": ["original"]}
    assert await store.get("second", collection="one") is None
    assert await store.get("third", collection="one") == {"value": 3}
    assert await store.get("first", collection="two") == {"value": "isolated"}
    assert await store.delete("first", collection="one") is True
    assert await store.delete("first", collection="one") is False


@pytest.mark.asyncio
async def test_sdk_memory_state_roundtrips_and_expires_without_sleep(monkeypatch):
    from key_value.aio._utils import managed_entry, time_to_live

    # Given: FastMCP's real Pydantic adapter over its default MemoryStore.
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    monkeypatch.setattr(time_to_live, "now", lambda: now)
    monkeypatch.setattr(managed_entry, "now", lambda: now)
    server = FastMCP("reviewed-memory-state")
    store = server._state_store
    await store.put("session-a:key", StateValue(value={"target": "owned"}), ttl=5)
    await store.put("session-b:key", StateValue(value={"target": "other"}))

    # When: logical time reaches the finite state's expiration boundary.
    assert (await store.get("session-a:key")).value == {"target": "owned"}
    now += timedelta(seconds=5)

    # Then: expired state disappears without erasing another session's state.
    assert await store.get("session-a:key") is None
    assert (await store.get("session-b:key")).value == {"target": "other"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["send", "encoding"])
async def test_sse_cleanup_deadline_preserves_original_failure(failure):
    # Given: an iterator whose cleanup would otherwise keep a failed response alive.
    cleanup_started = anyio.Event()
    cleanup_cancelled = anyio.Event()

    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            yield {"invalid_event_field": "value"} if failure == "encoding" else {"data": "x"}
        finally:
            cleanup_started.set()
            try:
                await anyio.sleep_forever()
            finally:
                cleanup_cancelled.set()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            raise BrokenPipeError("owned interrupted send")

    async def receive() -> Message:
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    response = EventSourceResponse(events(), cleanup_timeout=0.01)
    expected = TypeError if failure == "encoding" else BrokenPipeError
    # When: encoding or transport delivery fails on the real ASGI response path.
    with anyio.fail_after(2):
        with pytest.raises(expected):
            await response({"type": "http", "method": "GET"}, receive, send)

    # Then: cleanup is cancelled within its own budget and the cause still propagates.
    assert cleanup_started.is_set()
    assert cleanup_cancelled.is_set()
