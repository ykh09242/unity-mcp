"""Accounting remains readable while optional diagnostic I/O is blocked or failing."""

import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from core import phase_diagnostics as diag


def settled(sink):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = sink.capture_snapshot()
        if snapshot["pending"] == 0:
            return snapshot
        time.sleep(0.005)
    raise AssertionError("Diagnostic samples did not settle")


def finish(sink):
    sink.close()
    if sink.worker is not None:
        sink.worker.join(5)
        assert not sink.worker.is_alive()


def assert_accounted(snapshot):
    assert snapshot["offered"] == sum(
        snapshot[key] for key in ("written", "dropped", "write_failed", "pending")
    )
    assert snapshot["dropped"] == sum(
        snapshot[key] for key in ("drop_queue", "drop_startup", "drop_closed", "drop_shutdown")
    )


def test_queue_saturation_and_shutdown_have_complete_nonblocking_accounting():
    entered, release = threading.Event(), threading.Event()

    def blocked(_):
        entered.set()
        release.wait(10)

    sink = diag.BufferedSink(blocked)
    try:
        sink.offer({"event": "test"})
        assert entered.wait(5)
        for _ in range(1000):
            sink.offer({"event": "test"})
        before = sink.capture_snapshot()
        assert before["offered"] == 1001
        assert before["drop_queue"] == 744
        assert before["pending"] == 257
        assert_accounted(before)
        sink.close()
        assert not sink.capture_snapshot()["closed"]
    finally:
        release.set()
        finish(sink)
    after = sink.capture_snapshot()
    assert after["written"] == 1 and after["drop_shutdown"] == 256
    assert after["pending"] == 0 and after["closed"]
    assert sink.queue.empty()
    assert_accounted(after)


def test_startup_contention_and_closed_offers_are_counted_without_starting_worker():
    sink = diag.BufferedSink(lambda _: None)
    sink.lock.acquire()
    try:
        sink.offer({})
    finally:
        sink.lock.release()
    assert sink.worker is None
    sink.close()
    sink.offer({})
    result = sink.capture_snapshot()
    assert result["drop_startup"] == result["drop_closed"] == 1
    assert result["offered"] == 2 and result["pending"] == 0
    assert_accounted(result)


def test_worker_start_failure_is_counted_and_a_later_offer_can_recover(monkeypatch):
    sink = diag.BufferedSink(lambda _: None)

    def unavailable(_):
        raise RuntimeError("sensitive process error")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", unavailable)
            sink.offer({})
        sink.offer({})
        result = settled(sink)
        assert result["drop_startup"] == 1 and result["written"] == 1
        assert "sensitive" not in str(result)
        assert_accounted(result)
    finally:
        finish(sink)


def test_emit_failure_and_recovery_do_not_replace_command_outcome(monkeypatch):
    def unavailable(_):
        raise OSError("SECRET /private/path")

    sink = diag.BufferedSink(unavailable)
    monkeypatch.setattr(diag.config, "phase_diagnostics", True)
    monkeypatch.setattr(diag, "sample", lambda: True)
    monkeypatch.setattr(diag, "offer", sink.offer)
    try:
        with diag.invocation("secret command"):
            pass
        before = settled(sink)
        assert before["write_failed"] == 1
        sink.emit = lambda _: None
        with diag.invocation("command"):
            pass
        after = settled(sink)
        assert after["written"] == 1 and after["write_failed"] == 1
        assert "SECRET" not in str(after) and "private" not in str(after)
        assert_accounted(after)
    finally:
        finish(sink)


def test_concurrent_offers_do_not_lose_counter_updates():
    sink = diag.BufferedSink(lambda _: None)
    workers = [
        threading.Thread(target=lambda: [sink.offer({}) for _ in range(300)]) for _ in range(4)
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
        result = settled(sink)
        assert result["offered"] == 1200
        assert_accounted(result)
    finally:
        finish(sink)


def test_new_sink_has_a_new_capture_window_and_detached_snapshot():
    first, second = diag.BufferedSink(lambda _: None), diag.BufferedSink(lambda _: None)
    try:
        first.offer({})
        result = settled(first)
        result["written"] = 100
        assert first.capture_snapshot()["written"] == 1
        other = second.capture_snapshot()
        assert other["offered"] == 0
        assert other["session"] != first.capture_snapshot()["session"]
    finally:
        finish(first)
        finish(second)


def test_file_checkpoints_bound_actual_report_coverage(tmp_path, monkeypatch):
    path = tmp_path / f"phase-timing-1234-{uuid.uuid4().hex}.log"
    monkeypatch.setattr(diag, "_log_path", path)
    sink = diag.BufferedSink()
    try:
        for index in range(2):
            sink.offer(
                {
                    "event": "server_phase_timing",
                    "correlation": f"{index:032x}",
                    "total_ms": 1,
                    "outcome": "returned",
                    "native_requested": False,
                }
            )
        settled(sink)
    finally:
        finish(sink)
    rows = [json.loads(line.split(" ", 1)[1]) for line in path.read_text().splitlines()]
    captures = [row for row in rows if row["event"] == "phase_capture_coverage"]
    samples = [row for row in rows if row["event"] == "server_phase_timing"]
    assert captures and captures[-1]["closed"]
    assert captures[-1]["offered"] == captures[-1]["written"] == 2
    assert captures[-1]["pending"] == 0
    assert [row["capture_sequence"] for row in samples] == [1, 2]
    assert {row["capture_session"] for row in samples} == {captures[-1]["session"]}
    assert captures[-1]["retention"]["scan_complete"]
    report = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "tools/phase_timing_report.py"),
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    coverage = json.loads(report.stdout)["capture_coverage"]
    assert coverage["complete"] and coverage["offered"] == coverage["written"] == 2
    assert coverage["capture_fraction"] == coverage["selected_log_fraction"] == 1


def test_checkpoint_write_failure_is_visible_after_disk_recovery(tmp_path, monkeypatch):
    from core.phase_log_files import PhaseLogFile

    path = tmp_path / f"phase-timing-1234-{uuid.uuid4().hex}.log"
    monkeypatch.setattr(diag, "_log_path", path)
    original = PhaseLogFile.write
    failed = threading.Event()

    def fail_checkpoint(target, encoded):
        if b'"phase_capture_coverage"' in encoded and not failed.is_set():
            failed.set()
            raise OSError("sensitive disk failure")
        return original(target, encoded)

    monkeypatch.setattr(PhaseLogFile, "write", fail_checkpoint)
    sink = diag.BufferedSink()
    try:
        sink.offer({"event": "server_phase_timing"})
        assert failed.wait(5)
    finally:
        finish(sink)
    rows = [json.loads(line.split(" ", 1)[1]) for line in path.read_text().splitlines()]
    final = [row for row in rows if row["event"] == "phase_capture_coverage"][-1]
    assert final["checkpoint_write_failed"] == 1
    assert final["written"] == 1 and final["write_failed"] == 0
    assert "sensitive" not in str(final)


def test_close_during_worker_start_cannot_enqueue_after_worker_shutdown(monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = threading.Thread.start

    def hold_start(worker):
        original(worker)
        if worker.name == "unity-phase-diagnostics":
            started.set()
            release.wait(10)

    monkeypatch.setattr(threading.Thread, "start", hold_start)
    sink = diag.BufferedSink(lambda _: None)
    caller = threading.Thread(target=lambda: sink.offer({}))
    try:
        caller.start()
        assert started.wait(5)
        sink.close()
    finally:
        release.set()
        caller.join(5)
        finish(sink)
    assert not caller.is_alive()
    result = sink.capture_snapshot()
    assert result["offered"] == result["drop_closed"] == 1
    assert result["pending"] == result["written"] == 0
    assert sink.queue.empty()
    assert_accounted(result)


def test_write_attempt_sequence_excludes_drops_but_includes_failed_writes(tmp_path, monkeypatch):
    from core.phase_log_files import PhaseLogFile

    path = tmp_path / f"phase-timing-1234-{uuid.uuid4().hex}.log"
    monkeypatch.setattr(diag, "_log_path", path)
    original = PhaseLogFile.write
    failed = threading.Event()

    def fail_first_sample(target, encoded):
        if b'"server_phase_timing"' in encoded and not failed.is_set():
            failed.set()
            raise OSError("disk unavailable")
        return original(target, encoded)

    monkeypatch.setattr(PhaseLogFile, "write", fail_first_sample)
    sink = diag.BufferedSink()
    try:
        sink.lock.acquire()
        try:
            sink.offer({"event": "server_phase_timing"})
        finally:
            sink.lock.release()
        sink.offer({"event": "server_phase_timing"})
        assert failed.wait(5)
        settled(sink)
        sink.offer({"event": "server_phase_timing"})
        settled(sink)
    finally:
        finish(sink)
    rows = [json.loads(line.split(" ", 1)[1]) for line in path.read_text().splitlines()]
    samples = [row for row in rows if row["event"] == "server_phase_timing"]
    final = [row for row in rows if row["event"] == "phase_capture_coverage"][-1]
    assert [row["capture_sequence"] for row in samples] == [2]
    assert final["offered"] == 3
    assert final["drop_startup"] == final["write_failed"] == final["written"] == 1
    assert final["pending"] == 0 and final["closed"]
