"""Measure page cost on a large, synthetic, scope-owned reference graph."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Server" / "src"))

from services.reference_graph.queries import GraphQueries  # noqa: E402
from services.reference_graph.store import Edge, GraphScope, GraphStore  # noqa: E402


def measure(root: Path, count: int) -> dict:
    store = GraphStore.open(root, GraphScope("synthetic", "experiment", "owned"))
    generation = store.observe("root", "code", "synthetic", path="Assets/Synthetic.cs")
    store.publish(
        "root", "code", generation, (Edge(f"child:{i}", "synthetic") for i in range(count))
    )
    queries = GraphQueries(store)

    def capture(name):
        cursor = None
        started = time.perf_counter()
        while True:
            result = queries.snapshot(name=name, limit=1000, cursor=cursor)
            cursor = result["next_cursor"]
            if not cursor:
                assert result["snapshot"]["captured"] == count
                return result["snapshot"]["id"], time.perf_counter() - started

    before, first_seconds = capture("before")
    after, second_seconds = capture("after")
    connect = store._connect
    instructions = 0

    def step():
        nonlocal instructions
        instructions += 100
        return 0

    @contextmanager
    def counted(**kwargs):
        with connect(**kwargs) as connection:
            connection.set_progress_handler(step, 100)
            yield connection

    store._connect = counted
    tracemalloc.start()
    started = time.perf_counter()
    comparison = queries.diff(before, after, limit=100)
    comparison_seconds = time.perf_counter() - started
    _, comparison_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    comparison_instructions = instructions
    assert comparison["changes"] == [] and comparison["compared"] == min(100, count)
    assert bool(comparison["next_cursor"]) == (count > 100)
    store._connect = connect

    started = time.perf_counter()
    cursor, total, pages = None, 0, 0
    while True:
        result = queries.impact("root", direction="outgoing", limit=1000, cursor=cursor)
        total += len(result["edges"])
        pages += 1
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert total == count
    with store._connect() as connection:
        assert connection.execute("SELECT count(*) FROM graph_query_nodes").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM graph_query_queue").fetchone()[0] == 0
    return {
        "edges": count,
        "snapshot_seconds": [first_seconds, second_seconds],
        "identical_diff_first_page": {
            "compared": comparison["compared"],
            "seconds": comparison_seconds,
            "python_traced_peak_bytes": comparison_peak,
            "sqlite_vm_instructions_rounded": comparison_instructions,
        },
        "complete_fanout": {
            "edges": total,
            "pages": pages,
            "seconds": time.perf_counter() - started,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=50000)
    parser.add_argument("--scratch", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.count < 1:
        parser.error("count must be positive")
    arguments.scratch.mkdir(parents=True, exist_ok=True)
    # TemporaryDirectory owns exactly the fresh child it creates.
    with tempfile.TemporaryDirectory(prefix="query-measurement-", dir=arguments.scratch) as root:
        print(json.dumps(measure(Path(root), arguments.count), indent=2))


if __name__ == "__main__":
    main()
