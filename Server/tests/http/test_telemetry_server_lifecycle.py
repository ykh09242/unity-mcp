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
async def test_lifespan_stops_actual_worker_off_event_loop(
    lifecycle_state, controlled_telemetry, monkeypatch
):
    telemetry, _, _, _ = controlled_telemetry
    loop_thread = threading.get_ident()
    close_threads = []
    async with main.server_lifespan(FastMCP("telemetry-owner")):
        collector = telemetry.get_telemetry()
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
            collector = telemetry.get_telemetry()
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
