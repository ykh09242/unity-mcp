"""Exercise HTTP child coverage through real nested pytest/xdist processes."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests import test_http_security as wrapper


@pytest.mark.parametrize("branch", [False, True])
def test_http_child_coverage_joins_parent_worker_without_replacing_its_data(tmp_path, branch):
    pytest.importorskip("pytest_cov")
    server = Path(wrapper.__file__).resolve().parents[1]
    child = tmp_path / "test_child.py"
    child.write_text(
        """import sys
import pytest
from core.endpoint_logging import endpoint_for_log

@pytest.mark.parametrize("index", range(8))
def test_child(index):
    assert endpoint_for_log("http://[::1]:8080/path?secret=hidden") == "http://[::1]:8080/path"
    assert "tests.http.prepared_tool_registration" not in sys.modules
""",
        encoding="utf-8",
    )
    outer = tmp_path / "test_outer.py"
    outer.write_text(
        f"""from tests import test_http_security as wrapper

def test_outer(tmp_path, pytestconfig, monkeypatch):
    run = wrapper._run_http_subprocess
    def child(args, **kwargs):
        args[3] = {str(child)!r}
        args.remove("--ci-first-file=http/test_client_assurance_wire.py")
        args += ["--confcutdir", {str(tmp_path)!r}, "-c", {str(server / "tests" / "pytest.ini")!r}]
        return run(args, **kwargs)
    monkeypatch.setattr(wrapper, "_run_http_subprocess", child)
    wrapper.test_local_http_security_regressions(tmp_path, pytestconfig)
""",
        encoding="utf-8",
    )
    # This file runs on another outer worker and must survive the nested child run.
    parent = tmp_path / "test_parent.py"
    parent.write_text(
        """from core.endpoint_logging import endpoint_for_log

def test_parent():
    assert endpoint_for_log("invalid") == "<invalid endpoint>"
""",
        encoding="utf-8",
    )
    report = tmp_path / "coverage.json"
    data_file = tmp_path / "parent.coverage"
    result = wrapper._run_http_subprocess(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            str(outer),
            str(parent),
            "-c",
            str(server / "tests" / "pytest.ini"),
            "--confcutdir",
            str(tmp_path),
            "--basetemp",
            str(tmp_path / "outer-tmp"),
            "-p",
            "no:cacheprovider",
            "-n",
            "2",
            "--dist=loadfile",
            "--cov=core.endpoint_logging",
            "--cov-config",
            str(server / "pyproject.toml"),
            f"--cov-report=json:{report}",
            *(["--cov-branch"] if branch else []),
            "-q",
            "-W",
            "error",
        ],
        cwd=server,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(server), str(server / "src")]),
            "COVERAGE_FILE": str(data_file),
            "DISABLE_TELEMETRY": "1",
            "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
            "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
        },
        capture_output=True,
        text=True,
        timeout=wrapper.watchdog_timeout(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    matches = [
        value
        for key, value in files.items()
        if Path(key).as_posix().endswith("core/endpoint_logging.py")
    ]
    assert len(matches) == 1
    measured = matches[0]
    source = (server / "src" / "core" / "endpoint_logging.py").read_text().splitlines()
    child_line = next(i for i, line in enumerate(source, 1) if "authority +=" in line)
    parent_line = next(
        i for i, line in enumerate(source, 1) if 'return "<invalid endpoint>"' in line
    )
    assert parent_line in measured["executed_lines"]
    assert child_line in measured["executed_lines"], "HTTP child-only production line was lost"
    assert data_file.is_file()
    assert json.loads(report.read_text())["meta"]["branch_coverage"] is branch
