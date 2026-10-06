"""Read lookup costs stay bounded without changing selection or weak ownership."""
import asyncio
import gc
import weakref
from unittest.mock import AsyncMock

import pytest

from core.config import config
from services.tools.shared_read_budget import SharedReadBudget
import services.tools.shared_tool_reads as sharing
from transport.plugin_hub import InstanceSelectionRequiredError, NoUnitySessionError, PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.mark.asyncio
async def test_twenty_overlapping_leases_construct_one_loop_registry(monkeypatch):
    constructions = []
    original = sharing.WeakValueDictionary

    def construct():
        value = original()
        constructions.append(weakref.ref(value))
        return value

    budget = SharedReadBudget()
    monkeypatch.setattr(sharing, "WeakValueDictionary", construct)
    monkeypatch.setattr(sharing, "shared_read_budget", budget)
    pool = sharing.SharedToolReads()
    entered = 0
    joined, release = asyncio.Event(), asyncio.Event()
    source = AsyncMock()

    async def fetch():
        await release.wait()
        return {"success": True, "data": {"value": "owned"}}

    source.side_effect = fetch

    async def caller():
        nonlocal entered
        async with pool.session("owned-generation") as read:
            entered += 1
            if entered == 20:
                joined.set()
            return await read.fetch(source)

    callers = [asyncio.create_task(caller()) for _ in range(20)]
    try:
        await joined.wait()
        release.set()
        replies = await asyncio.gather(*callers)
        assert all(reply["success"] for reply in replies)
        assert source.await_count == 1
        assert len(constructions) == 1
        assert len(pool._loops[asyncio.get_running_loop()]) == 0
    finally:
        for task in callers:
            task.cancel()
        await asyncio.gather(*callers, return_exceptions=True)
        await pool.invalidate("owned-generation")
    assert budget.retained_bytes == 0


def test_loop_registry_remains_distinct_and_weakly_owned(monkeypatch):
    original = sharing.WeakValueDictionary
    constructed = []

    def construct():
        value = original()
        constructed.append(weakref.ref(value))
        return value

    monkeypatch.setattr(sharing, "WeakValueDictionary", construct)
    pool = sharing.SharedToolReads()

    async def lease_twice():
        for _ in range(2):
            async with pool.session("owned-generation"):
                pass

    loops = [asyncio.new_event_loop(), asyncio.new_event_loop()]
    loop_refs = [weakref.ref(loop) for loop in loops]
    try:
        for loop in loops:
            loop.run_until_complete(lease_twice())
        assert len(constructed) == 2
        assert len(pool._loops) == 2
        assert constructed[0]() is not constructed[1]()
    finally:
        for loop in loops:
            loop.close()
    del loop, loops
    gc.collect()
    assert all(reference() is None for reference in loop_refs)
    assert all(reference() is None for reference in constructed)
    assert len(pool._loops) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_explicit_success_resolves_hash_without_listing(monkeypatch, remote):
    monkeypatch.setattr(config, "http_remote_hosted", remote)
    registry = PluginRegistry()
    for owner in ("owned-a", "owned-b", "owned-c") if remote else (None,):
        for index in range(16):
            await registry.register(f"{owner}-{index}", "Synthetic", f"hash-{index}", "test", user_id=owner)
    monkeypatch.setattr(PluginHub, "_registry", registry)
    lookup = AsyncMock(wraps=registry.get_session_id_by_hash)
    listing = AsyncMock(wraps=registry.list_sessions)
    monkeypatch.setattr(registry, "get_session_id_by_hash", lookup)
    monkeypatch.setattr(registry, "list_sessions", listing)
    owner = "owned-a" if remote else None
    replies = await asyncio.gather(*(
        PluginHub._resolve_session_id("Synthetic@hash-0", user_id=owner, retry_on_reload=False)
        for _ in range(20)
    ))
    assert replies == [f"{owner}-0"] * 20
    assert lookup.await_count == 20
    listing.assert_not_awaited()
    await registry.register("owned-replacement", "Synthetic", "hash-0", "test", user_id=owner)
    assert await PluginHub._resolve_session_id("hash-0", user_id=owner, retry_on_reload=False) == "owned-replacement"
    assert lookup.await_count == 21
    listing.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_remote_principal_keeps_listing_guard_even_if_hash_resolves(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    registry = PluginRegistry()
    # A preexisting local mapping must not bypass the remote principal guard.
    await registry.register("owned-local", "Synthetic", "hash", "test")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(PluginHub, "_registry", registry)
    listing = AsyncMock(wraps=registry.list_sessions)
    monkeypatch.setattr(registry, "list_sessions", listing)
    with pytest.raises(ValueError, match="list_sessions requires user_id"):
        await PluginHub._resolve_session_id("Synthetic@hash", retry_on_reload=False)
    listing.assert_awaited_once_with(user_id=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_explicit_unknown_keeps_existing_miss_and_listing(monkeypatch, remote):
    monkeypatch.setattr(config, "http_remote_hosted", remote)
    registry = PluginRegistry()
    monkeypatch.setattr(PluginHub, "_registry", registry)
    listing = AsyncMock(wraps=registry.list_sessions)
    monkeypatch.setattr(registry, "list_sessions", listing)
    owner = "owned-user" if remote else None
    with pytest.raises(NoUnitySessionError, match="No Unity plugins"):
        await PluginHub._resolve_session_id("unknown", user_id=owner, retry_on_reload=False)
    listing.assert_awaited_once_with(user_id=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote,count", [(False, 0), (False, 1), (False, 2), (True, 1), (True, 2)])
async def test_omitted_selector_preserves_count_based_selection(monkeypatch, remote, count):
    monkeypatch.setattr(config, "http_remote_hosted", remote)
    registry = PluginRegistry()
    owner = "owned-user" if remote else None
    for index in range(count):
        await registry.register(f"owned-{index}", "Synthetic", f"hash-{index}", "test", user_id=owner)
    monkeypatch.setattr(PluginHub, "_registry", registry)
    lookup = AsyncMock(wraps=registry.get_session_id_by_hash)
    listing = AsyncMock(wraps=registry.list_sessions)
    monkeypatch.setattr(registry, "get_session_id_by_hash", lookup)
    monkeypatch.setattr(registry, "list_sessions", listing)
    if count == 0:
        with pytest.raises(NoUnitySessionError):
            await PluginHub._resolve_session_id(None, user_id=owner, retry_on_reload=False)
    elif count == 1 and not remote:
        assert await PluginHub._resolve_session_id(None, retry_on_reload=False) == "owned-0"
    else:
        with pytest.raises(InstanceSelectionRequiredError):
            await PluginHub._resolve_session_id(None, user_id=owner, retry_on_reload=False)
    lookup.assert_not_awaited()
    assert listing.await_count >= 1
