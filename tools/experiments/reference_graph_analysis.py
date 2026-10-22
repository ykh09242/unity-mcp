"""Reproducible source-analysis workload; no Unity or external project access.

Run with the existing server environment. Each invocation owns only synthetic
in-memory inputs. Time the cold parse/index, warm resolution and dependency fanout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Server" / "src"))

from services.reference_graph.analysis import AnalysisIndex, Catalog, parse_csharp  # noqa: E402
from services.reference_graph.external import analyze_document  # noqa: E402


def measure(scripts: int, assets: int) -> dict:
    def guid(i):
        return f"{i + 1:032x}"

    started = time.perf_counter()
    catalog = Catalog(
        {
            "guid": guid(i),
            "path": f"Assets/Resources/Icons/{i}.prefab",
            "keys": [{"scheme": "resources", "value": f"Icons/{i}"}],
        }
        for i in range(assets)
    )
    catalog_seconds = time.perf_counter() - started
    started = time.perf_counter()
    wrapper = parse_csharp(
        "wrapper",
        "Assets/Loader.cs",
        'using UnityEngine; public static class Loader { public static Object Get(string key) => Resources.Load("Icons/" + key); }',
    )
    facts = [wrapper]
    for i in range(scripts):
        facts.append(
            parse_csharp(
                f"script:{i}",
                f"Assets/C{i}.cs",
                f'class C{i} {{ void Go() {{ Loader.Get("{i % assets}"); }} }}',
            )
        )
    parse_seconds = time.perf_counter() - started
    started = time.perf_counter()
    index = AnalysisIndex(facts, catalog)
    index_seconds = time.perf_counter() - started
    started = time.perf_counter()
    edges = 0
    for i in range(scripts):
        result = index.analyze(f"script:{i}")
        assert len(result.edges) == 1 and result.edges[0].target == "asset:" + guid(i % assets)
        edges += 1
    resolve_seconds = time.perf_counter() - started
    started = time.perf_counter()
    affected = index.affected({"wrapper"})
    assert len(affected) == scripts + 1
    fanout_seconds = time.perf_counter() - started
    started = time.perf_counter()
    data = "icon\n" + "".join(f"Icons/{i % assets}\n" for i in range(scripts))
    external = analyze_document(
        "synthetic.csv", data, catalog, [{"field": "icon", "scheme": "resources"}]
    )
    assert len(external.edges) == scripts
    csv_seconds = time.perf_counter() - started
    return {
        "scripts": scripts,
        "assets": assets,
        "verified_edges": edges,
        "verified_affected": len(affected),
        "catalog_seconds": catalog_seconds,
        "parse_seconds": parse_seconds,
        "index_seconds": index_seconds,
        "resolve_seconds": resolve_seconds,
        "wrapper_fanout_seconds": fanout_seconds,
        "csv_seconds": csv_seconds,
        "python": sys.version,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts", type=int, default=50000)
    parser.add_argument("--assets", type=int, default=250000)
    args = parser.parse_args()
    if args.scripts < 1 or args.assets < 1:
        parser.error("Workload sizes must be positive")
    print(json.dumps(measure(args.scripts, args.assets), indent=2))
