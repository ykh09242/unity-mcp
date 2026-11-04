"""Server lifetimes own real telemetry workers and fence deferred callbacks."""

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

import main
from tests.http.test_server_lifecycle_ownership import lifecycle_state
from tests.test_telemetry_initialization_lifecycle import controlled_telemetry
from tests.timing import watchdog_timeout
from transport.plugin_hub import PluginHub


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["first-use", "shutdown-during-construction"])
async def test_constructor_never_blocks_event_loop_heartbeat(
    controlled_telemetry, monkeypatch, operation
):
    telemetry, _, _, _ = controlled_telemetry
    constructing, release, finished = threading.Event(), threading.Event(), threading.Event()
    heartbeat = asyncio.Event()
    heartbeat_before_release = []
    collectors = []

    def load(collector):
        collectors.append(collector)
        constructing.set()
        assert release.wait(watchdog_timeout())

    def watchdog():
        # Release even when the event loop is stuck, so RED fails rather than hangs.
        finished.wait(0.2)
        heartbeat_before_release.append(heartbeat.is_set())
        release.set()

    monkeypatch.setattr(telemetry.TelemetryCollector, "_load_persistent_data", load)
    initialization = None
    close = None
    if operation == "shutdown-during-construction":
        initialization = asyncio.create_task(asyncio.to_thread(telemetry.get_telemetry))
        async with asyncio.timeout(watchdog_timeout()):
            while not constructing.is_set():
                await asyncio.sleep(0)
    watcher = threading.Thread(target=watchdog)
    watcher.start()
    try:
        asyncio.get_running_loop().call_soon(heartbeat.set)
        if operation == "first-use":
            telemetry.record_tool_usage("async_tool", True, 1.0)
        else:
            telemetry.record_tool_usage("contending_async_tool", True, 1.0)
            close = telemetry.begin_telemetry_shutdown()
        await heartbeat.wait()
        finished.set()
        await asyncio.to_thread(watcher.join, watchdog_timeout())
        assert heartbeat_before_release == [True], "Telemetry blocked the event loop"
    finally:
        finished.set()
        release.set()
        await asyncio.to_thread(watcher.join, watchdog_timeout())
        if initialization is not None:
            await initialization
        await asyncio.to_thread(telemetry.shutdown_telemetry)
    if close is not None:
        await asyncio.to_thread(close)
    assert all(not collector._worker.is_alive() for collector in collectors)


@pytest.mark.asyncio
async def test_owner_rollover_keeps_unpublished_initializer_owned(
    controlled_telemetry, monkeypatch
):
    telemetry, _, _, requests = controlled_telemetry
    constructing, release = threading.Event(), threading.Event()
    collectors = []

    def load(collector):
        collectors.append(collector)
        constructing.set()
        assert release.wait(watchdog_timeout())

    monkeypatch.setattr(telemetry.TelemetryCollector, "_load_persistent_data", load)
    old_owner = telemetry.start_telemetry()
    initialization = asyncio.create_task(asyncio.to_thread(telemetry.get_telemetry))
    try:
        async with asyncio.timeout(watchdog_timeout()):
            while not constructing.is_set():
                await asyncio.sleep(0)
        captured = telemetry._telemetry_initialization
        # Model the bounded observation expiring while the constructor remains held.
        monkeypatch.setattr(captured.done, "wait", lambda **_kwargs: False)
        await asyncio.to_thread(telemetry.shutdown_telemetry, old_owner)
        current_owner = telemetry.start_telemetry()
        telemetry.record_telemetry(telemetry.RecordType.STARTUP, {}, owner=current_owner)
        assert telemetry.get_telemetry() is None
        assert len(collectors) == 1
        assert telemetry._telemetry_initialization is captured
        release.set()
        assert await initialization is None
        assert not collectors[0]._worker.is_alive()
        replacement = await asyncio.to_thread(telemetry.get_telemetry)
        assert replacement is not collectors[0]
        assert replacement._worker.is_alive()
        assert requests == []
    finally:
        release.set()
        await initialization
        await asyncio.to_thread(telemetry.shutdown_telemetry)


@pytest.mark.asyncio
async def test_lifespan_stops_actual_worker_off_event_loop(
    lifecycle_state, controlled_telemetry, monkeypatch
):
    telemetry, _, _, _ = controlled_telemetry
    loop_thread = threading.get_ident()
    close_threads = []
    async with main.server_lifespan(FastMCP("telemetry-owner")):
        collector = await asyncio.to_thread(telemetry.get_telemetry)
        shutdown = collector.shutdown

        def close():
            close_threads.append(threading.get_ident())
            shutdown()

        monkeypatch.setattr(collector, "shutdown", close)
        assert collector._worker.is_alive()
    assert not collector._worker.is_alive()
    assert close_threads and loop_thread not in close_threads
    # A timer that was already dispatching when canceled must not recreate telemetry.
    lifecycle_state.call_args.args[1]()
    assert telemetry.get_telemetry() is None


@pytest.mark.asyncio
async def test_old_lifespan_timer_and_cleanup_preserve_new_telemetry_owner(
    lifecycle_state, controlled_telemetry, monkeypatch
):
    telemetry, _, _, requests = controlled_telemetry
    monkeypatch.setattr(PluginHub, "shutdown", AsyncMock())
    ready, finish = asyncio.Event(), asyncio.Event()

    async def serve_old():
        async with main.server_lifespan(FastMCP("old-telemetry-owner")):
            ready.set()
            await finish.wait()

    old = asyncio.create_task(serve_old())
    await asyncio.wait_for(ready.wait(), watchdog_timeout())
    old_callback = lifecycle_state.call_args.args[1]
    try:
        async with main.server_lifespan(FastMCP("new-telemetry-owner")):
            collector = await asyncio.to_thread(telemetry.get_telemetry)
            finish.set()
            await asyncio.wait_for(old, watchdog_timeout())
            assert collector._worker.is_alive()
            assert telemetry.get_telemetry() is collector
            old_callback()
            assert collector._queue.empty()
            assert collector._milestones == {}
            assert requests == []
    finally:
        finish.set()
        await asyncio.wait_for(old, watchdog_timeout())
    assert not collector._worker.is_alive()
