"""Measure real SDK inventories and selection updates without contacting Unity.

Run with the repository's existing Server Python environment and PYTHONPATH=Server/src.
This is an in-memory SDK/selection benchmark; it does not measure network or Unity latency.
"""

# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp==4.1.3"]
# ///
# How to run: python tools/experiments/discovery_selection.py > results.json

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import gc
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import statistics
import time
import tracemalloc
from typing import NotRequired, TypedDict

import anyio
from fastmcp import Client, Context, FastMCP
from fastmcp.tools import Tool
from key_value.aio._utils.managed_entry import ManagedEntry

from core.config import config
from models.models import ToolDefinitionModel
from services.registry import get_group_tool_names
from services.tools import register_all_tools
import services.tool_discovery
from transport.session_state_store import SessionStateStore
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_instance_middleware import UnityInstanceMiddleware


class Measurement(TypedDict):
    p50_us: float
    p95_us: float
    peak_traced_bytes_per_batch: int
    retained_traced_bytes_after_five_batches: int
    operations_per_batch: int
    parity: str
    store_writes: NotRequired[int]


class CatalogMeasurement(TypedDict):
    custom_tools: int
    full_tool_count: int
    full_listing: Measurement
    noop_update: NotRequired[Measurement]
    project_listing: NotRequired[Measurement]
    concurrent_listing: NotRequired[Measurement]
    retained_selection_entries: NotRequired[int]
    retained_selection_bytes: NotRequired[int]
    revision_revocation_expiry_parity: NotRequired[bool]


class CountingStore(SessionStateStore):
    """Count writes while preserving the production serializer and expiration."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    async def _put_managed_entry(
        self, *, collection: str, key: str, managed_entry: ManagedEntry
    ) -> None:
        self.writes += 1
        await super()._put_managed_entry(
            collection=collection, key=key, managed_entry=managed_entry
        )


async def measure(operation: Callable[[], Awaitable[str]], *, divisor: int = 1) -> Measurement:
    """Time without tracing, then observe peak traced bytes and retained memory."""
    expected = await operation()
    for _ in range(4):
        assert await operation() == expected
    timings = []
    for _ in range(31):
        started = time.perf_counter_ns()
        assert await operation() == expected
        timings.append((time.perf_counter_ns() - started) / 1000 / divisor)
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    peaks = []
    for _ in range(5):
        tracemalloc.reset_peak()
        current = tracemalloc.get_traced_memory()[0]
        assert await operation() == expected
        peaks.append(tracemalloc.get_traced_memory()[1] - current)
    await anyio.sleep(0)
    gc.collect()
    retained = tracemalloc.get_traced_memory()[0] - before
    tracemalloc.stop()
    return {
        "p50_us": round(statistics.median(timings), 3),
        "p95_us": round(sorted(timings)[29], 3),
        "peak_traced_bytes_per_batch": max(peaks),
        "retained_traced_bytes_after_five_batches": retained,
        "operations_per_batch": divisor,
        "parity": expected,
    }


async def custom_probe(value: str = "") -> str:
    """Return the supplied value without accessing any external state."""
    return value


async def run_catalog(size: int) -> CatalogMeasurement:
    """Exercise real SDK serialization, transforms, project scopes and owner concurrency."""
    os.environ["UNITY_MCP_TOOL_PROFILE"] = "full"
    store = CountingStore()

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        async with store.lifespan():
            yield

    server = FastMCP("discovery-benchmark", session_state_store=store, lifespan=lifespan)
    server._unity_session_state_store = store
    register_all_tools(server)
    discovery: services.tool_discovery.ToolDiscovery = getattr(server, "_tool_discovery")
    registry = PluginRegistry()
    PluginHub.configure(registry, mcp=server)
    middleware = UnityInstanceMiddleware()
    allowed = [ToolDefinitionModel(name=name) for name in discovery.definitions]
    for session, project, project_hash in (("one", "First", "first"), ("two", "Second", "second")):
        await registry.register(session, project, project_hash, "benchmark")
        await registry.register_tools_for_session(session, allowed)
    optional_names = get_group_tool_names()["vfx"]
    for index in range(size):
        server.add_tool(Tool.from_function(custom_probe, name=f"custom_{index:05d}"))

    async def select_scope(ctx: Context, project: str, owner: str) -> None:
        # Synthetic trusted routing boundary. Production authentication/availability
        # are covered separately; no caller-supplied identity is trusted by product code.
        await ctx.set_state("unity_instance", project, serializable=False)
        await ctx.set_state("user_id", owner, serializable=False)

    @server.tool
    async def benchmark_update(ctx: Context, repeat: int = 1) -> dict:
        await select_scope(ctx, "First@first", "owner-one")
        for _ in range(repeat):
            await discovery.update(ctx, optional_names, enabled=False)
        return {"entries": store.retained_entries, "writes": store.writes}

    @server.tool
    async def benchmark_listing(ctx: Context, project: str, owner: str) -> str:
        await select_scope(ctx, project, owner)
        tools = await server.list_tools(run_middleware=False)
        tools = await middleware.filter_tools_for_context(ctx, tools)
        return hashlib.sha256("\n".join(sorted(tool.name for tool in tools)).encode()).hexdigest()

    async with Client(server, cache=False) as client:

        async def inventory() -> str:
            tools = await client.list_tools()
            return hashlib.sha256(
                json.dumps(
                    [tool.model_dump(mode="json") for tool in tools], sort_keys=True
                ).encode()
            ).hexdigest()

        result: CatalogMeasurement = {
            "custom_tools": size,
            "full_tool_count": len(await client.list_tools()),
            "full_listing": await measure(inventory),
        }
        await client.call_tool("benchmark_update")
        writes_before = store.writes

        async def noop() -> str:
            response = await client.call_tool("benchmark_update", {"repeat": 200})
            assert response.structured_content["entries"] == 1
            return "one-scoped-selection"

        result["noop_update"] = await measure(noop, divisor=200)
        result["noop_update"]["store_writes"] = store.writes - writes_before

        async def listing(project: str, owner: str) -> str:
            response = await client.call_tool(
                "benchmark_listing", {"project": project, "owner": owner}
            )
            return response.data

        selected = await listing("First@first", "owner-one")
        other_project = await listing("Second@second", "owner-one")
        other_owner = await listing("First@first", "owner-two")
        assert selected != other_project and other_project == other_owner
        result["project_listing"] = await measure(lambda: listing("First@first", "owner-one"))

        async def concurrent() -> str:
            observed = {}

            async def one(project: str, owner: str) -> None:
                observed[(project, owner)] = await listing(project, owner)

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(one, "First@first", "owner-one")
                tasks.start_soon(one, "Second@second", "owner-one")
                tasks.start_soon(one, "First@first", "owner-two")
            assert observed[("First@first", "owner-one")] == selected
            assert observed[("Second@second", "owner-one")] == other_project
            assert observed[("First@first", "owner-two")] == other_owner
            return "isolated-three-owner-project-listings"

        result["concurrent_listing"] = await measure(concurrent)
        result["retained_selection_entries"] = store.retained_entries
        result["retained_selection_bytes"] = store.retained_bytes
        await registry.register_tools_for_session(
            "one", [tool for tool in allowed if tool.name != "read_console"]
        )
        assert await listing("First@first", "owner-one") != selected
        await registry.register_tools_for_session("one", allowed)
        assert await listing("First@first", "owner-one") == selected
        store._clock = lambda: time.time() + 86401
        store.expire()
        assert await listing("First@first", "owner-one") == other_project
        assert store.retained_entries == 0
        result["revision_revocation_expiry_parity"] = True
    await PluginHub.shutdown()
    assert store.retained_entries == 0
    return result


async def main() -> None:
    """Emit machine-readable observations; timing and memory are separate runs."""
    expected_source = Path(__file__).resolve().parents[2] / "Server/src/services/tool_discovery.py"
    assert Path(services.tool_discovery.__file__).resolve() == expected_source
    assert services.tool_discovery.is_reviewed_sdk()
    logging.disable(logging.WARNING)
    config.http_remote_hosted = False
    config.transport_mode = "http"
    empty = FastMCP("empty-catalog")
    async with Client(empty, cache=False) as client:

        async def empty_listing() -> str:
            assert await client.list_tools() == []
            return "empty"

        empty_result = await measure(empty_listing)
    results = {
        "python": platform.python_version(),
        "fastmcp": "4.1.3",
        "notes": "In-memory SDK; no network/native latency. Peak is transient high-water, not cumulative allocations. No permission cache.",
        "empty_listing": empty_result,
        "catalogs": [await run_catalog(0), await run_catalog(2000)],
    }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    anyio.run(main)
