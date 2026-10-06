"""Registry changes wake reconnect waits without polling or shared-task cancellation."""
import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from core.config import config
from transport.plugin_hub import NoUnitySessionError, PluginHub
from transport.charge_ledger import ChargeLedger
from transport.plugin_registry import PluginRegistry


@pytest_asyncio.fixture
async def registry_hub(monkeypatch):
    monkeypatch.setattr(config, 'http_remote_hosted', True)
    registry = PluginRegistry()
    monkeypatch.setattr(PluginHub, '_registry', registry)
    # configure/shutdown replace these owners; capture every original before
    # configuring a new isolated registry so no earlier fixture is mutated.
    for name in ('_mcp', '_loop', '_lock', '_editor_states', '_readiness_reads',
                 '_ordinary_state_reads', '_large_results', '_unity_transform_start'):
        monkeypatch.setattr(PluginHub, name, getattr(PluginHub, name))
    for name in ('_connections', '_pending', '_ping_tasks', '_last_pong', '_admitted',
                 '_raw_results', '_retained_results'):
        monkeypatch.setattr(PluginHub, name, ChargeLedger() if name in ('_raw_results', '_retained_results') else {})
    PluginHub.configure(registry)
    monkeypatch.setattr('transport.plugin_hub._read_bounded_wait_env', lambda *args, **kwargs: 2.0)
    return registry


@pytest.mark.asyncio
async def test_registration_between_lookup_and_wait_cannot_be_lost(registry_hub, monkeypatch):
    registry = registry_hub
    event = registry.change_event
    original_lookup = registry.get_session_id_by_hash
    async def lookup(project_hash, user_id=None):
        result = await original_lookup(project_hash, user_id)
        if result is None:
            await registry.register('restored', 'Project', 'hash', 'test', user_id='alice')
        return result
    monkeypatch.setattr(registry, 'get_session_id_by_hash', lookup)
    assert await PluginHub._resolve_session_id('hash', user_id='alice') == 'restored'
    assert event.is_set()


@pytest.mark.asyncio
async def test_cancel_one_waiter_does_not_cancel_shared_notification(registry_hub, monkeypatch):
    registry = registry_hub
    entered, count = asyncio.Event(), 0
    original_wait = registry.wait_for_change
    async def wait(event, timeout):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        return await original_wait(event, timeout)
    monkeypatch.setattr(registry, 'wait_for_change', wait)
    first = asyncio.create_task(PluginHub._resolve_session_id('hash', user_id='alice'))
    second = asyncio.create_task(PluginHub._resolve_session_id('hash', user_id='alice'))
    try:
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await registry.register('restored', 'Project', 'hash', 'test', user_id='alice')
        assert await second == 'restored'
    finally:
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_foreign_notifications_keep_absolute_deadline_and_never_resolve(registry_hub, monkeypatch):
    clock, remaining = [0.0], []
    monkeypatch.setattr('transport.plugin_hub.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('transport.plugin_hub._read_bounded_wait_env', lambda *args, **kwargs: 1.0)
    async def wake(event, timeout):
        remaining.append(timeout)
        clock[0] += 0.4
        await registry_hub.register('foreign', 'Project', 'hash', 'test', user_id='bob')
        return True
    monkeypatch.setattr(registry_hub, 'wait_for_change', wake)
    with pytest.raises(NoUnitySessionError):
        await PluginHub._resolve_session_id('hash', user_id='alice')
    assert remaining == pytest.approx([1.0, 0.6, 0.2])


@pytest.mark.asyncio
async def test_touch_does_not_wake_but_unregister_and_clear_do(registry_hub):
    registry = registry_hub
    await registry.register('owned', 'Project', 'hash', 'test', user_id='alice')
    event = registry.change_event
    await registry.touch('owned')
    assert registry.change_event is event and not event.is_set()
    await registry.unregister('owned')
    assert event.is_set()
    event = registry.change_event
    await registry.clear()
    assert event.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize('replace', [False, True])
async def test_shutdown_or_configure_replacement_wakes_old_registry_waiter(registry_hub, monkeypatch, replace):
    registry = registry_hub
    entered = asyncio.Event()
    original_wait = registry.wait_for_change
    async def wait(event, timeout):
        entered.set()
        return await original_wait(event, timeout)
    monkeypatch.setattr(registry, 'wait_for_change', wait)
    for name in ('_pending', '_ping_tasks', '_last_pong', '_admitted', '_raw_results'):
        monkeypatch.setattr(PluginHub, name, {})
    task = asyncio.create_task(PluginHub._resolve_session_id('hash', user_id='alice'))
    try:
        await entered.wait()
        if replace:
            PluginHub.configure(PluginRegistry())
        else:
            await PluginHub.shutdown()
        with pytest.raises(NoUnitySessionError, match='stopped or replaced'):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_no_retry_miss_does_not_wait_and_success_does_not_subscribe(registry_hub, monkeypatch):
    wait = AsyncMock(side_effect=AssertionError('no wait required'))
    monkeypatch.setattr(registry_hub, 'wait_for_change', wait)
    with pytest.raises(NoUnitySessionError):
        await PluginHub._resolve_session_id('hash', user_id='alice', retry_on_reload=False)
    await registry_hub.register('owned', 'Project', 'hash', 'test', user_id='alice')
    assert await PluginHub._resolve_session_id('hash', user_id='alice') == 'owned'
    wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_after_absolute_deadline_cannot_admit_late_registration(registry_hub, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('transport.plugin_hub.time.monotonic', lambda: clock[0])
    async def late_wake(event, timeout):
        clock[0] = 3.0
        await registry_hub.register('late', 'Project', 'hash', 'test', user_id='alice')
        return True
    monkeypatch.setattr(registry_hub, 'wait_for_change', late_wake)
    with pytest.raises(NoUnitySessionError):
        await PluginHub._resolve_session_id('hash', user_id='alice')


@pytest.mark.asyncio
async def test_cancelled_shutdown_notifies_before_owned_ping_drain(registry_hub, monkeypatch):
    waiting, ping_started, draining, release = (asyncio.Event() for _ in range(4))
    event = registry_hub.change_event
    original_wait = registry_hub.wait_for_change
    async def wait(observed, timeout):
        waiting.set()
        return await original_wait(observed, timeout)
    monkeypatch.setattr(registry_hub, 'wait_for_change', wait)
    async def ping():
        ping_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            draining.set()
            await release.wait()
    resolver = asyncio.create_task(PluginHub._resolve_session_id('hash', user_id='alice'))
    owned_ping = asyncio.create_task(ping())
    shutdown = None
    try:
        await waiting.wait()
        await ping_started.wait()
        PluginHub._ping_tasks['owned'] = owned_ping
        shutdown = asyncio.create_task(PluginHub.shutdown())
        await draining.wait()
        # Revocation is observable while slow ping cleanup is still blocked.
        assert PluginHub._registry is None and event.is_set()
        shutdown.cancel()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        with pytest.raises(NoUnitySessionError, match='stopped or replaced'):
            await resolver
    finally:
        release.set()
        tasks = [resolver, owned_ping] + ([shutdown] if shutdown is not None else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit', [False, True])
async def test_resolution_waiting_for_registry_lock_cannot_return_old_session_after_reconfigure(registry_hub, monkeypatch, explicit):
    await registry_hub.register('old', 'Project', 'hash', 'test', user_id='alice')
    if not explicit:
        monkeypatch.setattr(config, 'http_remote_hosted', False)
    entered = asyncio.Event()
    method = 'get_session_id_by_hash' if explicit else 'list_sessions'
    original = getattr(registry_hub, method)
    async def lookup(*args, **kwargs):
        entered.set()
        return await original(*args, **kwargs)
    monkeypatch.setattr(registry_hub, method, lookup)
    task = None
    try:
        async with registry_hub._lock:
            task = asyncio.create_task(PluginHub._resolve_session_id('hash' if explicit else None, user_id='alice'))
            await entered.wait()
            PluginHub.configure(PluginRegistry())
        with pytest.raises(NoUnitySessionError, match='stopped or replaced'):
            await task
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
