"""Run the real FastMCP surface independently of the legacy module stubs."""

from tests.timing import watchdog_timeout

import json
import os
import subprocess
import sys
from pathlib import Path

_OUTPUT_TAIL_LIMIT = 4096
_HTTP_WORKERS = 4


def _output_tail(output: bytes | str | None) -> str:
    if output is None:
        return "<none>"
    if isinstance(output, bytes):
        return output[-_OUTPUT_TAIL_LIMIT:].decode("utf-8", errors="replace")
    return output[-_OUTPUT_TAIL_LIMIT:]


def _worker_progress(directory: Path) -> str:
    rows = []
    for worker in range(_HTTP_WORKERS):
        name = f"gw{worker}"
        try:
            with (directory / f"{name}.json").open(encoding="utf-8") as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise ValueError("Oversized worker phase record")
            state = json.loads(raw)
            phase, nodeid = state["phase"], state["nodeid"]
            if not isinstance(phase, str) or not isinstance(nodeid, str):
                raise ValueError("Invalid worker phase record")
            suffix = "..." if len(nodeid) > 1024 else ""
            rows.append(f"{name}: {phase[:80]}: {nodeid[:1024]}{suffix}")
        except FileNotFoundError:
            rows.append(f"{name}: no phase recorded")
        except (OSError, ValueError, KeyError, TypeError) as error:
            rows.append(f"{name}: phase unavailable ({type(error).__name__})")
    return "\n".join(rows)


def _failure_message(
    reason: str, stdout: bytes | str | None, stderr: bytes | str | None, progress_dir: Path
) -> str:
    return (
        f"{reason}\nworker phases:\n{_worker_progress(progress_dir)}"
        f"\nstdout tail:\n{_output_tail(stdout)}\nstderr tail:\n{_output_tail(stderr)}"
    )


def test_local_http_security_regressions(tmp_path, pytestconfig):
    server_root = Path(__file__).resolve().parents[1]
    progress_dir = tmp_path / "http-progress"
    env = {
        **os.environ,
        "UNITY_MCP_RUN_HTTP_TESTS": "1",
        "DISABLE_TELEMETRY": "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
        "UNITY_MCP_HTTP_TEST_PROGRESS_DIR": str(progress_dir),
    }
    report_args = []
    if xmlpath := pytestconfig.getoption("xmlpath", default=None):
        report = Path(xmlpath).resolve()
        report_args = ["--junitxml", str(report.with_stem(f"{report.stem}-http"))]
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/http",
                "-n",
                str(_HTTP_WORKERS),
                "--dist=loadfile",
                "--max-worker-restart=0",
                "-vv",
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
            # The suite watchdog is distinct from the request deadlines asserted inside.
            # Operators can increase the shared multiplier for slow/oversubscribed hosts.
            timeout=watchdog_timeout(900),
        )
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            _failure_message(
                f"HTTP test subprocess timed out after {error.timeout:g}s",
                error.output,
                error.stderr,
                progress_dir,
            )
        ) from error
    if result.returncode != 0:
        raise AssertionError(
            _failure_message(
                f"HTTP test subprocess exited with status {result.returncode}",
                result.stdout,
                result.stderr,
                progress_dir,
            )
        )
