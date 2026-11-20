"""Time validated reads of an explicitly named scene through a selected local MCP transport."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "Server/src")]
from tools.experiments.mcp_client_assurance import MODERN_VERSION, official_http_client


async def measure(args):
    if args.endpoint:
        connection = official_http_client(args.endpoint, "modern")
    else:
        server = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "Server/src/main.py"), "--transport", "stdio"],
            env={**os.environ, "PYTHONPATH": str(ROOT / "Server/src")},
        )
        connection = Client(
            stdio_client(server), mode=MODERN_VERSION, cache=None, read_timeout_seconds=30
        )
    started = time.perf_counter()
    async with connection as client:
        entered = time.perf_counter()
        samples = []
        first = None
        for _ in range(args.samples + 1):
            begin = time.perf_counter()
            result = await client.call_tool(
                "scene_inspect", {"action": "get_active", "unity_instance": args.instance}
            )
            payload = result.structured_content
            if result.is_error or not payload or payload.get("success") is not True:
                raise ValueError("The selected scene read failed")
            scene = payload["data"]
            if scene.get("path") != args.scene or scene.get("isLoaded") is not True:
                raise ValueError("The selected scene differs from the expected loaded scene")
            encoded = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
            digest = hashlib.sha256(encoded).hexdigest()
            verified = time.perf_counter()
            samples.append(
                {"ms": (verified - begin) * 1000, "bytes": len(encoded), "sha256": digest}
            )
            if len(samples) == 1:
                first = (verified - started) * 1000
        protocol = client.protocol_version
    warmed = [row["ms"] for row in samples[1:]]
    return {
        "transport": "http" if args.endpoint else "stdio",
        "protocol": protocol,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "instance": args.instance,
        "expected_scene": args.scene,
        "client_context_entered_ms": (entered - started) * 1000,
        "connect_to_first_verified_ms": first,
        "initialize_rpc_calls": None,
        "public_scene_calls": len(samples),
        "first": samples[0],
        "warm": samples[1:],
        "warm_median_ms": statistics.median(warmed),
        "warm_p95_ms": sorted(warmed)[math.ceil(0.95 * len(warmed)) - 1]
        if len(warmed) >= 20
        else None,
        "byte_scope": "Canonical UTF-8 structured result. HTTP headers, stdio envelopes and other wire bytes unmeasured.",
        "native_queue_ms": None,
        "native_execution_ms": None,
        "delivery_ms": None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--endpoint", help="Existing loopback MCP endpoint")
    target.add_argument("--stdio", action="store_true", help="Start the repository server on stdio")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.samples < 1:
        parser.error("Supply a positive warm sample count")
    report = anyio.run(measure, options)
    with options.output.open("x", encoding="utf-8") as dest:
        json.dump(report, dest, indent=2)
        dest.write("\n")
