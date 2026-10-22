"""Measure compiler-context analysis at project scale using owned in-memory inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Server" / "src"))

from services.reference_graph.analysis import AnalysisIndex, Catalog, parse_csharp  # noqa: E402


def measure(count: int, context_enabled: bool) -> dict:
    build = {
        "state": "available",
        "assembly": "Game",
        "references": [f"Dependency{index}" for index in range(100)],
        "defines": ["CLIENT", *(f"SYMBOL_{index}" for index in range(24))],
        "revision": "synthetic",
        "target": "synthetic",
        "assemblies_type": "Editor",
    }
    kwargs = {"context": build} if context_enabled else {}
    catalog = Catalog(
        [
            {
                "guid": "1" * 32,
                "path": "Assets/Resources/one.prefab",
                "keys": [{"scheme": "resources", "value": "one"}],
            }
        ]
    )
    started = time.perf_counter()
    facts = [
        parse_csharp(
            "loader",
            "Assets/Loader.cs",
            "class Loader { public static void Load(string key) { UnityEngine.Resources.Load(key); } }",
            **kwargs,
        )
    ]
    for number in range(count):
        facts.append(
            parse_csharp(
                str(number),
                f"Assets/C{number}.cs",
                f'class C{number} {{ void Run() {{ Loader.Load("one"); }} }}',
                **kwargs,
            )
        )
    parsed = time.perf_counter()
    index = AnalysisIndex(facts, catalog)
    indexed = time.perf_counter()
    digest = hashlib.sha256()
    for number in range(count):
        edges = index.analyze(str(number)).edges
        assert len(edges) == 1 and edges[0].target == "asset:" + "1" * 32
        digest.update(repr(edges).encode())
    resolved = time.perf_counter()
    tracemalloc.start()
    update_started = time.perf_counter()
    replacement = parse_csharp(
        "0", "Assets/C0.cs", 'class C0 { void Run() { Loader.Load("missing"); } }', **kwargs
    )
    affected = index.update([replacement])
    assert affected == {"0"}
    assert not index.analyze("0").edges
    update_seconds = time.perf_counter() - update_started
    _, update_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "scripts": count,
        "context_enabled": context_enabled,
        "parse_seconds": parsed - started,
        "index_seconds": indexed - parsed,
        "resolve_seconds": resolved - indexed,
        "edit_seconds": update_seconds,
        "edit_traced_peak_bytes": update_peak,
        "edge_digest": digest.hexdigest(),
        "verified_edges": count,
        "affected": len(affected),
        "python": sys.version,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts", type=int, default=50000)
    parser.add_argument("--context", action="store_true")
    args = parser.parse_args()
    if args.scripts < 1:
        parser.error("scripts must be positive")
    print(json.dumps(measure(args.scripts, args.context), indent=2))
