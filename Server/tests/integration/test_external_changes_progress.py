"""Budgeted scans eventually cover all files and detect identity/content changes."""

from tests.timing import watchdog_timeout

import os
import pytest
from services.state import external_changes_scanner as module


@pytest.fixture
def scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    scanner = module.ExternalChangesScanner(scan_interval_ms=0, max_entries=5, max_scan_seconds=10)
    scanner.set_project_root("owned", str(tmp_path))
    yield scanner
    scanner.close()


def write(path, content="old", stamp=1000000000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))


def advance(scanner, count=60):
    results = [scanner.update_and_get("owned") for _ in range(count)]
    return any(result["external_changes_dirty"] for result in results)


def test_budget_exhaustion_does_not_restart_at_the_same_directory(scanner, tmp_path):
    for directory in ("Assets", "ProjectSettings", "Packages"):
        for index in range(20):
            write(tmp_path / directory / f"{index}.txt")
    assert not advance(scanner)
    write(tmp_path / "Assets/19.txt", "changed", 2000000000)
    assert advance(scanner), "A finite tree must be covered across bounded calls"


@pytest.mark.parametrize(
    "operation", ["delete", "rename", "same-stamp-content", "older-than-future"]
)
def test_path_and_content_changes_do_not_depend_on_global_max_mtime(scanner, tmp_path, operation):
    target = tmp_path / "Assets/selected.txt"
    write(target)
    if operation == "older-than-future":
        write(tmp_path / "Assets/future.txt", stamp=9000000000)
    assert not advance(scanner)
    if operation == "delete":
        target.unlink()
    elif operation == "rename":
        target.rename(target.with_name("renamed.txt"))
    else:
        write(target, "new", 2000000000 if operation == "older-than-future" else 1000000000)
    assert advance(scanner)


def test_clear_cannot_acknowledge_a_newer_same_timestamp_change(scanner, tmp_path):
    target = tmp_path / "Assets/selected.txt"
    write(target)
    advance(scanner)
    write(target, "one", 2000000000)
    advance(scanner)
    snapshot = scanner.capture_dirty_state("owned")
    write(target, "two", 2000000000)
    advance(scanner)
    assert not scanner.clear_dirty("owned", expected=snapshot)
    assert scanner.update_and_get("owned")["external_changes_dirty"]


def test_partial_scan_exposes_incomplete_coverage_and_close_releases_handles(scanner, tmp_path):
    write(tmp_path / "Assets/large.bin", "x" * 262144)
    scanner._max_scan_bytes = 65536
    result = scanner.update_and_get("owned")
    assert result["external_changes_scan_complete"] is False
    cursor = scanner._states["owned"].cursor
    assert cursor.bytes_read <= 65536
    scanner.close()
    assert cursor.iterator.gi_frame is None


def test_finite_large_file_completes_across_byte_budgets(scanner, tmp_path):
    write(tmp_path / "Assets/large.bin", "x" * 262144)
    scanner._max_scan_bytes = 65536
    results = [scanner.update_and_get("owned") for _ in range(10)]
    assert any(result["external_changes_scan_complete"] for result in results)
    assert all(not result["external_changes_dirty"] for result in results)


def test_same_timestamp_manifest_edit_retargets_local_dependency(scanner, tmp_path):
    import json

    write(tmp_path / "LocalA/file.cs")
    write(tmp_path / "LocalB/file.cs")
    manifest = tmp_path / "Packages/manifest.json"
    write(manifest, json.dumps({"dependencies": {"local": "file:../LocalA"}}))
    advance(scanner)
    write(manifest, json.dumps({"dependencies": {"local": "file:../LocalB"}}))
    assert advance(scanner)
    scanner.clear_dirty("owned")
    write(tmp_path / "LocalB/file.cs", "new", 3000000000)
    assert advance(scanner)


def test_project_switch_during_manifest_read_releases_retired_cursor(
    scanner, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    write(tmp_path / "Assets/large.bin", "x" * 262144)
    old = scanner._states["owned"]
    entered, release = Event(), Event()

    def manifest(*args):
        entered.set()
        assert release.wait(watchdog_timeout())
        return []

    monkeypatch.setattr(scanner, "_resolve_manifest_extra_roots", manifest)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(scanner.update_and_get, "owned")
        assert entered.wait(watchdog_timeout())
        scanner.set_project_root("owned", str(tmp_path / "Other"))
        release.set()
        future.result(timeout=watchdog_timeout())
    assert old.cursor is None or old.cursor.iterator.gi_frame is None
