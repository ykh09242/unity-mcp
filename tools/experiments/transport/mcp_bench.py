#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = ["pydantic==2.13.5"]
# ///
# How to run: Server/.venv/Scripts/python.exe tools/experiments/transport/mcp_bench.py OUTPUT
"""Identical JSON-RPC stdio client for official C# SDK and Python FastMCP."""
from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
import sys
import threading
import time
import uuid
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Final, Protocol, assert_never

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

JSON: Final = TypeAdapter(dict[str, JsonValue])
HERE: Final = Path(__file__).resolve().parent
ROOT: Final = HERE.parents[2]


class Metric(BaseModel):
    """Process counters sampled outside request timing through PowerShell."""
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    cpu_seconds: float
    working_set_bytes: int
    peak_working_set_bytes: int
    private_bytes: int


class ChildExitedError(RuntimeError):
    """An owned subprocess ended before completing its protocol exchange."""


class TextContent(BaseModel):
    """Parse the representative text-only MCP content boundary."""
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    type: str
    text: str


class ToolResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    content: list[TextContent]
    structuredContent: dict[str, JsonValue]
    isError: bool = False


class Row(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    workload: str
    warmup: int
    iterations: int
    client_total_p50_ms: float
    client_total_p95_ms: float
    response_bytes: int
    samples_ms: list[float]
    output_sha256: str
    server_cpu_seconds: float
    server_counters_after: Metric


class SDKResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    label: str
    startup_initialize_ms: float
    rows: list[Row]


class Pipe(Protocol, metaclass=ABCMeta):
    # These are structural contracts, not concrete ellipsis-bodied methods returning None.
    @abstractmethod
    def write(self, data: bytes, /) -> int: ...
    @abstractmethod
    def readline(self, /) -> bytes: ...
    @abstractmethod
    def flush(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Child:
    process: subprocess.Popen[bytes]
    stdin: Pipe
    stdout: Pipe

    def request(self, message: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], int]:
        _ = self.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        self.stdin.flush()
        while True:
            line = self.stdout.readline()
            if not line:
                raise EOFError("Owned MCP child exited before response")
            response = JSON.validate_json(line)
            if response.get("id") == message.get("id"):
                if "error" in response:
                    raise RuntimeError(json.dumps(response["error"]))
                return response, len(line)

    def metric(self, pid: int) -> Metric:
        script = (f"$p = Get-Process -Id {pid}; "
                  "@{cpu_seconds=$p.CPU; working_set_bytes=$p.WorkingSet64; "
                  "peak_working_set_bytes=$p.PeakWorkingSet64; "
                  "private_bytes=$p.PrivateMemorySize64} | ConvertTo-Json -Compress")
        raw = subprocess.check_output(["pwsh", "-NoProfile", "-Command", script], timeout=10)
        return Metric.model_validate_json(raw)


def quantile(values: list[float], q: float) -> float:
    return sorted(values)[math.ceil(len(values) * q) - 1]


def canonical(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def stop_child(process: subprocess.Popen[bytes]) -> None:
    """Terminate an owned native process through its already-held OS handle."""
    if process.poll() is None:
        process.terminate()


def python_command(fixtures: Path) -> list[str]:
    """Use the actual interpreter with repository packages, avoiding the Windows venv redirector."""
    server = HERE / "python_server.py"
    packages = ROOT / "Server/.venv/Lib/site-packages"
    bootstrap = (f"import sys,runpy,site; site.addsitedir({str(packages)!r}); "
                 f"sys.argv=[{str(server)!r},'mcp',{str(fixtures)!r}]; "
                 f"runpy.run_path({str(server)!r},run_name='__main__')")
    return [str(Path(sys.base_prefix) / "python.exe"), "-S", "-c", bootstrap]


@dataclass(frozen=True, slots=True)
class Case:
    command: list[str]
    label: str
    fixtures: Path
    artifacts: Path


def measure(case: Case) -> SDKResult:
    fixtures, label, command = case.fixtures, case.label, case.command
    rows: list[Row] = []
    started = time.perf_counter()
    error_path = case.artifacts / f"{label}.stderr"
    with error_path.open("wb") as errors:
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors) as process:
            if process.stdin is None or process.stdout is None:
                raise ChildExitedError("Missing requested stdio pipes")
            child = Child(process, process.stdin, process.stdout)
            # Bounds startup/read hangs; child is always ours, never an existing process.
            watchdog = threading.Timer(120, stop_child, args=(process,))
            watchdog.start()
            try:
                response, _ = child.request({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "owned-comparison", "version": "1"}}})
                startup_ms = (time.perf_counter() - started) * 1000
                # The marker verifies the sampled PID belongs to the actual SDK server.
                metric_pid = int(error_path.read_text().split("owned_process_pid=", 1)[1].split()[0])
                if metric_pid != process.pid:
                    raise ChildExitedError("Experiment server unexpectedly forked a child")
                _ = child.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                child.stdin.flush()
                tools, _ = child.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                _ = (case.artifacts / f"{label}.tools.json").write_text(json.dumps(tools), encoding="utf-8")
                for workload in ("small", "state", "large", "job"):
                    expected = JSON.validate_json((fixtures / f"{workload}.json").read_bytes())
                    samples: list[float] = []
                    before = child.metric(metric_pid)
                    response_bytes = 0
                    output = expected
                    for iteration in range(-5, 30):
                        if iteration == 0:
                            before = child.metric(metric_pid)
                        arguments: dict[str, JsonValue] = {"filter_text": f"{workload}:{iteration}", "count": "all"}
                        name = "read_console"
                        match workload:
                            case "job":
                                name = "get_test_job"
                                arguments = {"job_id": "owned-job", "include_details": True}
                            case "small" | "state" | "large":
                                pass
                            case unreachable:
                                assert_never(unreachable)
                        start = time.perf_counter()
                        response, response_bytes = child.request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": name, "arguments": arguments}})
                        ms = (time.perf_counter() - start) * 1000
                        result = ToolResult.model_validate(response["result"])
                        output = result.structuredContent
                        text_output = JSON.validate_json(result.content[0].text)
                        if result.isError or canonical(output) != canonical(expected) or canonical(text_output) != canonical(expected):
                            raise AssertionError(f"{label}/{workload}: output differs")
                        if iteration >= 0:
                            samples.append(ms)
                    after = child.metric(metric_pid)
                    rows.append(Row(workload=workload, warmup=5, iterations=30,
                        client_total_p50_ms=quantile(samples, .50), client_total_p95_ms=quantile(samples, .95),
                        response_bytes=response_bytes, samples_ms=samples,
                        output_sha256=hashlib.sha256(canonical(output).encode()).hexdigest(),
                        server_cpu_seconds=after.cpu_seconds - before.cpu_seconds,
                        server_counters_after=after))
                return SDKResult(label=label, startup_initialize_ms=startup_ms, rows=rows)
            finally:
                watchdog.cancel()
                child.stdin.close()
                stop_child(process)
                _ = process.wait(timeout=5)


def main() -> None:
    fixture_path = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / ".artifacts/fixtures"
    fixtures = str(fixture_path)
    artifacts = HERE / ".artifacts" / uuid.uuid4().hex
    artifacts.mkdir(parents=True)
    commands = [("python", python_command(fixture_path)),
                ("csharp", [str(HERE / "dotnet/bin/Release/net10.0/TransportProbe.exe"), "mcp", fixtures])]
    results = [measure(Case(command, label, fixture_path, artifacts)) for label, command in commands]
    python_tools = JSON.validate_json((artifacts / "python.tools.json").read_bytes())["result"]
    csharp_tools = JSON.validate_json((artifacts / "csharp.tools.json").read_bytes())["result"]
    if canonical(python_tools) != canonical(csharp_tools):
        raise AssertionError("Advertised subset schemas differ")
    report = {"schema_id": "unity-mcp-sdk-stdio-probe-v1", "os": platform.platform(),
              "python_client": platform.python_version(), "tool_schema_equal": True, "results": [result.model_dump() for result in results],
              "timing_scope": "client write + OS pipes + SDK dispatch/serialization + JSON parse; excludes output canonical/hash verification",
              "limitation": "Two synthetic handlers; no Unity integration, full tool parity, HTTP or security validation parity"}
    _ = Path(sys.argv[1]).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
