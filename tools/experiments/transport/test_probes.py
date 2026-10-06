#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic==2.13.5", "pytest>=9.1.1"]
# ///
# How to run: Server/.venv/Scripts/python.exe -m pytest tools/experiments/transport/test_probes.py -q
"""Owned process cleanup regression; MCP and IPC scenarios live in RunProbes."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.experiments.transport.mcp_bench import stop_child, Pipe  # noqa: E402


def parse_owned_pid(stdout: Pipe) -> int:
    """Parse the subprocess pipe boundary through its byte-stream contract."""
    return int(stdout.readline())


def test_owned_process_cleanup_when_child_waits() -> None:
    # Given: the actual owned Windows interpreter, with no redirector descendant.
    command = [str(Path(sys.base_prefix) / "python.exe"), "-c", "import os,time; print(os.getpid(),flush=True); time.sleep(60)"]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        assert process.stdout is not None
        actual_pid = parse_owned_pid(process.stdout)
        assert actual_pid == process.pid
        try:
            # When: cleanup terminates the owned process through its held handle.
            stop_child(process)
            _ = process.wait(timeout=5)
            # Then: the actual server process has exited within the bound.
            script = f"if (Get-Process -Id {actual_pid} -ErrorAction SilentlyContinue) {{ exit 1 }} else {{ exit 0 }}"
            result = subprocess.run(["pwsh", "-NoProfile", "-Command", script],
                                    check=False, capture_output=True, timeout=5)
            assert result.returncode == 0
        finally:
            stop_child(process)
