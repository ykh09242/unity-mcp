"""Cold catalogs consume decoded records incrementally and publish atomically."""

import json

import pytest

from services.reference_graph import analysis_cache
from services.reference_graph.analysis import Catalog
from services.reference_graph.store import GraphScope, GraphStore, guard_graph_work
from services.reference_graph.workspace import GraphWorkspace


@pytest.mark.parametrize("consumer", ["runtime", "analysis"])
def test_cold_catalog_indexes_each_record_before_decoding_the_next(tmp_path, monkeypatch, consumer):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("u", "e", "p")))
    assert workspace.acquire("worker")
    workspace.record_page(
        "worker", [{"guid": str(i), "path": f"Assets/{i}"} for i in range(200)], {}, "scan"
    )
    decoded, indexed = [], []
    loads, asset_keys = json.loads, Catalog.asset_keys

    def decode(payload, *args, **kwargs):
        result = loads(payload, *args, **kwargs)
        if isinstance(result, dict) and "guid" in result:
            decoded.append(result["guid"])
        return result

    def index(cls, asset):
        indexed.append(asset["guid"])
        assert len(decoded) == len(indexed), "Cold construction retained a decoded payload backlog"
        return asset_keys(asset)

    monkeypatch.setattr(json, "loads", decode)
    monkeypatch.setattr(Catalog, "asset_keys", classmethod(index))
    if consumer == "runtime":
        result = workspace.key_admission()
    else:
        cache, _ = analysis_cache.refresh(
            workspace, None, {"revision": 0, "rules": []}, lambda: None
        )
        result = cache.index.catalog
    assert decoded == indexed and len(indexed) == 200
    assert result.resolve("path", "Assets/199")[0] == {"asset:199"}


def test_interrupted_cold_rebuild_keeps_prior_cache_and_excluded_memberships(tmp_path, monkeypatch):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("u", "e", "p")))
    workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    assert workspace.acquire("worker")
    rows = [{"guid": str(i), "path": f"Assets/{i}"} for i in range(30)]
    rows.append(
        {
            "guid": "hidden",
            "path": "Assets/Private/Hidden",
            "keys": [{"scheme": "resources", "value": "Secret"}],
        }
    )
    workspace.record_page("worker", rows, {}, "scan")
    original = workspace.key_admission()
    workspace.record_page("worker", [{"guid": "new", "path": "Assets/New"}], {}, "scan")
    with workspace.store._connect(write=True) as db:
        db.execute("DELETE FROM graph_catalog_deltas")
    indexed = []
    asset_keys = Catalog.asset_keys

    def index(cls, asset):
        indexed.append(asset["guid"])
        return asset_keys(asset)

    def interrupt():
        if len(indexed) >= 4:
            raise InterruptedError("stop catalog rebuild")

    monkeypatch.setattr(Catalog, "asset_keys", classmethod(index))
    with pytest.raises(InterruptedError, match="stop catalog rebuild"), guard_graph_work(interrupt):
        workspace.key_admission()
    assert workspace._key_cache[1] is original
    assert original.resolve("path", "Assets/New")[1] == "key_not_found"
    rebuilt = workspace.key_admission()
    assert rebuilt.resolve("path", "Assets/New")[0] == {"asset:new"}
    assert rebuilt.resolve("resources", "Secret")[1] == "excluded"
    revision, snapshot = workspace.catalog_snapshot()
    assert revision > 0 and isinstance(snapshot, list)
    assert next(row for row in snapshot if row["guid"] == "hidden")["_excluded"]
