"""Run the real FastMCP surface independently of the legacy module stubs."""

import os
import subprocess
import sys
from pathlib import Path


def test_local_http_security_regressions(tmp_path):
    server_root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "UNITY_MCP_RUN_HTTP_TESTS": "1",
        "DISABLE_TELEMETRY": "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/http",
            "-q",
            "--tb=short",
            "-W",
            "error",
            "--basetemp",
            str(tmp_path / "http-tmp"),
            "-o",
            f"cache_dir={tmp_path / 'http-cache'}",
        ],
        cwd=server_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
