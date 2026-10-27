"""Actual local warmup/teardown cooperate with the real watchdog while acquiring."""

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


@pytest.mark.parametrize(
    "phase,held_wait", [("warmup", False), ("warmup", True), ("resident", False)]
)
def test_watchdog_stops_published_owned_editor_before_acquisition_finishes(
    tmp_path, monkeypatch, phase, held_wait
):
    # Given: actual LocalLauncher.warmup publishes its Popen before its boot wait.
    entered, stopped, release_wait, main_done, hard_exit = (threading.Event() for _ in range(5))
    threads, exits, errors = [], [], []
    state = {}
    start = time.monotonic()

    class OwnedProcess:
        pid = 12345
        returncode = None
        terminate_calls = 0
        kill_calls = 0

        def wait(self, timeout=None):
            assert timeout is not None and timeout > 0
            if threading.current_thread().name == "owned-warmup-main":
                entered.set()
                if not stopped.wait(timeout):
                    raise subprocess.TimeoutExpired("owned-warmup", timeout)
                if held_wait and not release_wait.wait(4):
                    raise TimeoutError("test did not release the owned warmup wait")
            return self.returncode

        def terminate(self):
            self.terminate_calls += 1
            self.returncode = -15
            stopped.set()

        def kill(self):
            self.kill_calls += 1
            self.returncode = -9
            stopped.set()

        def poll(self):
            return self.returncode

    child = OwnedProcess()

    class OwnedLauncher(lh.LocalLauncher):
        def resolve_editor(self, project_path):
            return lh.EditorSpec("owned-fake", "6000")

        def launch(self, *args):
            if phase == "warmup":
                pytest.fail("expired warmup must not launch a resident Editor")
            return super().launch(*args)

    def make_launcher(args):
        state["args"] = args
        return OwnedLauncher(args)

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

    monkeypatch.setattr(lh.subprocess, "Popen", lambda *_a, **_kw: child)
    monkeypatch.setattr(lh, "make_launcher", make_launcher)
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
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", "original-test-status")
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", "original-test-instance")
    reports = tmp_path / "reports"
    argv = [
        "--project-path",
        str(tmp_path / "project"),
        "--status-dir",
        str(tmp_path / "status"),
        "--reports",
        str(reports),
        "--junit",
        str(reports / "junit.xml"),
        "--legs",
        "smoke",
        "--overall-timeout",
        "1",
        "--cleanup-timeout",
        "0.3",
        "--boot-timeout",
        "10",
    ]

    if phase == "resident":
        argv.append("--no-warmup")

    def publication_boundary(frame, event, _arg):
        # Pause only this owned main thread after native launch returned its
        # exact handle, before the acquisition context has exited. No line
        # number or source spelling is pinned by the scheduling barrier.
        if (
            event == "line"
            and frame.f_code.co_name == "_main"
            and frame.f_locals.get("handle") is not None
            and frame.f_locals.get("acquisitions") == 1
            and not entered.is_set()
        ):
            entered.set()
            if not stopped.wait(3):
                raise TimeoutError("watchdog did not stop the published owned resident")
        return publication_boundary

    def run_main():
        try:
            if phase == "resident":
                sys.settrace(publication_boundary)
            state["result"] = lh.main(argv)
        except BaseException as error:
            errors.append(error)
        finally:
            sys.settrace(None)
            main_done.set()

    main_thread = threading.Thread(target=run_main, name="owned-warmup-main")
    threads.append(main_thread)
    main_thread.start()
    try:
        assert entered.wait(1)
        # When: execution expires while the boot wait remains in an acquisition.
        assert hard_exit.wait(2)
        # Then: exact owned warmup termination is attempted within the cleanup budget.
        assert child.poll() is not None and child.terminate_calls == 1
        assert child.kill_calls == 0
        assert exits[0][0] == 2 and 0.9 <= exits[0][1] < 1.8
        if held_wait:
            assert not main_done.is_set()
            receipt = json.loads((reports / "reuse.json").read_text())
            assert receipt["pid"] == child.pid and receipt["ownership_confirmed"] is True
            assert receipt["acquisition_in_flight"] is True
            assert Path(receipt["status_dir"]).is_dir()
        release_wait.set()
        assert main_done.wait(1)
    finally:
        # Release only the owned fake, including the failing-baseline path.
        release_wait.set()
        stopped.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(2)
    assert not errors and state["result"] == 2
    assert not any(thread.is_alive() for thread in threads)
