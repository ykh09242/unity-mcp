"""Overlapping local package roots must not consume the scan budget twice."""

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import threading
import time

import pytest

from services.state import external_changes_scanner as module


@pytest.fixture
def active_scanner(monkeypatch):
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    monkeypatch.setattr(module, "_in_pytest", lambda: False)


def write_at(path, timestamp=1_000_000_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("//fixture", encoding="utf-8")
    os.utime(path, ns=(timestamp, timestamp))


def project(tmp_path, file_count=1):
    root = tmp_path / "Project"
    package = root / "Packages/Local"
    for index in range(file_count):
        write_at(package / f"{index}.cs")
    asset = root / "Assets/changed.cs"
    write_at(asset)
    manifest = root / "Packages/manifest.json"
    manifest.write_text(json.dumps({"dependencies": {"local": "file:Local"}}), encoding="utf-8")
    os.utime(manifest, ns=(1_000_000_000, 1_000_000_000))
    return root, package, asset


def count_scans(monkeypatch):
    counts = Counter()
    scan = module.os.scandir

    def counted(path):
        counts[Path(path)] += 1
        return scan(path)

    monkeypatch.setattr(module.os, "scandir", counted)
    return counts


@pytest.mark.parametrize("order", ["parent-first", "child-first", "repeated"])
def test_overlapping_roots_visit_each_directory_once(tmp_path, monkeypatch, active_scanner, order):
    parent = tmp_path / "Parent"
    child = parent / "Child"
    write_at(child / "new.cs", 2_000_000_000)
    roots = [parent, child] if order == "parent-first" else [child, parent]
    if order == "repeated":
        roots.extend([child, parent])
    counts = count_scans(monkeypatch)
    scanner = module.ExternalChangesScanner()
    assert scanner._scan_paths_max_mtime_ns(roots) == 2_000_000_000
    assert counts == {parent: 1, child: 1}


@pytest.mark.parametrize("max_entries", [10_000, 1500])
def test_local_package_overlap_does_not_hide_asset_change(
    tmp_path, monkeypatch, active_scanner, max_entries
):
    root, package, asset = project(tmp_path, file_count=1000)
    counts = count_scans(monkeypatch)
    scanner = module.ExternalChangesScanner(
        scan_interval_ms=0, max_entries=max_entries, max_scan_seconds=10
    )
    scanner.set_project_root("Selected@one", str(root))
    assert not scanner.update_and_get("Selected@one")["external_changes_dirty"]
    counts.clear()
    os.utime(asset, ns=(2_000_000_000, 2_000_000_000))
    observed = scanner.update_and_get("Selected@one")
    assert observed["external_changes_dirty"], (
        "Repeated package traversal exhausted the asset budget"
    )
    assert scanner._states["Selected@one"].last_seen_mtime_ns == 2_000_000_000
    assert counts[package] == 1
    assert counts[root / "Assets"] == 1


def test_visited_directories_are_request_local_and_manifest_cache_stays_fresh(
    tmp_path, monkeypatch, active_scanner
):
    root, package, _ = project(tmp_path)
    counts = count_scans(monkeypatch)
    reads = []
    open_file = Path.open

    def counted_open(path, *args, **kwargs):
        if path == root / "Packages/manifest.json":
            reads.append(path)
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted_open)
    scanner = module.ExternalChangesScanner(scan_interval_ms=0)
    scanner.set_project_root("one", str(root))
    assert not scanner.update_and_get("one")["external_changes_dirty"]
    write_at(package / "late.cs", 3_000_000_000)
    assert scanner.update_and_get("one")["external_changes_dirty"]
    assert scanner._states["one"].last_seen_mtime_ns == 3_000_000_000
    assert counts[package] == 2, "The next scan must traverse the package again"
    assert len(reads) == 1, "An unchanged manifest must retain its parsing cache"


def test_explicit_hidden_root_is_not_removed_by_ancestor_filter(
    tmp_path, monkeypatch, active_scanner
):
    hidden = tmp_path / ".Local"
    write_at(hidden / "file.cs", 3_000_000_000)
    counts = count_scans(monkeypatch)
    scanner = module.ExternalChangesScanner()
    assert scanner._scan_paths_max_mtime_ns([hidden, tmp_path]) == 3_000_000_000
    assert counts == {tmp_path: 1, hidden: 1}


def test_failed_directory_open_can_be_retried_in_the_same_scan(
    tmp_path, monkeypatch, active_scanner
):
    write_at(tmp_path / "file.cs", 2_000_000_000)
    scan = module.os.scandir
    attempts = []

    def open_directory(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise OSError("temporary fixture failure")
        return scan(path)

    monkeypatch.setattr(module.os, "scandir", open_directory)
    scanner = module.ExternalChangesScanner()
    assert scanner._scan_paths_max_mtime_ns([tmp_path, tmp_path]) == 2_000_000_000
    assert len(attempts) == 2


@pytest.mark.parametrize("stop", ["entries", "deadline", "cancel", "hosted"])
def test_overlap_does_not_bypass_existing_scan_stops(tmp_path, monkeypatch, active_scanner, stop):
    write_at(tmp_path / "file.cs")
    counts = count_scans(monkeypatch)
    scanner = module.ExternalChangesScanner(max_entries=0 if stop == "entries" else 20_000)
    if stop == "deadline":
        scanner._scan_deadline = time.monotonic() - 1
    elif stop == "cancel":
        scanner._active_stop = threading.Event()
        scanner._active_stop.set()
    elif stop == "hosted":
        monkeypatch.setattr(module.config, "http_remote_hosted", True)
    assert scanner._scan_paths_max_mtime_ns([tmp_path, tmp_path]) is None
    assert not counts


@pytest.mark.asyncio
async def test_cancelled_overlap_worker_releases_lock_and_next_scan_restarts(
    tmp_path, monkeypatch, active_scanner
):
    root, _, asset = project(tmp_path)
    scanner = module.ExternalChangesScanner(scan_interval_ms=0)
    scanner.set_project_root("one", str(root))
    entered, release = threading.Event(), threading.Event()
    real_scan = scanner._scan_paths_max_mtime_ns

    def blocked(roots):
        entered.set()
        assert release.wait(5)
        return real_scan(roots)

    monkeypatch.setattr(scanner, "_scan_paths_max_mtime_ns", blocked)
    caller = asyncio.create_task(scanner.update_and_get_async("one"))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert scanner._active_stop.is_set()
    finally:
        release.set()
        async with asyncio.timeout(5):
            while scanner._scan_lock.locked():
                await asyncio.sleep(0.001)
    monkeypatch.setattr(scanner, "_scan_paths_max_mtime_ns", real_scan)
    assert scanner._states["one"].last_seen_mtime_ns is None
    assert not (await scanner.update_and_get_async("one"))["external_changes_dirty"]
    os.utime(asset, ns=(2_000_000_000, 2_000_000_000))
    assert (await scanner.update_and_get_async("one"))["external_changes_dirty"]
