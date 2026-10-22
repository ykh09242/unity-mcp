"""Measure actual SharedToolReads admission, fan-out, isolation and cleanup."""

import asyncio
import gc
import hashlib
import os
import json
import platform
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "Server/src"))
from services.tools.immutable_json import prepare_json_copy
from services.tools.shared_tool_reads import SharedToolReads
from services.tools.shared_read_budget import shared_read_budget


class LoopCallbackObservation:
    """Constant-state scheduling observer; owns exactly one pending callback."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.count = 0
        self.maximum_gap_ms = None
        self.previous = None
        self.handle = None
        self.closed = False
        self.tick = asyncio.Event()

    def _observe(self):
        if self.closed:
            return
        now = self.loop.time()
        if self.previous is not None:
            gap = (now - self.previous) * 1000
            self.maximum_gap_ms = max(self.maximum_gap_ms or 0, gap)
        self.previous = now
        self.count += 1
        self.tick.set()
        self.handle = self.loop.call_later(0.001, self._observe)

    async def start(self):
        self.handle = self.loop.call_later(0.001, self._observe)
        try:
            await self.tick.wait()
        except BaseException:
            self.close()
            raise

    async def finish(self):
        try:
            self.tick.clear()
            await self.tick.wait()
        finally:
            self.close()
        return {
            "callback_count": self.count,
            "maximum_gap_ms": self.maximum_gap_ms,
            "observer_released": self.closed and self.handle is None,
        }

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None


async def measure(arm, value, readers, memory=False, callback_metrics=None):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return value

    reads = SharedToolReads(freshness_s=1, copy_preparer=prepare_json_copy if arm == "B" else None)
    before = shared_read_budget.retained_bytes
    gc.collect()
    observer = LoopCallbackObservation() if callback_metrics is not None else None
    try:
        if observer is not None:
            await observer.start()
        if memory:
            tracemalloc.start()
        start = time.perf_counter_ns()
        async with reads.session("owned-benchmark") as shared:
            copies = await asyncio.gather(*(shared.fetch(factory) for _ in range(readers)))
        ms = (time.perf_counter_ns() - start) / 1e6
        peak = tracemalloc.get_traced_memory()[1] if memory else None
        if observer is not None:
            callback_metrics.update(await observer.finish())
    finally:
        if observer is not None:
            observer.close()
        if memory:
            tracemalloc.stop()
    assert calls == 1 and shared_read_budget.retained_bytes == before
    assert copies == [value] * readers
    copies[0]["data"]["rows"][0]["name"] = "changed"
    assert value["data"]["rows"][0]["name"] != "changed"
    if readers > 1:
        assert copies[1]["data"]["rows"][0]["name"] != "changed"
    return ms, peak


def physical_memory_mb():
    if os.name != "nt":
        query = getattr(os, "sysconf")
        return query("SC_PHYS_PAGES") * query("SC_PAGE_SIZE") // (1024 * 1024)
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong)
            for name in (
                "total_physical",
                "available_physical",
                "total_page",
                "available_page",
                "total_virtual",
                "available_virtual",
                "available_extended",
            )
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    query = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True).GlobalMemoryStatusEx
    query.argtypes = [ctypes.POINTER(MemoryStatus)]
    query.restype = wintypes.BOOL
    if not query(ctypes.byref(status)):
        raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
    return status.total_physical // (1024 * 1024)


async def main():
    sys.path.insert(0, str(ROOT))
    from tools.workload_cohorts import server_fingerprint, harness_fingerprint

    source_hash, harness_hash = server_fingerprint(), harness_fingerprint()
    results = []
    cohorts = []

    environment = {
        "editor": "not_applicable",
        "backend": "CPython " + platform.python_version(),
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "processors": os.cpu_count(),
        "memory_mb": physical_memory_mb(),
        "gpu": "not_applicable",
    }
    for shape, count, text in [
        ("small", 4, "status"),
        ("ascii", 4000, "x" * 64),
        ("unicode", 4000, "한글😀" * 16),
    ]:
        value = {
            "success": True,
            "data": {
                "rows": [
                    {
                        "id": i,
                        "name": text + str(i),
                        "ok": i % 2 == 0,
                        "values": [i, -0.0, 1.2345678901234567, None],
                    }
                    for i in range(count)
                ]
            },
        }
        for readers in (1, 4, 16):
            for arm in "AB":
                await measure(arm, value, readers)
            samples = {"A": [], "B": []}
            for arm in "AABBAABBAABB":
                ms, _ = await measure(arm, value, readers)
                samples[arm].append(ms)
            peaks = {arm: (await measure(arm, value, readers, True))[1] for arm in "AB"}
            callbacks = {"A": [], "B": []}
            for pass_index in range(7):
                for arm in "AB":
                    observation = {"pass_index": pass_index}
                    await measure(arm, value, readers, callback_metrics=observation)
                    callbacks[arm].append(observation)
            row = {
                "shape": shape,
                "readers": readers,
                "ms": samples,
                "median_ms": {a: statistics.median(s) for a, s in samples.items()},
                "traced_peak_bytes": peaks,
            }
            results.append(row)
            output_hash = hashlib.sha256(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
            for arm in "AB":
                cohorts.append(
                    {
                        "schema": "unity-mcp-workload-raw-v1",
                        "server_sha256": source_hash,
                        "harness_sha256": harness_hash,
                        "scenario": "concurrent_readers",
                        "variant": f"{shape}-{readers}-{arm}",
                        "expected_units": readers,
                        "completed": True,
                        "environment": environment,
                        "samples": [
                            {
                                "preparation": "warm",
                                "elapsed_ms": ms,
                                "completed_units": readers,
                                "output_sha256": output_hash,
                            }
                            for ms in samples[arm]
                        ],
                        "traced_peak_bytes": peaks[arm],
                        "allocation_bytes": None,
                        "retained_managed_delta_bytes": None,
                        "retained_owner_bytes_delta": 0,
                        "main_thread_stall_ms": None,
                        "event_loop_callback_gap_ms": max(
                            p["maximum_gap_ms"] for p in callbacks[arm]
                        ),
                        "python_observation": {
                            "schema": "python-traced-and-callback-v1",
                            "tracemalloc": {
                                "metric": "python_traced_peak_bytes",
                                "scope": "python_allocations",
                                "phase": "separate_pass",
                                "peak_bytes": peaks[arm],
                            },
                            "callbacks": callbacks[arm],
                        },
                    }
                )
            print(
                json.dumps(
                    {k: row[k] for k in ("shape", "readers", "median_ms", "traced_peak_bytes")}
                ),
                flush=True,
            )
    output = Path(
        os.environ.get("UNITY_MCP_WORKLOAD_OUTPUT", ROOT / ".work/service-optimization-probes")
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "shared-read.json").write_text(
        json.dumps({"python": platform.python_version(), "results": results}, indent=2),
        encoding="utf-8",
    )

    assert (source_hash, harness_hash) == (server_fingerprint(), harness_fingerprint())
    (output / "concurrent-readers.json").write_text(json.dumps(cohorts, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
