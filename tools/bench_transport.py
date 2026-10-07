# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp>=4,<5", "mcp>=2,<3", "websockets", "uvicorn", "httpx2"]
# ///
# ─── How to run ───
# Server/.venv/Scripts/python.exe tools/bench_transport.py --output reports/transport.json
"""Compare installed MCP stdio/HTTP and actual product transports with owned peers.

No Editor discovery, real ports, credential files, or product mutations are used.
See tools/tests/fixtures/transport_bench/README.md for scope and stage definitions.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anyio
import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, JsonValue

from tools.bench_transport_report import (
    Observation,
    attach_stages,
    equivalent_outputs,
    fingerprint,
    parse_output,
    summarize,
)
from tools.bench_transport_process import cleanup_process, native_python
from tools.bench_transport_resource import observe_resources
from tools.bench_transport_diagnostic import (
    DiagnosticError,
    Trace,
    patch,
    require_sdk,
    write_sidecar,
)

FIXTURE = ROOT / "tools/tests/fixtures/transport_bench"
WORKLOADS = ("small", "state", "large", "job")


class Options(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    output: Path
    samples: int
    warmup: int
    large_bytes: int
    work_ms: float
    concurrency: int
    order: str
    product_root: Path = ROOT
    product_revision: str = "current-checkout"
    cohort_gate: bool = False
    resource_contract: bool = False
    diagnostic: bool = False


async def invoke(session: ClientSession, correlation: str) -> CallToolResult:
    workload = correlation.split(":", 1)[0]
    match workload:
        case "small" | "large":
            name, arguments = "read_console", {"filter_text": correlation}
        case "state":
            name, arguments = "get_editor_state", {"correlation": correlation}
        case "job":
            name, arguments = "get_test_job", {"job_id": correlation}
        case other:
            raise ValueError(f"Unknown workload {other}")
    result = await session.call_tool(name, arguments, read_timeout_seconds=10)
    if not isinstance(result, CallToolResult) or result.is_error:
        raise RuntimeError(f"MCP tool failed: {result}")
    return result


def validate_output(result: CallToolResult) -> JsonValue:
    """Check decoded text/structured parity outside the transport timing span."""
    text = next(item.text for item in result.content if isinstance(item, TextContent))
    value = parse_output(text)
    if result.structured_content is not None:
        structured = parse_output(json.dumps(result.structured_content))
        if fingerprint(structured) != fingerprint(value):
            raise RuntimeError("MCP text and structured content differ")
    else:
        raise RuntimeError("Owned public tool omitted structured content")
    if not isinstance(value, dict) or value.get("success") is not True:
        raise RuntimeError(f"Owned workload failed: {value}")
    return value


async def measure_mode(mode: str, options: Options, directory: Path) -> dict[str, JsonValue]:
    directory.mkdir()
    diagnostic = Trace() if options.diagnostic else None
    cpu_batches: list[dict[str, JsonValue]] = []
    diagnostic_calls = 0
    token = secrets.token_hex(32)
    # Explicitly allow only process essentials; never inherit credential variables.
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if key in os.environ
    }
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join((str(options.product_root / "Server/src"), str(ROOT))),
            "BENCH_AUTH": token,
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
            "UNITY_MCP_TELEMETRY_ENABLED": "0",
            "PYTHONUTF8": "1",
            "UNITY_MCP_LOG_DIR": str(directory / "logs"),
            "UNITY_MCP_STATUS_DIR": str(directory / "status"),
            "HOME": str(directory / "home"),
            "USERPROFILE": str(directory / "home"),
        }
    )
    command = native_python(
        [
            str(FIXTURE / "server.py"),
            "--transport",
            mode,
            "--directory",
            str(directory),
            "--large-bytes",
            str(options.large_bytes),
            "--work-ms",
            str(options.work_ms),
            "--product-root",
            str(options.product_root),
            "--concurrency",
            str(options.concurrency),
            "--samples",
            str(options.samples),
        ],
        (options.product_root / "Server/src", ROOT),
    )
    if options.cohort_gate:
        command.append("--cohort-gate")
    if options.resource_contract:
        command.append("--resource-contract")
    if diagnostic is not None:
        require_sdk()
        command.append("--diagnostic")
    observations: list[Observation] = []
    started = time.perf_counter()
    process: anyio.abc.Process | None = None
    with (directory / "server.stderr.log").open("w", encoding="utf-8") as errors:
        try:
            async with AsyncExitStack() as stack:
                match mode:
                    case "stdio":
                        read, write = await stack.enter_async_context(
                            stdio_client(
                                StdioServerParameters(
                                    command=command[0], args=command[1:], env=environment
                                ),
                                errlog=errors,
                            )
                        )
                    case "http":
                        process = await anyio.open_process(
                            command, env=environment, stdout=subprocess.DEVNULL, stderr=errors
                        )
                        ready_path = directory / "ready.json"
                        with anyio.fail_after(15):
                            while not ready_path.exists():
                                if process.returncode is not None:
                                    raise RuntimeError(
                                        f"Owned HTTP server exited {process.returncode}; see {errors.name}"
                                    )
                                await anyio.sleep(0.02)
                        ready = json.loads(ready_path.read_text(encoding="utf-8"))
                        if ready["pid"] != process.pid:
                            raise RuntimeError("Owned HTTP fixture unexpectedly forked")
                        endpoint = ready["endpoint"]
                        client = await stack.enter_async_context(
                            httpx2.AsyncClient(
                                headers={"X-Unity-MCP-Token": token},
                                trust_env=False,
                                timeout=httpx2.Timeout(10),
                                follow_redirects=False,
                            )
                        )
                        read, write = await stack.enter_async_context(
                            streamable_http_client(endpoint + "/mcp", http_client=client)
                        )
                    case other:
                        raise ValueError(f"Unknown mode {other}")
                session = await stack.enter_async_context(ClientSession(read, write))
                if diagnostic is not None:
                    stack.enter_context(
                        patch(
                            session,
                            "send_request",
                            (
                                "request",
                                "result_type",
                                "request_read_timeout_seconds",
                                "metadata",
                                "progress_callback",
                            ),
                            diagnostic,
                            "client_sdk_request",
                        )
                    )
                    stack.enter_context(
                        patch(
                            session._dispatcher,
                            "send_raw_request",
                            ("method", "params", "opts", "_related_request_id"),
                            diagnostic,
                            "client_dispatch_wait",
                        )
                    )
                await session.initialize()
                initialized_ms = (time.perf_counter() - started) * 1000
                tool_list = await session.list_tools()
                schemas = [item.model_dump(mode="json") for item in tool_list.tools]
                client_counts = {
                    "initialize": 1,
                    "tools/list": 1,
                    "tools/call": 0,
                    "public_tools/call": 0,
                }

                async def control(
                    name: str, arguments: dict[str, JsonValue] | None = None
                ) -> JsonValue:
                    client_counts["tools/call"] += 1
                    response = await session.call_tool(name, arguments or {})
                    if not isinstance(response, CallToolResult) or response.is_error:
                        raise RuntimeError(f"Owned control failed: {name}: {response}")
                    return parse_output(
                        next(
                            item.text for item in response.content if isinstance(item, TextContent)
                        )
                    )

                async def observe(correlation: str, phase: str) -> None:
                    client_counts["tools/call"] += 1
                    client_counts["public_tools/call"] += 1
                    begin = time.perf_counter()
                    if diagnostic is None:
                        response = await invoke(session, correlation)
                    else:
                        with diagnostic.correlated(correlation):
                            response = await invoke(session, correlation)
                    total = (time.perf_counter() - begin) * 1000
                    value = validate_output(response)
                    digest, size = fingerprint(value)
                    observations.append(
                        Observation(
                            correlation=correlation,
                            workload=correlation.split(":", 1)[0],
                            phase=phase,
                            client_total_ms=total,
                            output_sha256=digest,
                            output_bytes=size,
                        )
                    )

                for workload in WORKLOADS:
                    if diagnostic is not None:
                        await control(
                            "bench_diagnostic_batch", {"workload": workload, "begin": True}
                        )
                        diagnostic_calls += 1
                        cpu_started, wall_started = time.process_time_ns(), time.perf_counter_ns()
                    await observe(f"{workload}:cold", "cold")
                    for index in range(options.warmup):
                        await observe(f"{workload}:warmup:{index}", "warmup")
                    for start in range(0, options.samples, options.concurrency):
                        async with anyio.create_task_group() as group:
                            for index in range(
                                start, min(start + options.concurrency, options.samples)
                            ):
                                group.start_soon(observe, f"{workload}:warm:{index}", "warm")
                    if diagnostic is not None:
                        client_cpu_ms = (time.process_time_ns() - cpu_started) / 1e6
                        client_wall_ms = (time.perf_counter_ns() - wall_started) / 1e6
                        child = await control(
                            "bench_diagnostic_batch", {"workload": workload, "begin": False}
                        )
                        diagnostic_calls += 1
                        cpu_batches.append(
                            {
                                "workload": workload,
                                "client_process_cpu_ms": client_cpu_ms,
                                "client_wall_ms": client_wall_ms,
                                "child": child,
                                "client_scope": "whole_client_process_including_harness_parity_hash_excluding_batch_control_waits",
                            }
                        )

                async def pending_call(
                    *, task_status: anyio.abc.TaskStatus[anyio.CancelScope]
                ) -> None:
                    with anyio.CancelScope() as scope:
                        task_status.started(scope)
                        client_counts["tools/call"] += 1
                        client_counts["public_tools/call"] += 1
                        validate_output(await invoke(session, "small:cancel"))

                async with anyio.create_task_group() as group:
                    cancelled = await group.start(pending_call)
                    await control("bench_wait_active", {"correlation": "small:cancel"})
                    cancel_started = time.perf_counter()
                    cancelled.cancel()
                cancel_return_ms = (time.perf_counter() - cancel_started) * 1000
                # Synthetic peer work is intentionally non-cancellable. Observe late
                # result cleanup and recovery after its declared 500ms delay.
                await control("bench_wait_complete", {"correlation": "small:cancel"})
                await observe("small:after_cancel", "recovery")
                cancel_status = await control("bench_wait_drained")
                reconnect_started = time.perf_counter()
                await control("bench_reconnect")
                await observe("small:after_reconnect", "recovery")
                reconnect_ms = (time.perf_counter() - reconnect_started) * 1000
                final_status = await control("bench_wait_drained")
                lifecycle: dict[str, JsonValue] = {
                    "cancel_requested": cancelled.cancel_called,
                    "cancel_return_ms": cancel_return_ms,
                    "after_cancel": cancel_status,
                    "reconnect_and_first_call_ms": reconnect_ms,
                    "after_reconnect": final_status,
                }
                if mode == "http":
                    await control("bench_arm_partial")

                    async def partial_call(
                        *, task_status: anyio.abc.TaskStatus[anyio.CancelScope]
                    ) -> None:
                        with anyio.CancelScope() as scope:
                            task_status.started(scope)
                            client_counts["tools/call"] += 1
                            client_counts["public_tools/call"] += 1
                            validate_output(await invoke(session, "large:partial_cancel"))

                    async with anyio.create_task_group() as group:
                        partial_scope = await group.start(partial_call)
                        held = await control("bench_partial_status")
                        if not isinstance(held, dict) or held.get("assembler_buffer_bytes", 0) <= 0:
                            raise RuntimeError("Partial transfer was not actually admitted")
                        partial_scope.cancel()
                    after_partial = await control("bench_wait_drained")
                    await control("bench_release_partial")
                    completed = await control(
                        "bench_wait_complete", {"correlation": "large:partial_cancel"}
                    )
                    if (
                        not isinstance(completed, dict)
                        or completed.get("receiver_final_chunk_processed") is not True
                    ):
                        raise RuntimeError("Late chunks have no receiver-side completion proof")
                    after_late = await control("bench_wait_drained")
                    lifecycle["partial_cancel"] = {
                        "held": held,
                        "after_cancel": after_partial,
                        "receiver_final_chunk_processed": True,
                        "after_late_chunks": after_late,
                    }
                resource_results = (
                    await observe_resources(session, control, client_counts)
                    if options.resource_contract and mode == "http"
                    else None
                )
                metadata = await control("bench_metadata")
        finally:
            try:
                if process is not None:
                    await cleanup_process(process)
            finally:
                evidence = options.output.parent / (options.output.stem + "-traces")
                evidence.mkdir(parents=True, exist_ok=True)
                errors.flush()
                filenames = ("peer.jsonl", "queue.jsonl", "active.jsonl", "server.stderr.log")
                if diagnostic is not None:
                    filenames += ("diagnostic-child.json",)
                    write_sidecar(evidence / f"{mode}-diagnostic-client.json", diagnostic.export())
                for filename in filenames:
                    source = directory / filename
                    if source.exists():
                        (evidence / f"{mode}-{filename}").write_bytes(source.read_bytes())
    observations = attach_stages(observations, directory)
    result = {
        "mode": mode,
        "cold_launch_to_initialized_ms": initialized_ms,
        "cold_first_calls": {
            item.workload: item.client_total_ms for item in observations if item.phase == "cold"
        },
        "warmed": summarize(observations),
        "observations": [item.model_dump(mode="json") for item in observations],
        "lifecycle": lifecycle,
        "tool_schemas": schemas,
        "client_rpc_counts": client_counts,
        "fixture_metadata": metadata,
        "native_child_interpreter": command[0],
        "text_structured_parity": True,
        "resource_contract": resource_results,
    }
    if diagnostic is not None:
        result["diagnostic"] = {
            "mode": mode,
            "client": diagnostic.export(),
            "child": json.loads((directory / "diagnostic-child.json").read_text(encoding="utf-8")),
            "cpu_batches": cpu_batches,
            "diagnostic_control_rpc_count": diagnostic_calls,
            "normal_tools_call_count": client_counts["tools/call"] - diagnostic_calls,
        }
    return result


async def run(options: Options) -> dict[str, JsonValue]:
    if options.diagnostic and (options.samples > 30 or options.warmup > 3):
        raise DiagnosticError(
            "Diagnostic captures are bounded to 30 samples and 3 warmups per workload"
        )

    def source_hashes() -> dict[str, str]:
        product_paths = (options.product_root / "Server/src").rglob("*.py")
        sources = {
            path.relative_to(options.product_root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in product_paths
        }
        harness_paths = tuple((ROOT / "tools").glob("bench_transport*.py")) + tuple(
            FIXTURE.rglob("*.py")
        )
        sources.update(
            {
                "harness:" + path.relative_to(ROOT).as_posix(): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in harness_paths
            }
        )
        return sources

    sources_before = source_hashes()
    with tempfile.TemporaryDirectory(prefix="unity-mcp-transport-bench-") as temporary:
        directory = Path(temporary)
        modes = ("stdio", "http") if options.order == "stdio-first" else ("http", "stdio")
        results = [await measure_mode(mode, options, directory / mode) for mode in modes]
    groups = [
        [Observation.model_validate(raw) for raw in result["observations"]] for result in results
    ]
    equivalent = equivalent_outputs(groups)
    sources_after = source_hashes()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    result: dict[str, JsonValue] = {
        "schema_id": "unity-mcp-transport-bench-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                name: importlib.metadata.version(name)
                for name in (
                    "fastmcp",
                    "mcp",
                    "anyio",
                    "uvicorn",
                    "httpx2",
                    "websockets",
                    "pydantic",
                    "pydantic-core",
                )
            },
        },
        "options": options.model_dump(
            mode="json", exclude={"diagnostic"} if not options.diagnostic else set()
        ),
        "output_equivalent": equivalent,
        "results": results,
        "peer_profile": {
            "stdio_authentication": "reciprocal_hmac_v2",
            "http_capabilities": ["large_result_v1"],
            "http_gzip_negotiated": False,
            "editor_execution": "owned_synthetic_peer",
        },
        "source": {
            "harness_repository_head": head,
            "asserted_product_revision": options.product_revision,
            "comparison_kind": "stdio_vs_local_http_selected_product",
            "unchanged_during_measurement": sources_before == sources_after,
            "sha256_before": sources_before,
            "sha256_after": sources_after,
        },
        "limits": [
            "Owned synthetic Unity peers; no actual Editor execution or Editor main-thread timing.",
            "HTTP peer negotiates plain large_result_v1 chunks only; large_result_gzip_v1 is excluded, so captures do not measure the new gzip path.",
            "UnityMCP subset uses actual product stdio adapter and response limits; main import included, full catalog registration/discovery excluded.",
            "Accounted bytes are reservations, not RSS or all transient allocations; stdio shared-read scope cannot prove legacy/SDK allocation peaks.",
            "State workload sends get_editor_state through product routing, with a deterministic synthetic state envelope; resource formatting/state push cache excluded.",
            "Queue: actual legacy connection admission plus synthetic peer admission; other framework scheduling belongs to residual.",
            "Serialization: synthetic peer payload construction/JSON encoding only; MCP encoding and decode remain in residual.",
            "Residual combines TCP/WS/MCP wire, socket writes, framework scheduling, response validation/formatting and client handling; cannot identify pure network cost.",
            "Job workload measures terminal public get_test_job polling, excluding test execution/job start and multi-second waiting.",
            "Cancellation proves client return, pending-result cleanup and subsequent operation; synthetic/legacy Unity work can continue.",
            "Reconnect is deliberate peer replacement inside the same MCP session; HTTP process restart/client session resumption excluded.",
            "Nearest-rank p95/p99 have low confidence at small sample counts; cold startup is one observation per mode.",
        ],
    }
    options.output.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_complete = True
    if options.diagnostic:
        result["data_kind"] = "diagnostic"
        sidecar = {
            "data_kind": "diagnostic",
            "schema_id": "unity-mcp-transport-diagnostic-v1",
            "runtime": result["runtime"],
            "source": result["source"],
            "options": result["options"],
            "modes": [row.pop("diagnostic") for row in results],
            "limits": [
                "Overlapping local spans must not be added or subtracted across processes.",
                "Diagnostic wrapper/recorder overhead is included; these timings are not natural latency.",
                "Client batch CPU includes harness parity/hash verification; child batch CPU includes all threads, synthetic peers and begin/end control work through peer completion/accounting drain.",
                "Await spans are elapsed time, never per-request CPU. Only synchronous write/validation spans have current-thread CPU.",
                "CPU clock observations can be quantized on Windows; zero or tiny samples are not proof of zero CPU work.",
                "Child snapshot_scope distinguishes pre-metadata snapshots from graceful/error-finally snapshots; a hard OS kill may prevent a final or early child snapshot.",
                "Stdio write/flush events are uncorrelated worker spans; handoff events carry typed RPC IDs.",
                "Envelope validation samples one product middleware alias, not every product validation site.",
            ],
        }
        sidecar_path = options.output.with_name(options.output.stem + "-diagnostic.json")
        sidecar_written = write_sidecar(sidecar_path, sidecar)
        diagnostic_complete = sidecar_written and all(
            mode[process_name]["complete"]
            for mode in sidecar["modes"]
            for process_name in ("client", "child")
        )
        result["diagnostic_sidecar"] = {
            "path": str(sidecar_path),
            "sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
        }
    options.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not diagnostic_complete:
        raise DiagnosticError(
            "Diagnostic event/byte overflow; bounded evidence saved after cleanup"
        )
    if not equivalent:
        raise RuntimeError("Product transport outputs differ; see saved report")
    if sources_before != sources_after:
        raise RuntimeError("Sources changed during measurement; rerun against a stable checkout")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--large-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--work-ms", type=float, default=2)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--order", choices=("stdio-first", "http-first"), default="stdio-first")
    parser.add_argument("--product-root", type=Path, default=ROOT)
    parser.add_argument("--product-revision", default="current-checkout")
    parser.add_argument(
        "--cohort-gate",
        action="store_true",
        help="Owned event rendezvous for deterministic concurrent readiness tests",
    )
    parser.add_argument(
        "--resource-contract",
        action="store_true",
        help="Separate held HTTP ordinary/authoritative resource CI contract",
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Separate bounded diagnostic sidecar; never natural timing evidence",
    )
    options = Options.model_validate(vars(parser.parse_args()))
    options = options.model_copy(update={"product_root": options.product_root.resolve()})
    if not (options.product_root / "Server/src/main.py").is_file():
        parser.error("product-root must contain Server/src/main.py")
    if (
        options.samples < 1
        or options.warmup < 0
        or not 262144 <= options.large_bytes <= 8 * 1024 * 1024
        or options.work_ms < 0
        or options.concurrency < 1
    ):
        parser.error(
            "Use positive samples/concurrency, nonnegative warmup/work-ms, and large-bytes in 256KiB..8MiB"
        )
    anyio.run(run, options)
    print(options.output)
