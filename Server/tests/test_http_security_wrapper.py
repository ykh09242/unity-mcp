"""Diagnostics for the isolated HTTP test subprocess wrapper."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import test_http_security as wrapper


class _Config:
    def __init__(self, xmlpath=None):
        self.xmlpath = xmlpath

    def getoption(self, name, default=None):
        assert name == "xmlpath"
        return self.xmlpath if self.xmlpath is not None else default


@pytest.mark.parametrize(("scale", "expected_timeout"), [("1", 900), ("2", 1800)])
def test_wrapper_preserves_child_command_and_success(
    tmp_path, monkeypatch, scale, expected_timeout
):
    monkeypatch.setenv("UNITY_MCP_TEST_TIMEOUT_SCALE", scale)
    observed = {}

    def completed(args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return subprocess.CompletedProcess(args, 0, "passed", "")

    monkeypatch.setattr(wrapper.subprocess, "run", completed)
    report = tmp_path / "outer.xml"
    wrapper.test_local_http_security_regressions(tmp_path, _Config(report))

    args = observed["args"]
    kwargs = observed["kwargs"]
    assert args[:4] == [sys.executable, "-m", "pytest", "tests/http"]
    assert args[4:8] == ["-n", "4", "--dist=loadfile", "--max-worker-restart=0"]
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

    monkeypatch.setattr(wrapper.subprocess, "run", timed_out)
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

    monkeypatch.setattr(wrapper.subprocess, "run", failed)
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

    monkeypatch.setattr(wrapper.subprocess, "run", timed_out)
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
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(probe),
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

    monkeypatch.setattr(wrapper.subprocess, "run", timed_out)
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

    monkeypatch.setattr(wrapper.subprocess, "run", timed_out)
    with pytest.raises(AssertionError) as raised:
        wrapper.test_local_http_security_regressions(tmp_path, _Config())
    message = str(raised.value)
    for worker in range(4):
        assert f"gw{worker}: call: http/test_owned.py::test_long[" in message
    assert len(message) < 13000
