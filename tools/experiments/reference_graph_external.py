"""Owned component experiment; compare the same synthetic workloads across revisions."""

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

parser = argparse.ArgumentParser()
parser.add_argument("repo", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.repo / "Server/src"))
from core.config import config
from services.reference_graph.reconcile import Reconciler
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.store import GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace


async def allowed(*_args, **_kwargs):
    pass


async def measure(files, assets):
    root = args.output.parent / ("external-measure-" + uuid.uuid4().hex)
    settings = root / "settings"
    settings.mkdir(parents=True)
    for index in range(files):
        (settings / f"{index:05}.json").write_text('{"prefab":"One"}', encoding="utf-8")
    store = GraphStore.open(root / "state", GraphScope("local:benchmark", "editor", "project"))
    workspace = GraphWorkspace(store)
    workspace.set_source(
        "external:test",
        {
            "root": str(settings),
            "include": ["*.json"],
            "mappings": [{"field": "/prefab", "scheme": "resources"}],
        },
        expected_version=0,
    )
    engine = Reconciler(workspace, allowed, allowed)
    assert workspace.acquire(engine.worker)
    workspace.record_page(
        engine.worker,
        [
            {
                "guid": f"{index + 1:032x}",
                "path": f"Assets/Resources/A{index}.prefab",
                "kind": "asset",
                "source_format": "none",
                "keys": [{"scheme": "resources", "value": "One" if index == 0 else f"A{index}"}],
            }
            for index in range(assets)
        ],
        {},
        "seed",
        force=False,
    )
    counts = {"reads": 0, "writes": 0}
    original = store._connect

    @contextmanager
    def counted(*values, **options):
        counts["writes" if options.get("write") else "reads"] += 1
        with original(*values, **options) as connection:
            yield connection

    store._connect = counted
    samples = []
    try:
        for label in ("initial", "unchanged", "unchanged_again", "unchanged_third"):
            counts.update(reads=0, writes=0)
            started = time.perf_counter()
            await engine._external(force=False)
            elapsed = time.perf_counter() - started
            sample = {"label": label, "seconds": elapsed, **counts}
            with original() as connection:
                edges = [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT owner,target,kind FROM edges ORDER BY owner,target,kind"
                    )
                ]
            sample["edges"] = len(edges)
            sample["digest"] = hashlib.sha256(json.dumps(edges).encode()).hexdigest()
            samples.append(sample)
            workspace.renew(engine.worker)
        query_samples = []
        service = ReferenceGraphService()
        for _ in range(3):
            result = store.query("asset:" + f"{1:032x}", direction="incoming", limit=files)
            assert len(result["edges"]) == files
            counts.update(reads=0, writes=0)
            started = time.perf_counter()
            for _ in range(2):
                assert not service._validate_external(workspace, result)
            query_samples.append({"seconds": time.perf_counter() - started, **counts})
    finally:
        workspace.release(engine.worker)
    return {"files": files, "assets": assets, "scans": samples, "queries": query_samples}


async def main():
    config.http_remote_hosted = False
    results = [await measure(100, 2500), await measure(1000, 25000)]
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


asyncio.run(main())
