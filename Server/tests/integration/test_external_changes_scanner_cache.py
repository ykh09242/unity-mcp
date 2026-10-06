"""Cached readiness metadata must not wait for unrelated executor jobs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import threading

import pytest

from services.state import external_changes_scanner as module


@pytest.fixture
def local_scanner(monkeypatch, tmp_path):
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    now = [1000]
    monkeypatch.setattr(module, "_now_unix_ms", lambda: now[0])
    root = tmp_path / "Project"
    (root / "Assets").mkdir(parents=True)
    asset = root / "Assets/asset.txt"
    asset.write_text("original", encoding="utf-8")
    os.utime(asset, ns=(1_000_000_000, 1_000_000_000))
    scanner = module.ExternalChangesScanner(scan_interval_ms=1500)
    scanner.set_project_root("Selected@one", str(root))
    expected = scanner.update_and_get("Selected@one")
    return scanner, now, asset, expected


@pytest.mark.asyncio
async def test_cached_snapshot_returns_while_executor_worker_is_occupied(local_scanner, monkeypatch):
    scanner, _, asset, expected = local_scanner
    # A change inside the existing throttle window must still wait for the next scan.
    os.utime(asset, ns=(2_000_000_000, 2_000_000_000))
    loop = asyncio.get_running_loop()
    run_in_executor = loop.run_in_executor
    release = threading.Event()
    started = loop.create_future()

    def occupy():
        loop.call_soon_threadsafe(started.set_result, None)
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(loop, "run_in_executor", lambda _, fn, *args: run_in_executor(executor, fn, *args))
        busy = executor.submit(occupy)
        await started
        read = asyncio.create_task(scanner.update_and_get_async("Selected@one"))
        try:
            await asyncio.sleep(0)
            assert read.done(), "A fresh cached snapshot queued behind an unrelated executor job"
            assert read.result() == expected
        finally:
            release.set()
            await read
            await asyncio.wrap_future(busy)


@pytest.mark.asyncio
async def test_expired_snapshot_scans_in_worker_and_detects_changes(local_scanner, monkeypatch):
    scanner, now, asset, expected = local_scanner
    os.utime(asset, ns=(2_000_000_000, 2_000_000_000))
    threads = []
    scan = scanner._scan_paths_max_mtime_ns

    def record_scan(roots):
        threads.append(threading.get_ident())
        return scan(roots)

    monkeypatch.setattr(scanner, "_scan_paths_max_mtime_ns", record_scan)
    now[0] = 2499
    assert await scanner.update_and_get_async("Selected@one") == expected
    assert scanner._states["Selected@one"].last_scan_unix_ms == 1000
    now[0] = 2500
    observed = await scanner.update_and_get_async("Selected@one")
    assert observed["external_changes_dirty"] is True
    assert observed["dirty_since_unix_ms"] == 2500
    assert len(threads) == 1 and threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_cached_snapshot_preserves_clear_and_project_reassignment(local_scanner, tmp_path):
    scanner, now, asset, _ = local_scanner
    os.utime(asset, ns=(2_000_000_000, 2_000_000_000))
    now[0] = 2500
    assert (await scanner.update_and_get_async("Selected@one"))["external_changes_dirty"]
    scanner.clear_dirty("Selected@one")
    cleared = await scanner.update_and_get_async("Selected@one")
    assert cleared["external_changes_dirty"] is False
    assert cleared["last_cleared_unix_ms"] == 2500
    other = tmp_path / "Other"
    (other / "Assets").mkdir(parents=True)
    target = other / "Assets/other.txt"
    target.write_text("other", encoding="utf-8")
    os.utime(target, ns=(3_000_000_000, 3_000_000_000))
    scanner.set_project_root("Selected@one", str(other))
    observed = await scanner.update_and_get_async("Selected@one")
    assert observed["external_changes_dirty"] is False
    assert observed["last_cleared_unix_ms"] is None
    assert scanner._states["Selected@one"].last_seen_mtime_ns == 3_000_000_000


@pytest.mark.asyncio
async def test_cached_snapshot_refreshes_lru_but_does_not_revive_expired_state(monkeypatch):
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    now = [1000]
    monkeypatch.setattr(module, "_now_unix_ms", lambda: now[0])
    scanner = module.ExternalChangesScanner(scan_interval_ms=1500, state_ttl_ms=100, max_states=2)
    scanner.update_and_get("one")
    scanner.update_and_get("two")
    first = scanner._states["one"]
    first.dirty = True
    now[0] = 1050
    assert (await scanner.update_and_get_async("one"))["external_changes_dirty"]
    assert list(scanner._states) == ["two", "one"]
    assert scanner._last_access["one"] == 1050
    now[0] = 1150
    assert not (await scanner.update_and_get_async("one"))["external_changes_dirty"]
    assert scanner._states["one"] is not first
    assert list(scanner._states) == ["one"]
