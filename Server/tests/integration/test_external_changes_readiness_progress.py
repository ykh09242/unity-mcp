"""Readiness scans make bounded progress between polls without losing verified coverage."""

import os
import time
import asyncio

import pytest

from services.state import external_changes_scanner as module
from tests.timing import watchdog_timeout


@pytest.fixture
def local_scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    monkeypatch.setattr(module, "_now_unix_ms", lambda: 1000)
    scanner = module.ExternalChangesScanner(max_scan_seconds=10)
    scanner.set_project_root("selected", str(tmp_path))
    asset = tmp_path / "Assets/asset.bin"
    asset.parent.mkdir()
    yield scanner, asset
    scanner.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_pending_scan_advances_within_the_throttle_window(local_scanner, asynchronous):
    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 32)
    scanner._max_scan_bytes = 32
    results = []
    cursors = []
    for _ in range(12):
        results.append(
            await scanner.update_and_get_async("selected")
            if asynchronous
            else scanner.update_and_get("selected")
        )
        cursor = scanner._states["selected"].cursor
        if cursor is not None:
            cursors.append(cursor)
        if results[-1]["external_changes_scan_complete"]:
            break
    assert results[0]["external_changes_scan_complete"] is False
    assert results[-1]["external_changes_scan_complete"] is True, (
        "A pending cursor must advance even when polls are faster than the rescan interval"
    )
    assert len({id(cursor) for cursor in cursors}) == 1, "Polling must resume the same cursor"
    assert all(not result["external_changes_dirty"] for result in results)


def test_large_file_reaches_verified_coverage_across_bounded_default_slices(local_scanner):
    scanner, asset = local_scanner
    with asset.open("wb") as target:
        for _ in range(20):
            target.write(b"x" * 1024 * 1024)
    results = [scanner.update_and_get("selected") for _ in range(4)]
    assert results[0]["external_changes_scan_complete"] is False
    assert results[-1]["external_changes_scan_complete"] is True
    assert all(not result["external_changes_dirty"] for result in results)


def test_completed_coverage_survives_a_partial_unchanged_recheck(local_scanner):
    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 32)
    scanner._scan_interval_ms = 0
    assert scanner.update_and_get("selected")["external_changes_scan_complete"]
    scanner._max_scan_bytes = 32
    result = scanner.update_and_get("selected")
    assert scanner._states["selected"].cursor is not None
    assert result["external_changes_scan_complete"] is True
    assert result["external_changes_dirty"] is False
    assert result["external_changes_scan_in_progress"] is True


def test_same_timestamp_edit_is_detected_after_a_resumed_recheck(local_scanner):
    scanner, asset = local_scanner
    scanner._scan_interval_ms = 0
    asset.write_bytes(b"original" * 32)
    stamp = asset.stat().st_mtime_ns
    assert scanner.update_and_get("selected")["external_changes_scan_complete"]
    asset.write_bytes(b"modified" * 32)
    os.utime(asset, ns=(stamp, stamp))
    scanner._max_scan_bytes = 32
    results = [scanner.update_and_get("selected") for _ in range(12)]
    assert any(result["external_changes_dirty"] for result in results)
    assert scanner.capture_dirty_state("selected").revision > 0


def test_new_local_package_invalidates_coverage_until_its_contents_are_verified(local_scanner):
    scanner, asset = local_scanner
    scanner._scan_interval_ms = 0
    asset.write_bytes(b"original" * 32)
    assert scanner.update_and_get("selected")["external_changes_scan_complete"]
    package = asset.parents[1] / "LocalPackage"
    package.mkdir()
    (package / "large.bin").write_bytes(b"package" * 64)
    manifest = asset.parents[1] / "Packages/manifest.json"
    manifest.parent.mkdir()
    manifest.write_text('{"dependencies":{"local":"file:../LocalPackage"}}', encoding="utf-8")
    scanner._max_scan_bytes = 32
    result = scanner.update_and_get("selected")
    assert result["external_changes_scan_complete"] is False
    assert result["external_changes_scan_in_progress"] is True
    results = [scanner.update_and_get("selected") for _ in range(30)]
    assert any(result["external_changes_scan_complete"] for result in results)
    assert any(result["external_changes_dirty"] for result in results)


def test_limited_recheck_revokes_previously_verified_coverage(local_scanner, monkeypatch):
    scanner, asset = local_scanner
    scanner._scan_interval_ms = 0
    asset.write_bytes(b"original" * 32)
    assert scanner.update_and_get("selected")["external_changes_scan_complete"]

    def unavailable(*args):
        raise PermissionError("Fixture cannot be enumerated")

    monkeypatch.setattr(module.os, "scandir", unavailable)
    result = scanner.update_and_get("selected")
    assert result["external_changes_scan_complete"] is False
    assert result["external_changes_scan_limited"] is True
    assert result["external_changes_dirty"] is True


def test_completed_pass_is_committed_when_its_time_slice_expires(local_scanner, monkeypatch):
    scanner, asset = local_scanner
    asset.write_bytes(b"original")
    scanner._scan_interval_ms = 0
    advance = scanner._scan_paths_max_mtime_ns

    def finish_at_deadline(roots):
        result = advance(roots)
        scanner._scan_deadline = time.monotonic() - 1
        return result

    monkeypatch.setattr(scanner, "_scan_paths_max_mtime_ns", finish_at_deadline)
    assert scanner.update_and_get("selected")["external_changes_scan_complete"]
    assert scanner._states["selected"].fingerprint is not None
    asset.write_bytes(b"modified")
    assert scanner.update_and_get("selected")["external_changes_dirty"] is True


@pytest.mark.asyncio
async def test_one_status_read_finishes_initial_coverage_without_more_client_polls(local_scanner):
    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 32)
    scanner._max_scan_bytes = 32
    first = await scanner.update_and_get_async("selected")
    assert first["external_changes_scan_complete"] is False
    state = scanner._states["selected"]
    async with asyncio.timeout(watchdog_timeout(2)):
        while state.cursor is not None:
            await asyncio.sleep(0.001)
    assert state.scan_complete is True
    assert state.fingerprint is not None
    assert not state.dirty


@pytest.mark.asyncio
async def test_concurrent_polls_share_one_runner_and_leave_no_idle_rescan(local_scanner):
    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 128)
    scanner._max_scan_bytes = 32
    await scanner.update_and_get_async("selected")
    runner = scanner._progress_task
    await asyncio.gather(*(scanner.update_and_get_async("selected") for _ in range(20)))
    assert scanner._progress_task is runner
    await asyncio.wait_for(asyncio.shield(runner), watchdog_timeout(2))
    assert scanner._progress_task is None
    future = scanner._worker_future
    await asyncio.sleep(0.025)
    assert scanner._worker_future is future
    assert scanner._states["selected"].cursor is None


@pytest.mark.asyncio
async def test_project_switch_does_not_publish_retired_scan(local_scanner):
    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 128)
    scanner._max_scan_bytes = 32
    await scanner.update_and_get_async("selected")
    old = scanner._states["selected"]
    other = asset.parents[1] / "Other"
    (other / "Assets").mkdir(parents=True)
    (other / "Assets/asset.bin").write_bytes(b"replacement" * 64)
    scanner.set_project_root("selected", str(other))
    result = await scanner.update_and_get_async("selected")
    assert result["external_changes_scan_complete"] is False
    await asyncio.wait_for(asyncio.shield(scanner._progress_task), watchdog_timeout(2))
    assert old.cursor is None
    assert old.fingerprint is None
    replacement = scanner._states["selected"]
    assert replacement is not old
    assert replacement.scan_complete is True
    assert replacement.fingerprint is not None
    assert replacement.dirty is False


@pytest.mark.asyncio
async def test_close_cancels_and_drains_an_active_continuation(local_scanner, monkeypatch):
    from threading import Event

    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 128)
    scanner._max_scan_bytes = 32
    await scanner.update_and_get_async("selected")
    state = scanner._states["selected"]
    entered, release = Event(), Event()
    advance = scanner._scan_paths_max_mtime_ns

    def blocked(roots):
        entered.set()
        assert release.wait(watchdog_timeout(2))
        return advance(roots)

    monkeypatch.setattr(scanner, "_scan_paths_max_mtime_ns", blocked)
    runner = scanner._progress_task
    async with asyncio.timeout(watchdog_timeout(2)):
        while not entered.is_set():
            await asyncio.sleep(0.001)
    scanner.close()
    await asyncio.sleep(0)
    assert not runner.done(), "Continuation ownership must outlive its blocking worker"
    release.set()
    await asyncio.gather(runner, return_exceptions=True)
    assert not scanner._scan_lock.locked()
    assert state.cursor is None
    assert not scanner._states


@pytest.mark.asyncio
async def test_detached_scan_failure_is_visible_and_releases_cursor(
    local_scanner, monkeypatch, caplog
):
    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 128)
    scanner._max_scan_bytes = 32
    await scanner.update_and_get_async("selected")

    def failure(roots):
        raise RuntimeError("fixture continuation failure")

    monkeypatch.setattr(scanner, "_scan_paths_max_mtime_ns", failure)
    await asyncio.wait_for(asyncio.shield(scanner._progress_task), watchdog_timeout(2))
    state = scanner._states["selected"]
    assert state.scan_complete is False
    assert state.scan_limited is True
    assert state.cursor is None
    assert "External-change scan continuation failed" in caplog.text


@pytest.mark.asyncio
async def test_remote_transition_retires_pending_local_coverage(local_scanner, monkeypatch):
    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 128)
    scanner._max_scan_bytes = 32
    await scanner.update_and_get_async("selected")
    state = scanner._states["selected"]
    runner = scanner._progress_task
    monkeypatch.setattr(module.config, "http_remote_hosted", True)
    await asyncio.wait_for(asyncio.shield(runner), watchdog_timeout(2))
    assert state.cursor is None
    assert state.scan_complete is False
    assert (await scanner.update_and_get_async("selected"))[
        "external_changes_scan_complete"
    ] is None


@pytest.mark.asyncio
async def test_continuation_does_not_inherit_request_context(local_scanner, monkeypatch):
    from contextvars import ContextVar

    scanner, asset = local_scanner
    asset.write_bytes(b"original" * 32)
    scanner._max_scan_bytes = 32
    marker = ContextVar("fixture_request_context", default="unset")
    observed = []
    read = scanner.update_and_get_async

    async def record_context(instance):
        observed.append(marker.get())
        return await read(instance)

    monkeypatch.setattr(scanner, "update_and_get_async", record_context)
    token = marker.set("request")
    try:
        await scanner.update_and_get_async("selected")
        await asyncio.wait_for(asyncio.shield(scanner._progress_task), watchdog_timeout(2))
    finally:
        marker.reset(token)
    assert observed[0] == "request"
    assert observed[1:] and set(observed[1:]) == {"unset"}
