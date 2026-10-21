"""Measure actual SharedToolReads admission, fan-out, isolation and cleanup."""

import asyncio
import gc
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


async def measure(arm, value, readers, memory=False):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return value

    reads = SharedToolReads(freshness_s=1, copy_preparer=prepare_json_copy if arm == "B" else None)
    before = shared_read_budget.retained_bytes
    gc.collect()
    if memory:
        tracemalloc.start()
    start = time.perf_counter_ns()
    async with reads.session("owned-benchmark") as shared:
        copies = await asyncio.gather(*(shared.fetch(factory) for _ in range(readers)))
    ms = (time.perf_counter_ns() - start) / 1e6
    peak = tracemalloc.get_traced_memory()[1] if memory else None
    if memory:
        tracemalloc.stop()
    assert calls == 1 and shared_read_budget.retained_bytes == before
    assert copies == [value] * readers
    copies[0]["data"]["rows"][0]["name"] = "changed"
    assert value["data"]["rows"][0]["name"] != "changed"
    if readers > 1:
        assert copies[1]["data"]["rows"][0]["name"] != "changed"
    return ms, peak


async def main():
    results = []
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
            row = {
                "shape": shape,
                "readers": readers,
                "ms": samples,
                "median_ms": {a: statistics.median(s) for a, s in samples.items()},
                "traced_peak_bytes": peaks,
            }
            results.append(row)
            print(
                json.dumps(
                    {k: row[k] for k in ("shape", "readers", "median_ms", "traced_peak_bytes")}
                ),
                flush=True,
            )
    output = ROOT / ".work/service-optimization-probes"
    output.mkdir(parents=True, exist_ok=True)
    (output / "shared-read.json").write_text(
        json.dumps({"python": platform.python_version(), "results": results}, indent=2),
        encoding="utf-8",
    )


asyncio.run(main())
