"""Batch cache misses share settings reads while retaining independent dispatch."""
import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def batch(monkeypatch):
    module = importlib.import_module("services.tools.batch_execute")
    module.invalidate_cached_max_commands()
    monkeypatch.setattr(module.config, "transport_mode", "http")
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(side_effect=lambda ctx: ctx.instance))
    yield module
    module.invalidate_cached_max_commands()


def context(user="a", instance="Game@one", session="owned-session"):
    return SimpleNamespace(instance=instance, get_state=AsyncMock(side_effect=lambda key: {
        "user_id": user, "unity_session_id": session,
    }.get(key)))


@pytest.mark.asyncio
async def test_twenty_simultaneous_batches_share_settings_but_keep_all_commands(batch, monkeypatch):
    settings = []
    dispatches = []

    async def send(_send_fn, instance, command, params):
        if command == "get_editor_state":
            settings.append(instance)
            await asyncio.sleep(0.02)
            return {"success": True, "data": {"settings": {"batch_execute_max_commands": 3}}}
        dispatches.append(params)
        return {"success": True}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    commands = [{"tool": "read_console", "params": {"count": 1}}, {"tool": "read_console", "params": {"count": 2}}]
    replies = await asyncio.gather(*(
        batch.batch_execute(context(), commands, parallel=False, fail_fast=True) for _ in range(20)
    ))
    assert all(reply["success"] for reply in replies)
    assert len(settings) == 1
    assert len(dispatches) == 20
    assert all(payload == {"commands": commands, "parallel": False, "failFast": True} for payload in dispatches)


@pytest.mark.asyncio
async def test_invalidation_during_shared_read_does_not_publish_old_limit(batch, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def send(*args):
        nonlocal calls
        calls += 1
        limit = 1 if calls == 1 else 3
        if calls == 1:
            entered.set()
            await release.wait()
        return {"success": True, "data": {"settings": {"batch_execute_max_commands": limit}}}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    ctx = context()
    old = asyncio.create_task(batch._get_max_commands_from_editor_state(ctx, ctx.instance))
    await entered.wait()
    batch.invalidate_cached_max_commands()
    fresh = await batch._get_max_commands_from_editor_state(ctx, ctx.instance)
    release.set()
    assert await old == 1
    assert fresh == 3
    assert await batch._get_max_commands_from_editor_state(ctx, ctx.instance) == 3
    assert calls == 2


@pytest.mark.asyncio
async def test_one_cancelled_batch_does_not_cancel_other_batch_settings(batch, monkeypatch):
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def send(_send_fn, instance, command, params):
        nonlocal calls
        if command == "get_editor_state":
            calls += 1
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return {"success": True, "data": {"settings": {"batch_execute_max_commands": 3}}}
        return {"success": True}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    tasks = [asyncio.create_task(batch.batch_execute(context(), [{"tool": "read_console"}])) for _ in range(2)]
    await entered.wait()
    await asyncio.sleep(0.02)
    tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[0]
    assert not cancelled.is_set()
    release.set()
    assert (await tasks[1])["success"]
    assert calls == 1


@pytest.mark.asyncio
async def test_shared_settings_are_isolated_by_owner_and_editor(batch, monkeypatch):
    calls = 0

    async def send(*args):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {"success": True, "data": {"settings": {"batch_execute_max_commands": 3}}}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    contexts = [context(), context(), context(user="b"), context(instance="Game@two")]
    assert await asyncio.gather(*(batch._get_max_commands_from_editor_state(ctx, ctx.instance) for ctx in contexts)) == [3] * 4
    assert calls == 3


@pytest.mark.asyncio
async def test_missing_remote_owner_does_not_share_or_cache_settings(batch, monkeypatch):
    monkeypatch.setattr(batch.config, "http_remote_hosted", True)
    calls = 0

    async def send(*args):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {"success": True, "data": {"settings": {"batch_execute_max_commands": 3}}}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    ctx = context(user=None)
    await asyncio.gather(*(batch._get_max_commands_from_editor_state(ctx, ctx.instance) for _ in range(2)))
    assert calls == 2
    assert batch._cached_max_commands == {}


@pytest.mark.asyncio
async def test_transport_changes_do_not_reuse_other_transport_limit(batch, monkeypatch):
    calls = 0

    async def send(*args):
        nonlocal calls
        calls += 1
        return {"success": True, "data": {"settings": {"batch_execute_max_commands": calls}}}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    ctx = context()
    monkeypatch.setattr(batch.config, "transport_mode", "stdio")
    assert await batch._get_max_commands_from_editor_state(ctx, ctx.instance) == 1
    monkeypatch.setattr(batch.config, "transport_mode", "http")
    assert await batch._get_max_commands_from_editor_state(ctx, ctx.instance) == 2


@pytest.mark.asyncio
async def test_replacement_http_session_does_not_join_old_limit_read_or_cache(batch, monkeypatch):
    monkeypatch.setattr(batch.config, "transport_mode", "http")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def send(*args):
        nonlocal calls
        calls += 1
        limit = 1 if calls == 1 else 3
        if calls == 1:
            entered.set()
            await release.wait()
        return {"success": True, "data": {"settings": {"batch_execute_max_commands": limit}}}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    old_context, new_context = context(session="old-session"), context(session="new-session")
    old = asyncio.create_task(batch._get_max_commands_from_editor_state(old_context, old_context.instance))
    await entered.wait()
    assert await batch._get_max_commands_from_editor_state(new_context, new_context.instance) == 3
    release.set()
    assert await old == 1
    assert await batch._get_max_commands_from_editor_state(new_context, new_context.instance) == 3
    assert calls == 2


@pytest.mark.asyncio
async def test_missing_http_session_does_not_share_or_cache_limits(batch, monkeypatch):
    monkeypatch.setattr(batch.config, "transport_mode", "http")
    calls = 0

    async def send(*args):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {"success": True, "data": {"settings": {"batch_execute_max_commands": 3}}}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    ctx = context(session=None)
    await asyncio.gather(*(batch._get_max_commands_from_editor_state(ctx, ctx.instance) for _ in range(2)))
    assert calls == 2 and batch._cached_max_commands == {}


@pytest.mark.asyncio
async def test_limit_copy_capacity_returns_bounded_error_without_batch_dispatch(batch, monkeypatch):
    from services.tools.shared_tool_reads import SharedRead, SharedReadCapacityError
    sender = AsyncMock()
    monkeypatch.setattr(batch, "send_with_unity_instance", sender)
    monkeypatch.setattr(SharedRead, "fetch", AsyncMock(side_effect=SharedReadCapacityError()))
    reply = await batch.batch_execute(context(), [{"tool": "read_console"}])
    assert reply["success"] is False and reply["data"] == {"reason": "result_capacity"}
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_stdio_limits_stay_fresh_without_a_reliable_socket_generation(batch, monkeypatch):
    monkeypatch.setattr(batch.config, "transport_mode", "stdio")
    calls = 0

    async def send(*args):
        nonlocal calls
        calls += 1
        return {"success": True, "data": {"settings": {"batch_execute_max_commands": calls}}}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    ctx = context()
    assert await batch._get_max_commands_from_editor_state(ctx, ctx.instance) == 1
    assert await batch._get_max_commands_from_editor_state(ctx, ctx.instance) == 2
    assert batch._cached_max_commands == {}
