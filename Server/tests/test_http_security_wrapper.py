"""Diagnostics for the isolated HTTP test subprocess wrapper."""

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
