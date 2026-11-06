"""Diagnostics for the isolated HTTP test subprocess wrapper."""

import json
import builtins
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import test_http_security as wrapper


class _Config:
    def __init__(self, xmlpath=None):
        self.xmlpath = xmlpath
        self.pluginmanager = self

    def hasplugin(self, name):
        assert name == "_cov"
        return False

    def getoption(self, name, default=None):
        assert name == "xmlpath"
        return self.xmlpath if self.xmlpath is not None else default


def test_focused_wrapper_runs_without_importing_coverage(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def import_without_coverage(name, *args, **kwargs):
        if name == "coverage" or name.startswith(("coverage.", "pytest_cov")):
            raise AssertionError("Focused HTTP wrapper must not require coverage")
        return original_import(name, *args, **kwargs)

    def completed(args, **kwargs):
        assert not any(arg.startswith("--cov") for arg in args)
        return subprocess.CompletedProcess(args, 0, "passed", "")

    monkeypatch.setattr(builtins, "__import__", import_without_coverage)
    monkeypatch.setattr(wrapper, "_run_http_subprocess", completed)
    wrapper.test_local_http_security_regressions(tmp_path, _Config())
    assert not (tmp_path / "http-coverage").exists()


def test_owned_subprocess_preserves_exit_and_captured_text():
    result = wrapper._run_http_subprocess(
        [
            sys._base_executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 7
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"


@pytest.mark.parametrize("controller_exits", [False, True])
def test_owned_subprocess_timeout_preserves_both_output_streams(controller_exits):
    child = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"
    )
    tail = child if controller_exits else "import time; time.sleep(30)"
    code = (
        "import sys; print('stdout marker', flush=True); print('stderr marker', file=sys.stderr, flush=True); "
        + tail
    )
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        wrapper._run_http_subprocess(
            [sys._base_executable, "-c", code], capture_output=True, text=True, timeout=2
        )
    assert "stdout marker" in wrapper._output_tail(raised.value.output)
    assert "stderr marker" in wrapper._output_tail(raised.value.stderr)


@pytest.mark.parametrize("controller_exits", [False, True])
def test_timeout_terminates_owned_controller_worker_and_grandchild(tmp_path, controller_exits):
    probe = tmp_path / "process_probe.py"
    probe.write_text(
        """import os, subprocess, sys, time
from pathlib import Path
stage, root, exits = sys.argv[1], Path(sys.argv[2]), sys.argv[3] == "1"
if stage != "grandchild":
    child = "worker" if stage == "controller" else "grandchild"
    output = {} if exits else {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    subprocess.Popen([sys.executable, __file__, child, str(root), sys.argv[3]], **output)
(root / stage).write_text(str(os.getpid()))
if stage == "controller" and exits:
    while not (root / "grandchild").exists():
        time.sleep(0.01)
    sys.exit(0)
time.sleep(30)
""",
        encoding="utf-8",
    )
    args = [
        sys._base_executable,
        str(probe),
        "controller",
        str(tmp_path),
        str(int(controller_exits)),
    ]
    pids = []
    handles = {}
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            wrapper._run_http_subprocess(args, capture_output=True, timeout=3)
        for stage in ("worker", "grandchild"):
            pids.append(int((tmp_path / stage).read_text()))

        def alive(pid):
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes

                kernel = ctypes.windll.kernel32
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
                handle = handles.get(pid)
                if handle is None:
                    handle = kernel.OpenProcess(0x100001, False, pid)
                    if handle:
                        handles[pid] = handle
                if not handle:
                    return False
                return kernel.WaitForSingleObject(handle, 0) == 0x102
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True

        deadline = time.monotonic() + 2
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not any(alive(pid) for pid in pids), "owned descendants survived timeout"
    finally:
        # The probe owns these sleeping processes; clean them even on the red run.
        if os.name == "nt":
            import ctypes

            kernel = ctypes.windll.kernel32
            for handle in handles.values():
                if kernel.WaitForSingleObject(handle, 0) == 0x102:
                    kernel.TerminateProcess(handle, 1)
                kernel.CloseHandle(handle)
        else:
            import signal

            for pid in reversed(pids):
                try:
                    os.kill(pid, getattr(signal, "SIGKILL"))
                except ProcessLookupError:
                    pass


@pytest.mark.parametrize(("scale", "expected_timeout"), [("1", 900), ("2", 1800)])
@pytest.mark.parametrize("pythonpath", [None, "existing-python-path"])
def test_wrapper_preserves_child_command_and_success(
    tmp_path, monkeypatch, scale, expected_timeout, pythonpath
):
    monkeypatch.setenv("UNITY_MCP_TEST_TIMEOUT_SCALE", scale)
    if pythonpath is None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONPATH", pythonpath)
    observed = {}

    def completed(args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return subprocess.CompletedProcess(args, 0, "passed", "")

    monkeypatch.setattr(wrapper, "_run_http_subprocess", completed)
    report = tmp_path / "outer.xml"
    wrapper.test_local_http_security_regressions(tmp_path, _Config(report))

    args = observed["args"]
    kwargs = observed["kwargs"]
    assert args[:4] == [sys.executable, "-m", "pytest", "tests/http"]
    assert args[4:8] == ["-n", "4", "--dist=loadfile", "--max-worker-restart=0"]
    assert args[8:12] == [
        "-p",
        "pytest_ci_schedule",
        "--no-loadscope-reorder",
        "--ci-first-file=http/test_client_assurance_wire.py",
    ]
    assert "-vv" in args
    assert "-q" not in args
    assert args[args.index("--basetemp") + 1] == str(tmp_path / "http-tmp")
    assert f"cache_dir={tmp_path / 'http-cache'}" in args
    assert args[-2:] == ["--junitxml", str(tmp_path / "outer-http.xml")]
    assert kwargs["cwd"] == Path(wrapper.__file__).resolve().parents[1]
    assert kwargs["env"]["UNITY_MCP_RUN_HTTP_TESTS"] == "1"
    assert kwargs["env"]["UNITY_MCP_SKIP_STARTUP_CONNECT"] == "1"
    assert kwargs["env"]["UNITY_MCP_LOG_DIR"] == str(tmp_path / "logs")
    assert kwargs["env"]["UNITY_MCP_HTTP_TEST_PROGRESS_DIR"] == str(tmp_path / "http-progress")
    assert kwargs["env"]["PYTHONPATH"].split(os.pathsep) == [
        str(Path(wrapper.__file__).resolve().parents[2] / "tools"),
        *([pythonpath] if pythonpath is not None else []),
    ]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == expected_timeout


@pytest.mark.parametrize(
    ("stdout", "stderr", "expected"),
    [
        (b"child collected\nlast completed test", b"worker stderr", "last completed test"),
        ("child collecting", "worker stderr", "child collecting"),
        (None, None, "<none>"),
        (b"bad byte: \xff", None, "bad byte: \ufffd"),
        ("early marker" + "x" * 12000 + "late marker", None, "late marker"),
    ],
)
def test_wrapper_timeout_reports_bounded_child_progress(
    tmp_path, monkeypatch, stdout, stderr, expected
):
    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(
            args, timeout=kwargs["timeout"], output=stdout, stderr=stderr
        )

    monkeypatch.setattr(wrapper, "_run_http_subprocess", timed_out)
    with pytest.raises(AssertionError) as raised:
        wrapper.test_local_http_security_regressions(tmp_path, _Config())

    message = str(raised.value)
    assert f"{wrapper.watchdog_timeout(900):g}s" in message
    assert expected in message
    assert "stdout" in message
    assert "stderr" in message
    assert isinstance(raised.value.__cause__, subprocess.TimeoutExpired)
    if stdout is not None and stderr is not None:
        assert "worker stderr" in message
    if isinstance(stdout, str) and stdout.startswith("early marker"):
        assert "early marker" not in message
        assert len(message) < 10000


def test_wrapper_nonzero_child_reports_exit_and_bounded_tail(tmp_path, monkeypatch):
    def failed(args, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(
            args, 7, "early marker" + "x" * 12000 + "last test", b"stderr: \xff"
        )

    monkeypatch.setattr(wrapper, "_run_http_subprocess", failed)
    with pytest.raises(AssertionError) as raised:
        wrapper.test_local_http_security_regressions(tmp_path, _Config())

    message = str(raised.value)
    assert "7" in message
    assert "last test" in message
    assert "stderr: \ufffd" in message
    assert "early marker" not in message
    assert len(message) < 10000


@pytest.mark.parametrize("phase", ["setup", "call", "teardown", "session teardown"])
def test_timeout_keeps_earlier_worker_phase_after_later_output(tmp_path, monkeypatch, phase):
    stalled = "http/test_earlier.py::test_pending_cleanup[owned]"
    progress = tmp_path / "http-progress"

    def timed_out(args, **kwargs):
        progress.mkdir()
        (progress / "gw0.json").write_text(
            json.dumps({"nodeid": stalled, "phase": phase}), encoding="utf-8"
        )
        (progress / "gw3.json").write_text(
            json.dumps({"nodeid": "http/test_later.py::test_done", "phase": "finished"}),
            encoding="utf-8",
        )
        stdout = (
            f"tests/{stalled}\n"
            + "later completed traffic\n" * 1000
            + "[gw3] [ 99%] PASSED tests/http/test_later.py::test_done\n"
        )
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], output=stdout.encode())

    monkeypatch.setattr(wrapper, "_run_http_subprocess", timed_out)
    with pytest.raises(AssertionError) as raised:
        wrapper.test_local_http_security_regressions(tmp_path, _Config())
    message = str(raised.value)
    assert f"gw0: {phase}: {stalled}" in message
    assert "gw3: finished: http/test_later.py::test_done" in message
    assert "stdout tail:" in message
    assert len(message) < 10000


def test_http_worker_progress_tracks_real_pytest_phases(tmp_path):
    server_root = Path(wrapper.__file__).resolve().parents[1]
    progress = tmp_path / "progress"
    probe = tmp_path / "test_phase_probe.py"
    probe.write_text(
        """import json
import os
import sys
from pathlib import Path
import pytest


def current():
    path = Path(os.environ["UNITY_MCP_HTTP_TEST_PROGRESS_DIR"])
    return json.loads((path / (os.environ["PYTEST_XDIST_WORKER"] + ".json")).read_text())


@pytest.fixture
def phase_probe(request):
    assert current() == {"nodeid": request.node.nodeid, "phase": "setup"}
    yield
    assert current() == {"nodeid": request.node.nodeid, "phase": "teardown"}


@pytest.mark.parametrize("index", range(8))
def test_phases(phase_probe, request, index):
    assert current() == {"nodeid": request.node.nodeid, "phase": "call"}
    assert "tests.http.prepared_tool_registration" not in sys.modules
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(probe),
            "-c",
            str(server_root / "tests" / "pytest.ini"),
            "-n",
            "4",
            "--max-worker-restart=0",
            "-p",
            "tests.http.conftest",
            "--confcutdir",
            str(tmp_path),
            "--rootdir",
            str(tmp_path),
            "--basetemp",
            str(tmp_path / "worker-tmp"),
            "-o",
            f"cache_dir={tmp_path / 'cache'}",
            "-q",
            "-W",
            "error",
        ],
        cwd=server_root,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(server_root / "src"), str(server_root)]),
            "UNITY_MCP_HTTP_TEST_PROGRESS_DIR": str(progress),
            "DISABLE_TELEMETRY": "1",
            "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
            "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
        },
        capture_output=True,
        text=True,
        timeout=wrapper.watchdog_timeout(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for worker in range(4):
        state = json.loads((progress / f"gw{worker}.json").read_text(encoding="utf-8"))
        assert state["phase"] == "session finished"
        assert state["nodeid"].startswith("test_phase_probe.py::test_phases[")


@pytest.mark.parametrize(
    "record",
    [b"{partial", b"\xff", b"x" * 8193, b'{"phase": null}'],
    ids=["partial-json", "invalid-utf8", "oversized", "missing-nodeid"],
)
def test_bad_worker_record_preserves_original_timeout(tmp_path, monkeypatch, record):
    def timed_out(args, **kwargs):
        progress = tmp_path / "http-progress"
        progress.mkdir()
        (progress / "gw0.json").write_bytes(record)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], output="latest progress")

    monkeypatch.setattr(wrapper, "_run_http_subprocess", timed_out)
    with pytest.raises(AssertionError) as raised:
        wrapper.test_local_http_security_regressions(tmp_path, _Config())
    message = str(raised.value)
    assert "HTTP test subprocess timed out after" in message
    assert "gw0: phase unavailable" in message
    assert "latest progress" in message
    assert isinstance(raised.value.__cause__, subprocess.TimeoutExpired)


def test_worker_progress_and_both_output_tails_remain_bounded(tmp_path, monkeypatch):
    def timed_out(args, **kwargs):
        progress = tmp_path / "http-progress"
        progress.mkdir()
        for worker in range(4):
            (progress / f"gw{worker}.json").write_text(
                json.dumps(
                    {"phase": "call", "nodeid": "http/test_owned.py::test_long[" + "x" * 6000 + "]"}
                ),
                encoding="utf-8",
            )
        raise subprocess.TimeoutExpired(
            args, kwargs["timeout"], output="a" * 20000, stderr="b" * 20000
        )

    monkeypatch.setattr(wrapper, "_run_http_subprocess", timed_out)
    with pytest.raises(AssertionError) as raised:
        wrapper.test_local_http_security_regressions(tmp_path, _Config())
    message = str(raised.value)
    for worker in range(4):
        assert f"gw{worker}: call: http/test_owned.py::test_long[" in message
    assert len(message) < 13000


@pytest.mark.skipif(os.name != "nt", reason="Windows read handles deny replacing an open file")
def test_worker_progress_updates_while_windows_reader_holds_current_snapshot(tmp_path, monkeypatch):
    from importlib import import_module

    worker = import_module("tests.http.conftest")
    path = tmp_path / "gw0.json"
    path.write_text(json.dumps({"nodeid": "prior", "phase": "call"}), encoding="utf-8")
    monkeypatch.setattr(worker, "_progress_path", path)
    monkeypatch.setattr(worker, "_last_nodeid", "prior")
    # A normal observer may keep the current diagnostic snapshot open during a phase change.
    with path.open(encoding="utf-8"):
        worker._record_progress("teardown", "http/test_owned.py::test_observed")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "nodeid": "http/test_owned.py::test_observed",
        "phase": "teardown",
    }
