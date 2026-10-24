"""Read-only official MCP client probe for an explicitly selected local server.

Run with the repository's Server environment and PYTHONPATH=Server/src:
python tools/experiments/mcp_client_assurance.py --endpoint http://127.0.0.1:8080/mcp
  --instance First@hash --instance Second@hash --expected-scene First --expected-scene Second
"""

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from ipaddress import ip_address
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from core.local_auth import LOCAL_AUTH_HEADER, read_local_auth_token

MODERN_VERSION = "2026-07-28"


@asynccontextmanager
async def official_http_client(endpoint: str, mode: str) -> AsyncIterator[Client]:
    """Authenticate only to an explicit loopback endpoint; never print credentials."""
    parsed = urlsplit(endpoint)
    host = parsed.hostname or ""
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if (
        parsed.scheme != "http"
        or not loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Probe requires an HTTP loopback endpoint without credentials or query")
    token = read_local_auth_token(host, parsed.port or 80)
    if not token:
        raise ValueError("Local server authentication credential is unavailable")
    async with httpx2.AsyncClient(
        headers={LOCAL_AUTH_HEADER: token},
        timeout=httpx2.Timeout(30, connect=5),
        trust_env=False,
    ) as http:
        async with Client(
            streamable_http_client(endpoint, http_client=http),
            mode=MODERN_VERSION if mode == "modern" else "legacy",
            read_timeout_seconds=30,
            cache=None,
        ) as client:
            yield client


async def probe(args: argparse.Namespace) -> dict:
    """Reconnect between rounds and verify two independently named native scenes."""
    if len(args.instance) != 2 or len(set(args.instance)) != 2:
        raise ValueError("Supply exactly two distinct instance IDs")
    if args.expected_scene and (
        len(args.expected_scene) != 2 or len(set(args.expected_scene)) != 2
    ):
        raise ValueError("Supply distinct expected scenes, one for each instance")
    modes = ("modern", "legacy") if args.mode == "both" else (args.mode,)
    receipts = []
    started = time.perf_counter()
    for mode in modes:
        for round_number in range(args.rounds):
            async with official_http_client(args.endpoint, mode) as client:
                catalog = await client.list_tools(cache_mode="refresh")
                names = {tool.name for tool in catalog.tools}
                if "manage_scene" not in names:
                    raise AssertionError("Current server profile does not expose manage_scene")
                inventory = await client.read_resource(
                    "mcpforunity://instances", cache_mode="refresh"
                )
                instances = json.loads(inventory.contents[0].text)
                observed = {item["id"] for item in instances["instances"]}
                if not set(args.instance) <= observed:
                    raise AssertionError(
                        "Both explicit targets must be present in server inventory"
                    )
                scenes = []
                for index, instance in enumerate(args.instance):
                    result = await client.call_tool(
                        "manage_scene", {"action": "get_active", "unity_instance": instance}
                    )
                    payload = result.structured_content
                    if result.is_error or not payload or not payload.get("success"):
                        raise AssertionError(f"Read-only scene call failed for target {index + 1}")
                    scene = payload["data"]["name"]
                    if args.expected_scene and scene != args.expected_scene[index]:
                        raise AssertionError(f"Scene identity mismatch for target {index + 1}")
                    scenes.append(scene)
                receipts.append(
                    {
                        "mode": mode,
                        "protocol": client.protocol_version,
                        "round": round_number + 1,
                        "tools": len(names),
                        "scenes": scenes,
                    }
                )
    return {
        "client": "official MCP Python SDK",
        "boundary": "HTTP server to caller-supplied Unity targets",
        "expected_scene_assertions": bool(args.expected_scene),
        "connections": len(receipts),
        "scene_reads": 2 * len(receipts),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "receipts": receipts,
    }


def main() -> None:
    """Emit a credential-free JSON receipt or fail with the relevant assertion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--instance", action="append", required=True)
    parser.add_argument("--expected-scene", action="append")
    parser.add_argument("--mode", choices=("modern", "legacy", "both"), default="both")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10000:
        parser.error("--rounds must be in 1..10000")
    receipt = anyio.run(probe, args)
    payload = json.dumps(receipt, indent=2)
    if args.output is not None:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
