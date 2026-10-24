"""Compare synchronous final admission with bounded worker admission.

Run using the existing Server Python environment from the repository root:
    python tools/experiments/response_admission.py --output /path/to/results.json

Rejected cases append nonfinite metadata after the workload, retaining actual
production limits. Payload construction and parity serialization are untimed.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Server" / "src"))

from fastmcp.tools.base import ToolResult
from mcp_types import CallToolResult, TextContent

from services.tools.response_work import needs_response_worker
from transport.response_limit_middleware import ResponseLimitMiddleware


def make_result(kind, rows, rejected):
    graph = {"rows": [{"index": n, "name": f"item-{n}", "enabled": True} for n in range(rows)]}
    content = [] if kind == "structured" else [TextContent(text=json.dumps(graph))]
    structured = None if kind == "text" else graph
    meta = {"invalid": float("nan")} if rejected else None
    if kind == "raw":
        return ToolResult.from_mcp_result(
            CallToolResult(content=content, structured_content=structured, _meta=meta)
        )
    return ToolResult(content=content, structured_content=structured, meta=meta)


def projection(result):
    raw = result._raw_mcp_result
    if raw is None:
        raw = CallToolResult(
            content=result.content,
            structured_content=result.structured_content,
            _meta=result.meta,
            is_error=result.is_error,
        )
    return raw.model_dump_json(by_alias=True, exclude_none=True)


async def measure(operation):
    loop = asyncio.get_running_loop()
    delays = []
    active = True
    handle = None

    def heartbeat(expected):
        nonlocal handle
        now = loop.time()
        delays.append(max(0, now - expected))
        if active:
            handle = loop.call_later(0.001, heartbeat, now + 0.001)

    loop.call_soon(heartbeat, loop.time())
    start_cpu, start = time.process_time(), time.perf_counter()
    result = await operation()
    elapsed = time.perf_counter() - start
    cpu = time.process_time() - start_cpu
    await asyncio.sleep(0.003)
    active = False
    if handle is not None:
        handle.cancel()
    return result, {
        "latency_ms": elapsed * 1000,
        "cpu_ms": cpu * 1000,
        "heartbeat_delay_ms": max(delays, default=0) * 1000,
    }


async def benchmark(repeats):
    guard = ResponseLimitMiddleware()
    records = []
    # Start the executor outside timed samples.
    await asyncio.to_thread(lambda: None)
    for kind in ("text", "structured", "raw"):
        for rows in (10, 2_000, 30_000):
            for rejected in (False, True):
                source = make_result(kind, rows, rejected)

                async def call_next(_context, source=source):
                    return source

                async def baseline(source=source):
                    return guard.admit_tool_result(source)

                async def candidate(call_next=call_next):
                    return await guard.on_call_tool(None, call_next)

                expected = projection(await baseline())
                assert projection(await candidate()) == expected
                samples = {"baseline": [], "candidate": []}
                for iteration in range(repeats):
                    order = (
                        ("baseline", "candidate")
                        if iteration % 2 == 0
                        else ("candidate", "baseline")
                    )
                    for name in order:
                        operation = baseline if name == "baseline" else candidate
                        result, sample = await measure(operation)
                        assert result.is_error is rejected
                        assert projection(result) == expected
                        samples[name].append(sample)
                        del result
                memory = {}
                for name, operation in (("baseline", baseline), ("candidate", candidate)):
                    gc.collect()
                    tracemalloc.start()
                    result = await operation()
                    memory[name] = tracemalloc.get_traced_memory()[1]
                    tracemalloc.stop()
                    assert projection(result) == expected
                    del result
                record = {
                    "kind": kind,
                    "rows": rows,
                    "rejected": rejected,
                    "worker": needs_response_worker(source),
                    "output_bytes": len(expected.encode("utf-8")),
                    "samples": samples,
                    "peak_traced_bytes": memory,
                    "median": {
                        name: {
                            metric: statistics.median(item[metric] for item in values)
                            for metric in values[0]
                        }
                        for name, values in samples.items()
                    },
                }
                records.append(record)
                print(
                    json.dumps(
                        {
                            key: record[key]
                            for key in (
                                "kind",
                                "rows",
                                "rejected",
                                "worker",
                                "median",
                                "peak_traced_bytes",
                            )
                        }
                    ),
                    flush=True,
                )
    source = make_result("raw", 30_000, False)

    async def concurrent_call_next(_context):
        return source

    async def concurrent_baseline():
        return [guard.admit_tool_result(source) for _ in range(4)]

    async def concurrent_candidate():
        return await asyncio.gather(
            *(guard.on_call_tool(None, concurrent_call_next) for _ in range(4))
        )

    concurrent = {}
    for name, operation in (("baseline", concurrent_baseline), ("candidate", concurrent_candidate)):
        values = []
        for _ in range(repeats):
            results, sample = await measure(operation)
            assert all(projection(item) == projection(results[0]) for item in results)
            values.append(sample)
        concurrent[name] = values
    small_batches = []
    for kind in ("text", "structured", "raw"):
        source = make_result(kind, 10, False)

        async def batch_call_next(_context, source=source):
            return source

        for name in ("baseline", "candidate", "candidate", "baseline"):
            start_cpu, start = time.process_time(), time.perf_counter()
            for _ in range(1_000):
                if name == "baseline":
                    guard.admit_tool_result(source)
                else:
                    await guard.on_call_tool(None, batch_call_next)
            small_batches.append(
                {
                    "kind": kind,
                    "mode": name,
                    "calls": 1_000,
                    "cpu_ms": (time.process_time() - start_cpu) * 1000,
                    "latency_ms": (time.perf_counter() - start) * 1000,
                }
            )
    return {"cases": records, "concurrent_large_raw": concurrent, "small_batches": small_batches}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    results = asyncio.run(benchmark(args.repeats))
    results["python"] = sys.version
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
