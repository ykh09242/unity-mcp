"""Exercise real file leases and positively owned diagnostic retention."""

from tests.timing import watchdog_timeout

import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest


def writer(directory, pid=1234):
    from core.phase_log_files import PhaseLogFile

    return PhaseLogFile(directory / f"phase-timing-{pid}-{uuid.uuid4().hex}.log", 128)


def age(files, seconds=8 * 86400):
    stamp = time.time() - seconds
    for path in files:
        os.utime(path, (stamp, stamp))


def sweep(directory, limit=128):
    from core.phase_log_files import LogRetention

    maintenance = LogRetention(directory)
    try:
        for _ in range(100):
            result = maintenance.step(max_entries=limit)
            if result["scan_complete"]:
                return result
        pytest.fail("Retention scan did not finish")
    finally:
        maintenance.close()


def test_old_inactive_owned_sessions_retire_and_recent_or_foreign_files_survive(tmp_path):
    old = writer(tmp_path)
    for _ in range(4):
        old.write(b"x" * 80 + b"\n")
    old.close()
    old_files = [old.owner_path, *old.paths]
    age(old_files)
    recent = writer(tmp_path)
    recent.write(b"recent\n")
    recent.close()
    foreign = [
        tmp_path / name
        for name in (
            "phase-timing-1111.log",
            "phase-timing-1111.log.1",
            "export.log",
            old.path.name + ".export",
            "unity_mcp_server.log",
        )
    ]
    for path in foreign:
        path.write_bytes(b"foreign")
    age(foreign)
    result = sweep(tmp_path)
    assert all(not path.exists() for path in old_files)
    assert recent.path.read_bytes() == b"recent\n"
    assert all(path.read_bytes() == b"foreign" for path in foreign)
    assert result["deleted_sessions"] == 1
    assert result["retained_bytes"] >= len(b"recent\n")
    assert result["oldest_retained_age_seconds"] < 60


def test_live_lease_protects_old_logs_and_pid_reuse_is_a_different_session(tmp_path):
    first, second = writer(tmp_path), writer(tmp_path)
    try:
        first.write(b"live\n")
        second.write(b"new session\n")
        age([first.owner_path, first.path])
        result = sweep(tmp_path)
        assert first.path.read_bytes() == b"live\n"
        assert result["active_sessions"] == 2
        assert first.path != second.path
    finally:
        first.close()
        second.close()
    age([first.owner_path, first.path])
    assert sweep(tmp_path)["deleted_sessions"] == 1
    assert second.path.exists()


def test_actual_child_process_lease_survives_maintenance_then_retires_after_crash(tmp_path):
    path = tmp_path / f"phase-timing-1234-{uuid.uuid4().hex}.log"
    code = """
import sys
from pathlib import Path
from core.phase_log_files import PhaseLogFile
writer = PhaseLogFile(Path(sys.argv[1]), 128)
writer.write(b'child\\n')
Path(sys.argv[2]).write_text('ready')
sys.stdin.read()
"""
    ready = tmp_path / "child-ready"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(path), str(ready)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + watchdog_timeout()
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Child did not acquire its diagnostic lease"
        age([path, path.with_suffix(".owner")])
        assert sweep(tmp_path)["active_sessions"] == 1
        assert path.exists()
    finally:
        process.kill()
        process.communicate(timeout=watchdog_timeout())
    assert sweep(tmp_path)["deleted_sessions"] == 1
    assert not path.exists()


def test_recent_rotated_file_preserves_whole_inactive_session(tmp_path):
    target = writer(tmp_path)
    target.write(b"x" * 80)
    target.write(b"y" * 80)
    target.close()
    age([target.owner_path, target.path])
    assert sweep(tmp_path)["deleted_sessions"] == 0
    assert target.path.with_suffix(".log.1").exists()


def test_malformed_owner_and_hard_link_are_never_retired(tmp_path):
    invalid = writer(tmp_path)
    invalid.write(b"foreign")
    invalid.close()
    invalid.owner_path.write_bytes(b"not an owned log")
    linked = writer(tmp_path)
    linked.write(b"linked")
    linked.close()
    export = tmp_path / "linked-export.log"
    os.link(linked.path, export)
    age([invalid.owner_path, invalid.path, linked.owner_path, linked.path])
    assert sweep(tmp_path)["deleted_sessions"] == 0
    assert invalid.path.read_bytes() == b"foreign"
    assert linked.path.read_bytes() == export.read_bytes() == b"linked"


def test_link_in_rotated_slot_preserves_target_and_owned_session(tmp_path):
    target = writer(tmp_path)
    target.write(b"owned")
    target.close()
    foreign = tmp_path / "foreign.txt"
    foreign.write_bytes(b"preserved")
    try:
        target.path.with_suffix(".log.1").symlink_to(foreign)
    except OSError:
        pytest.skip("Creating symlinks is not permitted by this OS account")
    age([target.owner_path, target.path])
    assert sweep(tmp_path)["deleted_sessions"] == 0
    assert foreign.read_bytes() == b"preserved"
    assert target.path.exists()


def test_cleanup_failure_preserves_owner_for_retry(tmp_path, monkeypatch):
    target = writer(tmp_path)
    target.write(b"owned")
    target.close()
    age([target.owner_path, target.path])
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if path == target.path:
            raise PermissionError("busy")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        result = sweep(tmp_path)
        assert result["cleanup_errors"] == 1
        assert target.owner_path.exists()
    assert sweep(tmp_path)["deleted_sessions"] == 1


def test_scan_budget_progresses_past_foreign_entries_and_releases_iterator(tmp_path):
    from core.phase_log_files import LogRetention

    for index in range(15):
        (tmp_path / f"foreign-{index}").write_bytes(b"foreign")
    target = writer(tmp_path)
    target.write(b"old")
    target.close()
    age([target.owner_path, target.path])
    maintenance = LogRetention(tmp_path)
    try:
        first = maintenance.step(max_entries=2)
        assert first["entries_scanned"] == 2 and not first["scan_complete"]
        for _ in range(20):
            result = maintenance.step(max_entries=2)
            if result["scan_complete"]:
                break
        assert result["scan_complete"]
        assert result["deleted_sessions"] == 1
        assert len(list(tmp_path.glob("foreign-*"))) == 15
    finally:
        maintenance.close()
    assert maintenance.scan is None


def test_writer_refuses_link_rotation_and_closes_lease(tmp_path):
    target = writer(tmp_path)
    target.write(b"x" * 80)
    export = tmp_path / "export"
    os.link(target.path, export)
    try:
        with pytest.raises(OSError):
            target.write(b"y" * 80)
        assert export.read_bytes() == b"x" * 80
    finally:
        target.close()


def test_owner_replacement_does_not_authorize_deletion(tmp_path):
    target = writer(tmp_path)
    target.write(b"preserved")
    target.close()
    age([target.owner_path, target.path])
    other = writer(tmp_path)
    other.write(b"other")
    other.close()
    target.owner_path.write_bytes(other.owner_path.read_bytes())
    assert sweep(tmp_path)["deleted_sessions"] == 0
    assert target.path.read_bytes() == b"preserved"


def test_bounded_shutdown_does_not_release_a_blocked_writers_lease(tmp_path, monkeypatch):
    import threading
    from core import phase_diagnostics as diag

    path = tmp_path / f"phase-timing-1234-{uuid.uuid4().hex}.log"
    monkeypatch.setattr(diag, "_log_path", path)
    entered, release = threading.Event(), threading.Event()
    original = Path.open

    def blocked(target, *args, **kwargs):
        if target == path:
            entered.set()
            release.wait(watchdog_timeout())
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", blocked)
    sink = diag.BufferedSink()
    try:
        sink.offer({"event": "test"})
        assert entered.wait(watchdog_timeout())
        sink.close()
        assert sink.worker.is_alive()
        age([path.with_suffix(".owner")])
        assert sweep(tmp_path)["active_sessions"] == 1
        assert path.with_suffix(".owner").exists()
    finally:
        release.set()
        sink.worker.join(watchdog_timeout())
        sink.close()
    assert not sink.worker.is_alive()
    age([path, path.with_suffix(".owner")])
    assert sweep(tmp_path)["deleted_sessions"] == 1


def test_replaced_directory_is_not_followed_by_writer_or_maintenance(tmp_path):
    from core.phase_log_files import LogRetention

    directory = tmp_path / "logs"
    directory.mkdir()
    target = writer(directory)
    target.write(b"owned")
    target.close()
    maintenance = LogRetention(directory)
    moved = tmp_path / "moved"
    directory.rename(moved)
    directory.mkdir()
    foreign = directory / target.path.name
    foreign.write_bytes(b"foreign")
    try:
        with pytest.raises(OSError):
            maintenance.step()
        with pytest.raises(OSError):
            target.write(b"new")
        assert foreign.read_bytes() == b"foreign"
    finally:
        maintenance.close()
        target.close()


def test_failed_initial_owner_write_releases_lease_and_allows_retry(tmp_path, monkeypatch):
    from core import phase_log_files as files

    target = writer(tmp_path)
    original = files._lock
    try:
        with monkeypatch.context() as patch:

            def fail(_):
                raise OSError("lock unavailable")

            patch.setattr(files, "_lock", fail)
            with pytest.raises(OSError):
                target.write(b"first")
            assert not target.owner_path.exists()
        assert files._lock is original
        target.write(b"second")
        assert target.path.read_bytes() == b"second"
    finally:
        target.close()


def test_graceful_close_between_stat_and_lease_keeps_full_retention_window(tmp_path, monkeypatch):
    from core import phase_log_files as files

    target = writer(tmp_path)
    target.write(b"retain after close")
    age([target.owner_path, target.path])
    original = files._lock
    closed = []

    def close_before_acquire(stream):
        if Path(stream.name) == target.owner_path and target.owner is not None:
            target.close()
            closed.append(True)
        return original(stream)

    try:
        monkeypatch.setattr(files, "_lock", close_before_acquire)
        result = sweep(tmp_path)
        assert closed == [True]
        assert result["deleted_sessions"] == 0
        assert target.path.read_bytes() == b"retain after close"
        assert result["oldest_retained_age_seconds"] < 60
    finally:
        target.close()
