"""Measure synthetic reference-graph work; write only the explicit JSON destination.

Run with the existing Server development environment from the repository root:
    Server/.venv/Scripts/python.exe tools/experiments/reference_graph_bounded_work.py --output results.json

Temporary scopes and JSON files are created under the OS temporary directory and
removed after each workload. No configured graph, source root, or Unity is used.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import time
import tracemalloc


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "Server/src"), str(ROOT / "Server")]

from services.reference_graph import reconcile as reconciliation
from services.reference_graph.reconcile import Reconciler, drained_thread
from services.reference_graph.store import Edge, GraphScope, GraphStore, _hash
from services.reference_graph.workspace import GraphWorkspace


async def allowed() -> None:
    """Synthetic fixture has no remote credential or operator grant."""


async def synthetic_editor(action: str, **_values) -> dict:
    """Supply an empty native catalog; only owned JSON sources are measured."""
    status = {
        "instance_id": "editor",
        "project_id": "project",
        "epoch": "synthetic",
        "revision": 0,
        "ignore_state": "available",
        "ignore_revision": 0,
    }
    match action:
        case "status":
            return status
        case "inventory":
            return {**status, "snapshot": "inventory", "assets": [], "next_offset": None}
        case "ignore_rules":
            return {**status, "snapshot": "ignore", "files": [], "next_offset": None}
        case "changes":
            return {
                **status,
                "requires_census": False,
                "changes": [],
                "next_after": 0,
                "has_more": False,
            }
        case _:
            raise AssertionError("Unexpected synthetic native action: " + action)


def component_work(count: int, query_limit: int) -> dict:
    """Measure prior metadata, one excluded query page, and atomic final cleanup."""
    with tempfile.TemporaryDirectory(prefix="reference-graph-bounded-") as directory:
        store = GraphStore.open(Path(directory), GraphScope("synthetic", "editor", "project"))
        workspace = GraphWorkspace(store)
        provider = "external:measure"
        workspace.set_source(
            provider, {"root": "/synthetic", "include": ["*.json"]}, expected_version=0
        )
        assert workspace.acquire("worker")
        for start in range(0, count, 128):
            workspace.record_external_many(
                "worker",
                [
                    (
                        provider,
                        provider + ":" + _hash(f"{i}.json"),
                        f"{i}.json",
                        "fp",
                        "stamp",
                        "key",
                        "old",
                    )
                    for i in range(start, min(start + 128, count))
                ],
            )
            with store._connect(write=True) as db:
                for i in range(start, min(start + 128, count)):
                    store._insert_edge(
                        db, "root", "manual", Edge(f"hidden:{i}", "declared"), f"declared:{i:08}"
                    )
                    db.execute(
                        "INSERT INTO graph_excluded_nodes VALUES (?,?)", (f"hidden:{i}", "private")
                    )
        with store._connect(write=True) as db:
            store._insert_edge(db, "root", "manual", Edge("visible", "declared"), "declared:zzzz")
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        loaded, maximum = 0, 0
        if hasattr(workspace, "external_input_page"):
            after = ""
            while page := workspace.external_input_page(provider, after=after, unseen="new"):
                loaded += len(page)
                maximum = max(maximum, len(page))
                after = next(reversed(page.values()))["owner"]
        else:
            page = workspace.external_inputs(provider, admission=True)
            loaded = maximum = len(page)
        prior_seconds, prior_peak = (
            time.perf_counter() - started,
            tracemalloc.get_traced_memory()[1],
        )
        tracemalloc.stop()
        del page
        steps = [0]
        with store.read_snapshot(), store._connect() as db:

            def tick():
                steps[0] += 100
                return 0

            # This measurement owns a read-only callback; production cancellation
            # and storage callback composition are separately regression tested.
            db.set_progress_handler(tick, 100)
            started = time.perf_counter()
            result = store.query("root", limit=query_limit)
            query_seconds = time.perf_counter() - started
            query_steps = steps[0]
            steps[0] = 0
            # The old filter-before-limit seam can be measured on identical rows
            # without restoring or mutating production sources.
            legacy = db.execute(
                "SELECT e.id FROM edges e JOIN sources s USING(provider) WHERE e.owner='root' AND s.enabled=1 AND NOT EXISTS (SELECT 1 FROM graph_excluded_nodes x WHERE x.id=e.owner OR x.id=e.target) ORDER BY e.id LIMIT ?",
                (query_limit,),
            ).fetchall()
            legacy_steps = steps[0]
            db.set_progress_handler(None, 0)
        assert len(legacy) == 1
        # The long chain's terminal boundary must still expose the same final
        # valid edge. Small regression fixtures exhaust every intervening page.
        anchor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "query": _hash((store.scope.key, "root", "outgoing")),
                    "revision": result["revision"],
                    "last": f"declared:{count - 1:08}",
                }
            ).encode()
        ).decode()
        final = store.query("root", limit=query_limit, cursor=anchor)
        assert [edge["target"] for edge in final["edges"]] == ["visible"]
        assert store.query("root", limit=query_limit, cursor=anchor) == final
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        workspace.finish_external("worker", provider, "new")
        cleanup_seconds, cleanup_peak = (
            time.perf_counter() - started,
            tracemalloc.get_traced_memory()[1],
        )
        tracemalloc.stop()
        return {
            "count": count,
            "loaded": loaded,
            "maximum_metadata_rows": maximum,
            "prior_seconds": prior_seconds,
            "prior_peak_bytes": prior_peak,
            "excluded_query_seconds": query_seconds,
            "query_limit": query_limit,
            "excluded_query_vm_steps": query_steps,
            "legacy_filtered_vm_steps": legacy_steps,
            "query_continues": result["next_cursor"] is not None,
            "final_valid_edge_and_replay": True,
            "cleanup_seconds": cleanup_seconds,
            "cleanup_peak_bytes": cleanup_peak,
        }


async def full_sweep(count: int) -> dict:
    """Measure real reconciliation and compare immutable evidence after reopening."""
    with tempfile.TemporaryDirectory(prefix="reference-graph-sweep-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        for i in range(count):
            (source / f"{i}.json").write_text("{}", encoding="utf-8")
        store = GraphStore.open(root / "state", GraphScope("synthetic", "editor", "project"))
        workspace = GraphWorkspace(store)
        workspace.set_source(
            "external:measure", {"root": str(source), "include": ["*.json"]}, expected_version=0
        )
        editor = synthetic_editor
        engine = Reconciler(workspace, editor, allowed)
        started = time.perf_counter()
        assert await engine.reconcile()
        cold = time.perf_counter() - started
        calls, maximum = [0], [0]
        inputs = workspace.external_inputs

        def counted(*args, **kwargs):
            if hasattr(workspace, "external_input_page"):
                assert kwargs.get("paths") is not None or kwargs.get("_after") is not None
            page = inputs(*args, **kwargs)
            calls[0] += 1
            maximum[0] = max(maximum[0], len(page))
            return page

        workspace.external_inputs = counted
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        assert await engine.reconcile()
        warm, peak = time.perf_counter() - started, tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()

        def digest():
            with store._connect() as db:
                return _hash(
                    [
                        tuple(row)
                        for row in db.execute(
                            "SELECT owner,fingerprint,stamp,analysis_key FROM graph_external ORDER BY owner"
                        )
                    ]
                )

        before = digest()
        reopened = GraphWorkspace(GraphStore(store.path, store.scope))
        assert await Reconciler(reopened, editor, allowed).reconcile()
        assert before == digest()
        return {
            "files": count,
            "cold_full_seconds": cold,
            "warm_full_seconds": warm,
            "warm_python_peak_bytes": peak,
            "metadata_queries": calls[0],
            "maximum_metadata_page_rows": maximum[0],
            "reopened_digest_equal": True,
        }


def sizes(value: str) -> list[int]:
    """Require explicitly bounded positive workload inputs."""
    result = [int(item) for item in value.split(",")]
    if not result or any(not 1 <= item <= 100000 for item in result) or len(result) > 8:
        raise argparse.ArgumentTypeError("Use 1-8 comma-separated counts from 1 to 100000")
    return result


async def cancellation_work(cooperative: bool) -> float:
    """Time physical producer drain for identical synthetic long-running SQL."""
    with tempfile.TemporaryDirectory(prefix="reference-graph-cancel-") as directory:
        store = GraphStore.open(Path(directory), GraphScope("synthetic", "editor", "project"))
        started, finished = Event(), Event()

        def work():
            try:
                with store._connect() as db:
                    started.set()
                    db.execute(
                        "WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<1000000) SELECT sum(n) FROM x"
                    ).fetchone()
            finally:
                finished.set()

        thread = (
            getattr(reconciliation, "cancellable_thread", drained_thread)
            if cooperative
            else drained_thread
        )
        task = asyncio.create_task(thread(work))
        assert await asyncio.to_thread(started.wait, 10)
        start = time.perf_counter()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert finished.is_set()
        return time.perf_counter() - start


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--counts", type=sizes, default=sizes("1000,10000,100000"))
    parser.add_argument("--query-limit", type=int, choices=range(1, 10001), default=1)
    parser.add_argument(
        "--sweep-counts", type=sizes, help="Optional real synthetic full sweeps, e.g. 128,512,2048"
    )
    args = parser.parse_args()
    result = {
        "components": [component_work(count, args.query_limit) for count in args.counts],
        "full_sweeps": [await full_sweep(count) for count in args.sweep_counts or []],
        "sql_cancellation": {
            "cooperative_hook_available": hasattr(reconciliation, "cancellable_thread"),
            "uncooperative_drain_seconds": await cancellation_work(False),
            "cooperative_drain_seconds": await cancellation_work(True),
        },
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
