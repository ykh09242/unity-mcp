"""Measure a serialization-only lower bound; never enable it in product delivery.

Run with the repository's existing Server environment and PYTHONPATH=Server/src.
Both arms use the installed SDK's wire encoding and actual settled binary writer.
The prepared arm bypasses per-caller mutable result/middleware contracts, so its
timings alone cannot justify a production change.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import gc
import hashlib
import json
from importlib.metadata import version
import platform
from pathlib import Path
import statistics
import time
import tracemalloc

from mcp.types import CallToolResult, JSONRPCResponse, jsonrpc_message_adapter

from services.tools.immutable_json import prepare_json_copy
from transport.stdio_response_delivery import _settled_binary_write


class DigestSink:
    """Consume actual writes without retaining every delivered frame."""

    def __init__(self):
        self.digest = hashlib.sha256()
        self.bytes = 0
        self.flushes = 0

    def write(self, value):
        self.digest.update(value)
        self.bytes += len(value)
        return len(value)

    def flush(self):
        self.flushes += 1


def payload(rows, unicode_text):
    text = "\ud55c\uae00\U0001f600" if unicode_text else "material"
    return {
        "success": True,
        "rows": [
            {
                "id": i,
                "path": f"Assets/{text}/{i}",
                "values": [i, -0.0, 1.25, None, True],
                "label": text * 8,
            }
            for i in range(rows)
        ],
    }


def encode_result(value):
    result = CallToolResult(content=[], structured_content=value)
    return result.model_dump(by_alias=True, exclude_unset=True)


def sdk_frame(value, request_id):
    return jsonrpc_message_adapter.dump_json(
        JSONRPCResponse(jsonrpc="2.0", id=request_id, result=encode_result(value)),
        by_alias=True,
        exclude_unset=True,
    )


def prepare_frames(value):
    """Experimental last-writer bytes; not an SDK SessionMessage replacement."""
    if prepare_json_copy(value) is None:
        return None
    prefix = b'{"jsonrpc":"2.0","id":"owned-template","result":'
    frame = sdk_frame(value, "owned-template")
    if not frame.startswith(prefix) or not frame.endswith(b"}"):
        raise RuntimeError("Installed SDK envelope differs; experiment must be reviewed")
    result = frame[len(prefix) : -1]

    def copy_frame(request_id):
        identity = json.dumps(request_id, ensure_ascii=False, separators=(",", ":")).encode()
        return b'{"jsonrpc":"2.0","id":' + identity + b',"result":' + result + b"}"

    return copy_frame


async def deliver(value, readers, prepared):
    frames = prepare_frames(value) if prepared else None
    copier = prepare_json_copy(value) if not prepared and readers >= 16 else None

    async def one(index):
        sink = DigestSink()
        frame = (
            frames(index) if frames else sdk_frame(copier() if copier else deepcopy(value), index)
        )
        await _settled_binary_write(sink, frame)
        return {"sha256": sink.digest.hexdigest(), "bytes": sink.bytes, "flushes": sink.flushes}

    delivered = await asyncio.gather(*(one(index) for index in range(readers)))
    return {
        "frames": delivered,
        "bytes": sum(row["bytes"] for row in delivered),
        "flushes": sum(row["flushes"] for row in delivered),
    }


async def measure(rows, readers, unicode_text):
    value = payload(rows, unicode_text)
    expected = await deliver(value, readers, False)
    samples = []
    for traced in (False, True):
        for round_index in range(6):
            prepared = round_index % 2 == 1
            gc.collect()
            if traced:
                tracemalloc.start()
            started = time.perf_counter()
            actual = await deliver(value, readers, prepared)
            elapsed = (time.perf_counter() - started) * 1000
            peak = tracemalloc.get_traced_memory()[1] if traced else None
            if traced:
                tracemalloc.stop()
            if actual != expected:
                raise AssertionError("Incomplete or different SDK delivery")
            samples.append(
                {
                    "prepared": prepared,
                    "traced": traced,
                    "round": round_index,
                    "elapsed_ms": elapsed,
                    "traced_peak_bytes": peak,
                }
            )
    return {
        "rows": rows,
        "readers": readers,
        "unicode": unicode_text,
        "delivery": expected,
        "samples": samples,
        "median_ms": {
            str(arm): statistics.median(
                row["elapsed_ms"] for row in samples if row["prepared"] == arm and not row["traced"]
            )
            for arm in (False, True)
        },
        "median_traced_peak_bytes": {
            str(arm): statistics.median(
                row["traced_peak_bytes"]
                for row in samples
                if row["prepared"] == arm and row["traced"]
            )
            for arm in (False, True)
        },
    }


def provenance():
    root = Path(__file__).resolve().parents[3]
    sources = (
        "tools/experiments/service_optimizations/serialization_delivery.py",
        "Server/src/services/tools/immutable_json.py",
        "Server/src/transport/stdio_response_delivery.py",
    )
    return {
        "python": platform.python_version(),
        "os": platform.platform(),
        "packages": {
            name: version(name) for name in ("mcp", "mcp-types", "fastmcp", "pydantic", "anyio")
        },
        "sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources
        },
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    identity = provenance()
    rows = []
    for unicode_text in (False, True):
        for count, readers in ((8000, 16), (25000, 4), (50000, 1)):
            row = await measure(count, readers, unicode_text)
            rows.append(row)
            print(
                json.dumps(
                    {
                        key: row[key]
                        for key in (
                            "rows",
                            "readers",
                            "unicode",
                            "median_ms",
                            "median_traced_peak_bytes",
                        )
                    }
                ),
                flush=True,
            )
    if provenance() != identity:
        raise AssertionError("Measured source or environment changed during the experiment")
    args.output.write_text(
        json.dumps(
            {
                "schema": "serialization-only-lower-bound-v1",
                "provenance": identity,
                "production_adoption": False,
                "scope": "SDK result/stdio writer only; excludes authority, admission, middleware and network",
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
