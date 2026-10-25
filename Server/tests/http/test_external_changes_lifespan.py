"""Embedded server lifespan exit owns scanner work even while its loop stays alive."""

import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

import main
from transport.plugin_hub import PluginHub
from services.state import external_changes_scanner as module
from tests.http.test_server_lifecycle_ownership import lifecycle_state
from tests.timing import watchdog_timeout


@pytest.mark.asyncio
async def test_same_loop_lifespan_exit_stops_pending_external_change_scan(
    lifecycle_state, monkeypatch, tmp_path
):
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    scanner = module.ExternalChangesScanner(max_scan_bytes=8)
    monkeypatch.setattr(module, "external_changes_scanner", scanner)
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Assets/fixture.bin").write_bytes(b"x" * 256)
    scanner.set_project_root("selected", str(tmp_path))
    runner = None
    try:
        async with main.server_lifespan(FastMCP("scanner-lifetime")):
            first = await scanner.update_and_get_async("selected")
            assert first["external_changes_scan_complete"] is False
            state = scanner._states["selected"]
            runner = scanner._progress_task
        assert runner.done(), "Lifespan exit must drain the scanner while the loop remains alive"
        assert state.cursor is None
        assert state.fingerprint is None
        assert not scanner._states
        worker = scanner._worker_future
        await asyncio.sleep(0.025)
        assert scanner._worker_future is worker
        assert state.fingerprint is None
    finally:
        scanner.close()
        if runner is not None:
            await asyncio.gather(runner, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_lifespan_exit_drains_active_scan_worker(
    lifecycle_state, monkeypatch, tmp_path
):
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    scanner = module.ExternalChangesScanner(max_scan_bytes=8)
    monkeypatch.setattr(module, "external_changes_scanner", scanner)
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Assets/fixture.bin").write_bytes(b"x" * 256)
    scanner.set_project_root("selected", str(tmp_path))
    entered, release = Event(), Event()
    ready, exit_started = asyncio.Event(), asyncio.Event()
    advance = scanner._scan_paths_max_mtime_ns

    def blocked(roots):
        entered.set()
        assert release.wait(watchdog_timeout(2))
        return advance(roots)

    async def serve():
        async with main.server_lifespan(FastMCP("scanner-cancel")):
            await scanner.update_and_get_async("selected")
            monkeypatch.setattr(scanner, "_scan_paths_max_mtime_ns", blocked)
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                exit_started.set()

    server = asyncio.create_task(serve())
    try:
        await asyncio.wait_for(ready.wait(), watchdog_timeout(2))
        async with asyncio.timeout(watchdog_timeout(2)):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        server.cancel()
        await asyncio.wait_for(exit_started.wait(), watchdog_timeout(2))
        await asyncio.sleep(0.025)
        assert not server.done(), "A canceled lifespan must still own its blocking scan worker"
        release.set()
        await asyncio.gather(server, return_exceptions=True)
        assert not scanner._scan_lock.locked()
        assert not scanner._states
        assert scanner._progress_task is None
    finally:
        release.set()
        scanner.close()
        server.cancel()
        await asyncio.gather(server, return_exceptions=True)


@pytest.mark.asyncio
async def test_old_lifespan_drain_preserves_new_scanner_generation(
    lifecycle_state, monkeypatch, tmp_path
):
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    scanner = module.ExternalChangesScanner(max_scan_bytes=8)
    monkeypatch.setattr(module, "external_changes_scanner", scanner)
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Assets/fixture.bin").write_bytes(b"x" * 256)
    graph_closing, release_graph = asyncio.Event(), asyncio.Event()

    async def graph_close():
        graph_closing.set()
        await release_graph.wait()
        return True

    # Exercise scanner ownership independently of the hub's own generation rules.
    monkeypatch.setattr(PluginHub, "shutdown", AsyncMock())
    first = FastMCP("old-scanner-generation")
    first._reference_graph_service = SimpleNamespace(aclose=graph_close)
    first_ready, start_exit = asyncio.Event(), asyncio.Event()

    async def serve_first():
        async with main.server_lifespan(first):
            scanner.set_project_root("selected", str(tmp_path))
            await scanner.update_and_get_async("selected")
            first_ready.set()
            await start_exit.wait()

    exit_task = asyncio.create_task(serve_first())
    try:
        await asyncio.wait_for(first_ready.wait(), watchdog_timeout(2))
        start_exit.set()
        await asyncio.wait_for(graph_closing.wait(), watchdog_timeout(2))
        assert not scanner._states
        monkeypatch.setattr(main, "_plugin_registry", None)
        async with main.server_lifespan(FastMCP("new-scanner-generation")):
            scanner.set_project_root("selected", str(tmp_path))
            await scanner.update_and_get_async("selected")
            replacement = scanner._states["selected"]
            runner = scanner._progress_task
            release_graph.set()
            await asyncio.wait_for(exit_task, watchdog_timeout(2))
            assert scanner._states["selected"] is replacement
            await asyncio.wait_for(asyncio.shield(runner), watchdog_timeout(2))
            assert replacement.scan_complete is True
            assert replacement.fingerprint is not None
        assert not scanner._states
    finally:
        start_exit.set()
        release_graph.set()
        scanner.close()
        await asyncio.gather(exit_task, return_exceptions=True)
