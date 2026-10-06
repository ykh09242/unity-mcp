#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic==2.13.5"]
# ///
# How to run: Server/.venv/Scripts/python.exe tools/experiments/transport/startup_bench.py OUTPUT FIXTURES
"""Fresh processes with cached build/import files; this is not cold machine boot."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.experiments.transport.mcp_bench import Child, ChildExitedError, HERE, quantile, stop_child, python_command  # noqa: E402

TRIALS: Final = 5


def trial(command: list[str]) -> float:
    started = time.perf_counter()
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        if process.stdin is None or process.stdout is None:
            raise ChildExitedError("Missing requested subprocess pipes")
        watchdog = threading.Timer(15, stop_child, args=(process,))
        watchdog.start()
        try:
            _ = Child(process, process.stdin, process.stdout).request({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "owned-startup", "version": "1"}}})
            return (time.perf_counter() - started) * 1000
        finally:
            watchdog.cancel()
            process.stdin.close()
            stop_child(process)
            _ = process.wait(timeout=5)


def main() -> None:
    fixtures = sys.argv[2]
    python = python_command(Path(fixtures))
    csharp = [str(HERE / "dotnet/bin/Release/net10.0/TransportProbe.exe"), "mcp", fixtures]
    samples: dict[str, list[float]] = {"python": [], "csharp_apphost": []}
    for _ in range(TRIALS):
        samples["python"].append(trial(python))
        samples["csharp_apphost"].append(trial(csharp))
    report = {"scope": "Fresh process spawn through MCP initialize; includes fixture loading. Cached files, alternating trials.",
              "csharp_launch": "Framework-dependent Windows apphost executable; Release net10.0, not dotnet run, not NativeAOT",
              "trials": TRIALS, "samples_ms": samples,
              "p50_ms": {key: quantile(value, .50) for key, value in samples.items()},
              "p95_ms": {key: quantile(value, .95) for key, value in samples.items()}}
    _ = Path(sys.argv[1]).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
