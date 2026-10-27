"""Deterministic main-flow coverage for resource acquisition and cleanup races."""

import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_harness as lh


class ControlledRuntime:
    """Run actual watchdog closures at explicit virtual-time boundaries."""

    def __init__(self):
        self.now = 100.0
        self.watchdog = None
        self.exits = []

    def advance(self, seconds):
        self.now += seconds
        if self.now >= 101.0 and self.watchdog is not None:
            target, self.watchdog = self.watchdog, None
            target()

    def thread(self, *, target, name, daemon):
        def start():
            if name == "harness-watchdog":
                self.watchdog = target
            else:
                target()

        return SimpleNamespace(start=start)

    @staticmethod
    def timer(_timeout, _target):
        return SimpleNamespace(start=lambda: None, cancel=lambda: None, daemon=False)

    @staticmethod
    def event():
        state = threading.Event()
        return SimpleNamespace(set=state.set, is_set=state.is_set, wait=lambda *_: state.is_set())


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    runtime = ControlledRuntime()
    monkeypatch.setattr(
        lh, "time", SimpleNamespace(monotonic=lambda: runtime.now, sleep=lambda _: None)
    )
    monkeypatch.setattr(
        lh,
        "threading",
        SimpleNamespace(
            Thread=runtime.thread,
            Timer=runtime.timer,
            Event=runtime.event,
            Lock=threading.Lock,
        ),
    )
    monkeypatch.setattr(lh.os, "_exit", runtime.exits.append)
    monkeypatch.setattr(lh.signal, "signal", lambda *_: None)
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", "original-test-status")
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", "original-test-instance")
    monkeypatch.setattr(
        lh, "wait_for_ready", lambda *_: lh.ReadyInfo(6400, "owned@12345678", "owned")
    )
    monkeypatch.setattr(lh, "compile_probe", lambda *_: True)
    monkeypatch.setattr(
        lh, "run_smoke_leg", lambda *_a, **_kw: lh.LegOutcome("smoke", "pass", True)
    )
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
        "--no-warmup",
        "--legs",
        "smoke",
        "--overall-timeout",
        "1",
        "--cleanup-timeout",
        "2",
    ]
    return SimpleNamespace(
        runtime=runtime, argv=argv, reports=reports, project=tmp_path / "project"
    )


@pytest.mark.parametrize("phase", ["resolver", "warmup", "launch"])
def test_expiry_cannot_acquire_or_leave_a_late_editor(sandbox, monkeypatch, phase):
    # Given: real _main control flow crosses expiry during discovery or native launch.
    state = {"alive": False, "launches": 0, "stops": 0}

    def resolve(*_):
        if phase == "resolver":
            sandbox.runtime.advance(1.1)
        return lh.EditorSpec("owned-fake", "6000")

    def warmup(*_):
        sandbox.runtime.advance(1.1)
        return 0

    def launch(_editor, _project, status, *_):
        state["launches"] += 1
        if phase == "launch":
            sandbox.runtime.advance(1.1)
            state["registry_retained_while_acquiring"] = status.exists()
        state["alive"] = True
        return lh.Handle(pid=12345)

    def stop(_):
        state["stops"] += 1
        state["alive"] = False

    launcher = SimpleNamespace(
        resolve_editor=resolve,
        warmup=warmup,
        launch=launch,
        teardown=stop,
        is_alive=lambda _: state["alive"],
        tail_log=lambda *_: "",
    )
    monkeypatch.setattr(lh, "make_launcher", lambda _: launcher)
    # When: expiry cleanup runs before the delayed operation returns.
    argv = sandbox.argv.copy()
    if phase == "warmup":
        argv.remove("--no-warmup")
    result = lh.main(argv)
    # Then: discovery/warmup cannot start launch; an admitted late launch is reclaimed.
    assert result == 2
    if phase != "launch":
        assert state["launches"] == 0
    else:
        assert not state["alive"] and state["stops"] == 1
        assert state["registry_retained_while_acquiring"]


def test_normal_cleanup_has_its_full_independent_budget(sandbox, monkeypatch):
    # Given: execution ends before its deadline, but cleanup crosses that deadline.
    state = {"alive": True, "stops": 0}

    def smoke(*_a, **_kw):
        sandbox.runtime.advance(0.8)
        return lh.LegOutcome("smoke", "pass", True)

    def stop(_):
        state["stops"] += 1
        sandbox.runtime.advance(0.4)
        state["alive"] = False

    monkeypatch.setattr(lh, "run_smoke_leg", smoke)
    monkeypatch.setattr(
        lh,
        "make_launcher",
        lambda _: SimpleNamespace(
            resolve_editor=lambda *_: lh.EditorSpec("owned-fake", "6000"),
            launch=lambda *_: lh.Handle(pid=12345),
            teardown=stop,
            is_alive=lambda _: state["alive"],
            tail_log=lambda *_: "",
        ),
    )
    # When: ordinary cleanup uses only 0.4 seconds of its independent 2-second budget.
    result = lh.main(sandbox.argv)
    # Then: the completed execution does not acquire a later watchdog failure.
    assert result == 0 and state["stops"] == 1
    assert sandbox.runtime.exits == []
    assert ET.parse(sandbox.reports / "junit-all.xml").find(".//failure") is None


def test_failed_docker_relaunch_retains_pending_replacement_and_scene(sandbox, monkeypatch):
    # Given: an exact owned first container stops; the replacement launch loses its reply.
    calls, launch_count = [], 0
    owned_id = "a" * 64
    state = {}
    monkeypatch.setenv("UNITY_IMAGE", "owned-fake-image")

    def docker(argv, **kwargs):
        nonlocal launch_count
        calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            launch_count += 1
            if launch_count == 2:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return subprocess.CompletedProcess(argv, 0, stdout=owned_id)
        return subprocess.CompletedProcess(argv, 0, stdout="running")

    def make_launcher(args):
        state["launcher"] = lh.DockerLauncher(args)
        state["args"] = args
        return state["launcher"]

    def prepare(args, _instance):
        name = "Scene_" + "b" * 32
        target = sandbox.project / "Assets/__MCPHarness" / (name + ".unity")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("owned synthetic scene")
        args._harness_scene = dict(
            project=str(sandbox.project),
            name=name,
            directory_created=True,
            directory_meta_created=False,
        )
        state["scene"] = target
        return True

    monkeypatch.setattr(lh, "_run_docker", docker)
    monkeypatch.setattr(lh, "make_launcher", make_launcher)
    monkeypatch.setattr(lh, "prepare_ci_scene", prepare)
    monkeypatch.setattr(lh, "run_playmode_with_retry", lambda *_a, **kw: kw["relaunch"]())
    argv = sandbox.argv.copy()
    argv[argv.index("--legs") + 1] = "playmode"
    argv.append("--ci")
    # When: replacement launch fails while the old resident handle is still assigned.
    try:
        result = lh.main(argv)
    except subprocess.TimeoutExpired:
        result = 2  # baseline propagated the launch failure after deleting recovery evidence
    # Then: registry, scene, and the unknown replacement identity survive; only exact ID removed.
    assert result == 2
    receipt = json.loads((sandbox.reports / "reuse.json").read_text())
    assert receipt["container"] == state["launcher"].pending_handle.container
    assert receipt["ownership_confirmed"] is False
    assert Path(receipt["status_dir"]).is_dir() and state["scene"].is_file()
    assert all(call[-1] == owned_id for call in calls if call[:3] == ["docker", "rm", "-f"])
