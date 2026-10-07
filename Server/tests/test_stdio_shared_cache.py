"""Authenticated generation, stdio read counts, TTL and retained-copy bounds."""

import asyncio
import importlib
import socket
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models.response_limits import ResponseOwner, response_owner, response_size
from services.tools.shared_read_budget import SharedReadBudget
from services.tools.shared_tool_reads import SharedReadCapacityError, SharedToolReads
import services.tools.shared_tool_reads as sharing
import transport.legacy.unity_connection as connections


@pytest.fixture
def budget(monkeypatch):
    value = SharedReadBudget()
    monkeypatch.setattr(sharing, "shared_read_budget", value)
    yield value
    assert value.retained_bytes == 0


@pytest.mark.asyncio
async def test_retained_snapshot_and_detached_delivery_copies_have_separate_lifetimes(budget):
    reads = SharedToolReads(freshness_s=0.02, retention_s=0.02)
    source = {"data": "owned"}
    charge = response_size(source)
    owners = [ResponseOwner(), ResponseOwner()]

    async def fetch():
        return source

    for owner in owners:
        token = response_owner.set(owner)
        try:
            async with reads.session("generation") as read:
                assert await read.fetch(fetch) == source
        finally:
            response_owner.reset(token)
    try:
        assert budget.retained_bytes == 3 * charge
        await asyncio.wait_for(asyncio.shield(read.expiry_task), timeout=1)
        assert budget.retained_bytes == 2 * charge
        owners[0].release()
        assert budget.retained_bytes == charge
    finally:
        for owner in owners:
            owner.release()
        await reads.invalidate("generation")


@pytest.mark.asyncio
async def test_copy_capacity_denial_does_not_repeat_rpc_or_return_uncharged_copy(budget):
    source = {"data": "owned"}
    budget.max_bytes = 2 * response_size(source)
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return source

    first, second = ResponseOwner(), ResponseOwner()
    reads = SharedToolReads(freshness_s=1)
    async with reads.session("generation") as read:
        token = response_owner.set(first)
        try:
            await read.fetch(fetch)
        finally:
            response_owner.reset(token)
        token = response_owner.set(second)
        try:
            with pytest.raises(SharedReadCapacityError):
                await read.fetch(fetch)
        finally:
            response_owner.reset(token)
        assert calls == 1 and not second.entries
    assert budget.retained_bytes > 0
    first.release()
    second.release()


@pytest.mark.asyncio
async def test_cancel_during_cache_expiry_join_cannot_orphan_snapshot(budget):
    reads = SharedToolReads(freshness_s=1, retention_s=0.01)

    async def fetch():
        return {"data": "owned"}

    async with reads.session("generation") as read:
        await read.fetch(fetch)
    read.expiry_task.cancel()
    await asyncio.gather(read.expiry_task, return_exceptions=True)
    entered, drain = asyncio.Event(), asyncio.Event()

    async def parked_expiry():
        try:
            await asyncio.Future()
        finally:
            entered.set()
            await drain.wait()

    read.expiry_task = asyncio.create_task(parked_expiry())
    await asyncio.sleep(0)

    async def join():
        async with reads.session("generation"):
            pytest.fail("cancelled join must not enter lease body")

    waiter = asyncio.create_task(join())
    await entered.wait()
    waiter.cancel()
    drain.set()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.sleep(0.03)
    assert not reads._loops[asyncio.get_running_loop()]


@pytest.mark.asyncio
async def test_socket_fin_invalidates_authenticated_generation_without_sending_command(monkeypatch):
    left, right = socket.socketpair()
    conn = connections.UnityConnection(port=1111, auth_token_provider=lambda _: "x" * 64)
    conn.sock = left
    conn.session_generation = "owned-server:owned-session"
    pool = connections.UnityConnectionPool()
    pool._known_instances = {"Owned@synthetic": SimpleNamespace(id="Owned@synthetic")}
    pool._connections = {"Owned@synthetic": conn}
    monkeypatch.setattr(connections, "_unity_connection_pool", pool)
    monkeypatch.setattr(connections.config, "http_remote_hosted", False)
    try:
        assert (
            await connections.get_authenticated_stdio_generation("Owned@synthetic")
            == conn.session_generation
        )
        right.close()
        assert await connections.get_authenticated_stdio_generation("Owned@synthetic") is None
        assert conn.sock is None and conn.session_generation is None
    finally:
        left.close()
        right.close()


@pytest.mark.asyncio
async def test_generation_probe_never_discovers_or_connects_and_rejects_ambiguous_alias(
    monkeypatch,
):
    def deny(*_, **__):
        pytest.fail("optional cache identity must never discover/connect")

    pool = connections.UnityConnectionPool()
    pool._known_instances = {
        "Owned@one": SimpleNamespace(
            id="Owned@one", name="Owned", hash="one", port=1111, path="owned-one"
        ),
        "Owned@two": SimpleNamespace(
            id="Owned@two", name="Owned", hash="two", port=2222, path="owned-two"
        ),
    }
    monkeypatch.setattr(connections.config, "http_remote_hosted", False)
    monkeypatch.setattr(connections, "get_unity_connection", deny)
    monkeypatch.setattr(connections.PortDiscovery, "discover_all_unity_instances", deny)
    monkeypatch.setattr(connections.UnityConnection, "connect", deny)
    monkeypatch.setattr(connections, "_unity_connection_pool", None)
    assert await connections.get_authenticated_stdio_generation("Owned@one") is None
    monkeypatch.setattr(connections, "_unity_connection_pool", pool)
    assert await connections.get_authenticated_stdio_generation("Owned@one") is None
    assert await connections.get_authenticated_stdio_generation("Owned") is None
    assert await connections.get_authenticated_stdio_generation("missing") is None


def context():
    return SimpleNamespace(get_state=AsyncMock(return_value=None))


def test_retained_snapshot_releases_on_loop_shutdown(budget):
    reads = SharedToolReads(freshness_s=1, retention_s=1)

    async def run():
        async with reads.session("generation") as read:

            async def fetch():
                return {"data": "owned"}

            await read.fetch(fetch)
        assert budget.retained_bytes > 0

    asyncio.run(run())
    assert budget.retained_bytes == 0


def test_fallback_aggregate_budget_is_strict_across_worker_threads():
    bounded = SharedReadBudget(max_bytes=100, max_entries=10)
    owners = [ResponseOwner() for _ in range(32)]
    barrier = threading.Barrier(32)
    results = []

    def reserve(owner):
        barrier.wait()
        results.append(bounded.reserve(owner, 10))

    threads = [threading.Thread(target=reserve, args=(owner,)) for owner in owners]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert sum(results) == 10 and bounded.retained_bytes == 100
    for owner in owners:
        owner.release()
    assert bounded.retained_bytes == 0 and not bounded.entries


@pytest.mark.asyncio
async def test_stdio_twenty_batches_share_one_settings_read_and_preserve_dispatch_order(
    monkeypatch, budget
):
    batch = importlib.import_module("services.tools.batch_execute")
    batch.invalidate_cached_max_commands()
    monkeypatch.setattr(batch.config, "transport_mode", "stdio")
    monkeypatch.setattr(batch.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        batch, "get_unity_instance_from_context", AsyncMock(return_value="Owned@synthetic")
    )
    generation = "authenticated-one"
    monkeypatch.setattr(
        batch, "get_authenticated_stdio_generation", AsyncMock(side_effect=lambda _: generation)
    )
    settings, dispatches = [], []

    async def send(_sender, instance, command, params):
        if command == "get_editor_state":
            settings.append(instance)
            await asyncio.sleep(0.02)
            return {"success": True, "data": {"settings": {"batch_execute_max_commands": 25}}}
        dispatches.append(params)
        return {"success": True}

    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    commands = [{"tool": "read_console", "params": {"count": count}} for count in (1, 2)]
    replies = await asyncio.gather(*(batch.batch_execute(context(), commands) for _ in range(20)))
    assert all(reply["success"] for reply in replies)
    assert len(settings) == 1 and len(dispatches) == 20
    assert all(item["commands"] == commands for item in dispatches)
    generation = "authenticated-replacement"
    await batch.batch_execute(context(), commands)
    assert len(settings) == 2
    batch.invalidate_cached_max_commands()


@pytest.mark.asyncio
async def test_stdio_job_waiters_share_status_but_replacement_and_legacy_stay_private(
    monkeypatch, budget
):
    jobs = importlib.import_module("services.tools.run_tests")
    monkeypatch.setattr(jobs.config, "transport_mode", "stdio")
    monkeypatch.setattr(jobs.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        jobs, "get_unity_instance_from_context", AsyncMock(return_value="Owned@synthetic")
    )
    monkeypatch.setattr(jobs, "_update_job_nudge", AsyncMock())
    generation = "authenticated-one"
    monkeypatch.setattr(
        jobs, "get_authenticated_stdio_generation", AsyncMock(side_effect=lambda _: generation)
    )
    sender = AsyncMock(
        return_value={"success": True, "data": {"job_id": "job", "status": "succeeded"}}
    )
    monkeypatch.setattr(jobs.unity_transport, "send_with_unity_instance", sender)
    replies = await asyncio.gather(
        *(jobs.get_test_job(context(), "job", wait_timeout=1) for _ in range(20))
    )
    assert all(reply.data.status == "succeeded" for reply in replies)
    assert sender.await_count == 1
    generation = None
    await asyncio.gather(*(jobs.get_test_job(context(), "job", wait_timeout=1) for _ in range(2)))
    assert sender.await_count == 3


@pytest.mark.asyncio
async def test_replacement_does_not_join_old_active_stdio_job(monkeypatch, budget):
    jobs = importlib.import_module("services.tools.run_tests")
    monkeypatch.setattr(jobs.config, "transport_mode", "stdio")
    monkeypatch.setattr(jobs.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        jobs, "get_unity_instance_from_context", AsyncMock(return_value="Owned@synthetic")
    )
    monkeypatch.setattr(jobs, "_update_job_nudge", AsyncMock())
    generation = "authenticated-old"
    monkeypatch.setattr(
        jobs, "get_authenticated_stdio_generation", AsyncMock(side_effect=lambda _: generation)
    )
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def send(*_):
        calls.append(generation)
        if generation == "authenticated-old":
            entered.set()
            await release.wait()
            return {"success": True, "data": {"job_id": "job", "status": "succeeded"}}
        return {"success": False, "error": "Job not found in replacement"}

    monkeypatch.setattr(jobs.unity_transport, "send_with_unity_instance", send)
    old = asyncio.create_task(jobs.get_test_job(context(), "job", wait_timeout=1))
    await entered.wait()
    generation = "authenticated-replacement"
    fresh = await jobs.get_test_job(context(), "job", wait_timeout=1)
    release.set()
    assert "replacement" in fresh.error and (await old).data.status == "succeeded"
    assert calls == ["authenticated-old", "authenticated-replacement"]


@pytest.mark.asyncio
async def test_stdio_resource_ttl_generation_and_authoritative_bypass(monkeypatch, budget):
    state = importlib.import_module("services.resources.editor_state")
    reads = SharedToolReads(freshness_s=0.03, retention_s=0.03, max_entries=128)
    loop = asyncio.get_running_loop()
    now = loop.time()
    expiry_waits = []

    async def controlled_sleep(delay):
        future = loop.create_future()
        if delay == 0.03:
            expiry_waits.append(future)
        else:
            loop.call_soon(future.set_result, None)
        await future

    async def expire_snapshots():
        tasks = [
            read.expiry_task
            for read in reads._loops.get(loop, {}).values()
            if read.expiry_task is not None
        ]
        # Start each expiry waiter before releasing its explicit timer barrier.
        future = loop.create_future()
        loop.call_soon(future.set_result, None)
        await future
        for waiting in expiry_waits:
            if not waiting.done():
                waiting.set_result(None)
        await asyncio.gather(*tasks)

    # Keep response enrichment independent of the intended cache age; advance
    # the same clock used by freshness and release expiry at its exact boundary.
    monkeypatch.setattr(loop, "time", lambda: now)
    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)
    monkeypatch.setattr(state, "_stdio_state_reads", reads)
    monkeypatch.setattr(state.config, "transport_mode", "stdio")
    monkeypatch.setattr(state.config, "http_remote_hosted", False)
    monkeypatch.setattr(
        state, "get_unity_instance_from_context", AsyncMock(return_value="Owned@synthetic")
    )
    monkeypatch.setattr(state, "_local_project_root", AsyncMock(return_value=None))
    monkeypatch.setattr(
        state.external_changes_scanner, "update_and_get_async", AsyncMock(return_value={})
    )
    generation = "authenticated-one"
    monkeypatch.setattr(
        state, "get_authenticated_stdio_generation", AsyncMock(side_effect=lambda _: generation)
    )

    async def send(*_):
        await asyncio.sleep(0.005)
        return {"success": True, "data": {"sequence": 1}}

    sender = AsyncMock(side_effect=send)
    monkeypatch.setattr(state.unity_transport, "send_with_unity_instance", sender)
    callers = [asyncio.create_task(state.get_editor_state(context())) for _ in range(20)]
    try:
        replies = await asyncio.gather(*callers)
        assert all(reply.success for reply in replies) and sender.await_count == 1
        await state.get_editor_state(context())
        assert sender.await_count == 1
        await state.get_editor_state_authoritative(context())
        assert sender.await_count == 2
        await state.get_editor_state(context())
        assert sender.await_count == 3
        now += 0.04
        await expire_snapshots()
        await state.get_editor_state(context())
        assert sender.await_count == 4
        generation = "authenticated-replacement"
        await state.get_editor_state(context())
        assert sender.await_count == 5
        now += 0.04
        await expire_snapshots()
    finally:
        for caller in callers:
            caller.cancel()
        await asyncio.gather(*callers, return_exceptions=True)
        for key in tuple(reads._loops.get(loop, {})):
            await reads.invalidate(key)
