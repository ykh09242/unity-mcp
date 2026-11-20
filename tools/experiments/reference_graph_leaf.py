"""Measure cold and one-leaf publication in a retained production SQLite graph."""

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import sys
from threading import Event
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Server/src"))

from services.reference_graph.reconcile import cancellable_thread
from services.reference_graph.store import (
    Edge,
    GraphScope,
    GraphStore,
    Observation,
    check_graph_work,
)


def source_name(source):
    return f"source:{source:08d}"


def edges(source, version):
    for index in range(100):
        target = f"target:{index:03d}"
        if index == 99 and source == 0:
            target += f":{version}"
        yield Edge(target, "fixture")


def snapshot(store):
    """Hash every published semantic row in a stable order, including its payload."""
    digest = hashlib.sha256()
    count = 0
    with store._connect() as db:
        for row in db.execute(
            "SELECT owner,provider,target,kind,payload FROM edges ORDER BY owner,target"
        ):
            digest.update(json.dumps(list(row), separators=(",", ":")).encode() + b"\n")
            count += 1
    return {"edges": count, "sha256": digest.hexdigest()}


def measure(store, sources, version):
    connections = store._connect
    counts = {"write_transactions": 0, "sqlite_row_changes": 0, "published_sources": 0}

    @contextmanager
    def counted(*args, **kwargs):
        with connections(*args, **kwargs) as db:
            before = db.total_changes
            yield db
            if kwargs.get("write"):
                counts["write_transactions"] += 1
                counts["sqlite_row_changes"] += db.total_changes - before

    store._connect = counted
    start = time.perf_counter()
    try:
        for offset in range(0, len(sources), 64):
            batch = sources[offset : offset + 64]
            generations = store.observe_batch(
                Observation(source_name(i), "unity", str(version if i == 0 else 0)) for i in batch
            )
            published = store.publish_batch(
                (source_name(i), "unity", generation, edges(i, version))
                for i, generation in zip(batch, generations, strict=True)
            )
            if not all(published):
                raise AssertionError("Fixture publication was refused")
            counts["published_sources"] += len(published)
    finally:
        store._connect = connections
    return {
        **counts,
        "elapsed_ms": (time.perf_counter() - start) * 1000,
        "database_bytes": store.path.stat().st_size,
    }


async def cancel_leaf(store):
    """Cancel after SQLite has accepted one private replacement edge."""
    generation = store.observe(source_name(0), "unity", "cancel")
    entered = Event()

    def replacement():
        yield Edge("uncommitted", "fixture")
        entered.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            check_graph_work()
            time.sleep(0.001)
        raise TimeoutError("Fixture cancellation did not reach the storage worker")

    task = asyncio.create_task(
        cancellable_thread(store.publish, source_name(0), "unity", generation, replacement())
    )
    try:
        if not await asyncio.to_thread(entered.wait, 10):
            raise TimeoutError("Fixture never entered the publication")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return generation


async def run(root, count, repeats):
    scope = GraphScope("local:benchmark", "editor", "project")
    live = GraphStore.open(root / "incremental", scope)
    cold = measure(live, range(count), 0)
    original = snapshot(live)
    if original["edges"] != 100 * count:
        raise AssertionError("Cold fixture has missing or extra edges")
    samples = []
    previous = original
    for version in range(1, repeats + 1):
        incremental = measure(live, [0], version)
        observed = snapshot(GraphStore.open(root / "incremental", scope))
        full = GraphStore.open(root / f"full-{version}", scope)
        rebuilt = measure(full, range(count), version)
        if snapshot(full) != observed or observed == previous or observed["edges"] != count * 100:
            raise AssertionError("Leaf update and fresh full rebuild disagree")
        samples.append({"incremental": incremental, "full": rebuilt, "published": observed})
        previous = observed
    generation = await cancel_leaf(live)
    reopened = GraphStore.open(root / "incremental", scope)
    after_cancel = snapshot(reopened)
    pending = [job for job in reopened.pending() if job["owner"] == source_name(0)]
    if after_cancel != previous or len(pending) != 1 or pending[0]["generation"] != generation:
        raise AssertionError("Cancellation damaged the retained graph or lost the pending edit")
    return {
        "sources": count,
        "edges_per_source": 100,
        "cold": cold,
        "original": original,
        "samples": samples,
        "cancelled": {"published": after_cancel, "pending_leaf_generation": generation},
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="Fresh directory for retained graphs and results"
    )
    parser.add_argument("--sources", nargs="+", type=int, default=[10, 1000])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if min(args.sources) < 1 or len(set(args.sources)) != len(args.sources) or args.repeats < 1:
        parser.error("Use distinct positive source counts and a positive repetition count")
    args.output.mkdir(parents=True, exist_ok=False)
    result = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "scope": "Known synthetic graph inputs supplied to production observe/publish methods. Times exclude graph schema creation, input discovery and result verification.",
        "fixtures": [
            await run(args.output / str(count), count, args.repeats) for count in args.sources
        ],
    }
    (args.output / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
