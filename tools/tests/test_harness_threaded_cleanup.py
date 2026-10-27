"""Real-thread cleanup handoff with an admitted launch and blocked recovery writer."""

import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_harness as lh


@pytest.mark.parametrize("mode", ["owned", "unknown", "past-budget", "unknown-past-budget"])
def test_real_watchdog_hands_off_latest_generation_with_one_cleanup_deadline(
    tmp_path, monkeypatch, mode
):
    # Given: actual watchdog threads hold the first in-flight recovery write.
    receipt_entered = threading.Event()
    release_receipt = threading.Event()
    main_finalizing = threading.Event()
    completed = threading.Event()
    hard_exit = threading.Event()
    exits, threads, errors = [], [], []
    state = {"alive": False, "stops": 0}
    start = time.monotonic()
    real_write = Path.write_text
    real_reports = lh.write_reports

    def write_text(path, data, *args, **kwargs):
        if path.name == "reuse.json" and not receipt_entered.is_set():
            receipt_entered.set()
            if not release_receipt.wait(6):
                raise TimeoutError("test did not release its owned receipt writer")
        return real_write(path, data, *args, **kwargs)

    def spawn(*args, **kwargs):
        thread = threading.Thread(*args, **kwargs)
        threads.append(thread)
        return thread

    def timer(*args, **kwargs):
        thread = threading.Timer(*args, **kwargs)
        threads.append(thread)
        return thread

    def intercepted_exit(code):
        exits.append((code, time.monotonic() - start))
        hard_exit.set()

    def reports(*args):
        real_reports(*args)
        if any(outcome.name == "cleanup" for outcome in args[2]):
            main_finalizing.set()

    def launch(_editor, _project, status, *_):
        state["status"] = status
        if not receipt_entered.wait(4):
            raise TimeoutError("watchdog did not enter the owned receipt writer")
        state["alive"] = True
        if mode in ("unknown", "unknown-past-budget"):
            launcher.pending_handle = lh.Handle(container="late-unconfirmed-owned-request")
            raise subprocess.TimeoutExpired("owned fake daemon", 1)
        return lh.Handle(pid=12345)

    def stop(_):
        state["stops"] += 1
        state["alive"] = False

    launcher = SimpleNamespace(
        resolve_editor=lambda *_: lh.EditorSpec("owned-fake", "6000"),
        launch=launch,
        teardown=stop,
        is_alive=lambda _: state["alive"],
        tail_log=lambda *_: "",
    )
    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(lh, "write_reports", reports)
    monkeypatch.setattr(
        lh,
        "threading",
        SimpleNamespace(
            Thread=spawn,
            Timer=timer,
            Event=threading.Event,
            Lock=threading.Lock,
        ),
    )
    monkeypatch.setattr(lh.os, "_exit", intercepted_exit)
    monkeypatch.setattr(lh.signal, "signal", lambda *_: None)
    monkeypatch.setattr(lh, "make_launcher", lambda _: launcher)
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", "original-test-status")
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", "original-test-instance")
    report_dir = tmp_path / "reports"
    argv = [
        "--project-path",
        str(tmp_path / "project"),
        "--status-dir",
        str(tmp_path / "status"),
        "--reports",
        str(report_dir),
        "--junit",
        str(report_dir / "junit.xml"),
        "--legs",
        "smoke",
        "--no-warmup",
        "--overall-timeout",
        "1",
        "--cleanup-timeout",
        "2",
    ]

    def run_main():
        try:
            state["result"] = lh.main(argv)
        except BaseException as error:
            errors.append(error)
        finally:
            state["returned_at"] = time.monotonic() - start
            completed.set()

    main_thread = threading.Thread(target=run_main, name="owned-harness-main")
    threads.append(main_thread)
    main_thread.start()
    try:
        assert receipt_entered.wait(4) and main_finalizing.wait(1)
        # When: main publishes a late resource while another cleanup owns the lock.
        if mode in ("past-budget", "unknown-past-budget"):
            assert hard_exit.wait(3)
            assert exits[0][0] == 2 and 2.7 <= exits[0][1] < 4.0
            assert completed.wait(0.3), "main must use the existing absolute cleanup deadline"
            # A still-blocked old writer must not be the sole recovery identity.
            late_receipt = json.loads((report_dir / "reuse.json").read_text())
            assert late_receipt["cleanup_confirmed"] is False
            publications = list(report_dir.glob("reuse-unity-mcp-harness-*.json"))
            assert len(publications) == 1
            published = json.loads(publications[0].read_text())
            if mode == "unknown-past-budget":
                assert (
                    late_receipt["container"]
                    == published["container"]
                    == "late-unconfirmed-owned-request"
                )
                assert late_receipt["ownership_confirmed"] is False
            else:
                assert late_receipt["pid"] == published["pid"] == 12345
        else:
            assert not completed.wait(0.05), "lock contention must not complete cleanup"
            assert not hard_exit.is_set(), "cleanup completion must not trigger an early hard exit"
        release_receipt.set()
        assert completed.wait(3)
    finally:
        release_receipt.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(4)
    # Then: released ownership observes the latest generation before declaring completion.
    assert not errors and state["result"] == 2
    assert not any(thread.is_alive() for thread in threads)
    if mode == "owned":
        assert state["stops"] == 1 and not state["alive"]
        assert not state["status"].exists()
    else:
        assert state["status"].is_dir()
        receipt = json.loads((report_dir / "reuse.json").read_text())
        assert receipt["acquisition_in_flight"] is False
        if mode in ("unknown", "unknown-past-budget"):
            assert receipt["container"] == "late-unconfirmed-owned-request"
            assert receipt["ownership_confirmed"] is False and state["stops"] == 0
        else:
            assert receipt["pid"] == 12345
            assert state["stops"] == 0 and state["alive"]
