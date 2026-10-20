"""Run the real FastMCP surface independently of the legacy module stubs."""

import os
import subprocess
import sys
from pathlib import Path


def test_local_http_security_regressions(tmp_path, pytestconfig):
    server_root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "UNITY_MCP_RUN_HTTP_TESTS": "1",
        "DISABLE_TELEMETRY": "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
    }
    report_args = []
    if xmlpath := pytestconfig.getoption("xmlpath", default=None):
        report = Path(xmlpath).resolve()
        report_args = ["--junitxml", str(report.with_stem(f"{report.stem}-http"))]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/http",
            "-n",
            "4",
            "--dist=loadfile",
            "--max-worker-restart=0",
            "-q",
            "--tb=short",
            "-W",
            "error",
            "--durations=20",
            "--basetemp",
            str(tmp_path / "http-tmp"),
            "-o",
            f"cache_dir={tmp_path / 'http-cache'}",
            *report_args,
        ],
        cwd=server_root,
        env=env,
        capture_output=True,
        text=True,
        # Four workers overlap isolated SDK subprocesses without multiplying by host CPUs.
        # Keep each file's fixtures together and retain the existing total/request deadlines.
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
