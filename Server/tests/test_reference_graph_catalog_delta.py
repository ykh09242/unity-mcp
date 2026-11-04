"""Warm catalogs apply changed asset payloads while preserving all lookup outcomes."""

import json

import pytest

from services.reference_graph.analysis import Catalog
from services.reference_graph import catalog_delta
from services.reference_graph.reconcile import Reconciler
from services.reference_graph.store import GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace
from tests.test_reference_graph_reconcile import Editor, allowed


@pytest.mark.parametrize("consumer", ["runtime", "analysis"])
def test_warm_catalog_decodes_and_indexes_only_changed_assets(tmp_path, monkeypatch, consumer):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    assert workspace.acquire("worker")
    assets = [
        {
            "guid": f"{index:032x}",
            "path": f"Assets/{index}.prefab",
            "keys": [{"scheme": "resources", "value": f"item/{index}"}],
        }
        for index in range(2000)
    ]
    workspace.record_page("worker", assets, {}, "scan")
    engine = Reconciler(workspace, Editor(), allowed)

    def catalog():
        if consumer == "runtime":
            return workspace.key_admission()
        return engine._refresh_analysis({}, {"revision": 0, "rules": []}, set())[0].catalog

    catalog()
    decoded, indexed = [], []
    loads, asset_keys = json.loads, Catalog.asset_keys

    def counted_loads(value, *args, **kwargs):
        result = loads(value, *args, **kwargs)
        if isinstance(result, dict) and "guid" in result:
            decoded.append(result["guid"])
        return result

    def counted_keys(cls, asset):
        indexed.append(asset["guid"])
        return asset_keys(asset)

    monkeypatch.setattr(json, "loads", counted_loads)
    monkeypatch.setattr(Catalog, "asset_keys", classmethod(counted_keys))
    changed = {
        **assets[1],
        "path": "Assets/Renamed.prefab",
        "keys": [{"scheme": "resources", "value": "renamed"}],
    }
    workspace.record_page("worker", [changed], {}, "scan", force=False)
    decoded.clear()
    indexed.clear()
    warm = catalog()
    assert decoded == [assets[1]["guid"]]
    assert indexed == [assets[1]["guid"]]
    assert warm.resolve("resources", "renamed")[0] == {"asset:" + assets[1]["guid"]}
    assert warm.resolve("resources", "item/1")[1] == "key_not_found"
    assert warm.resolve("path", "Assets/1.prefab")[1] == "key_not_found"
    decoded.clear()
    indexed.clear()
    catalog()
    assert decoded == indexed == []
    workspace.remove_path("worker", "Assets/Renamed.prefab")
    decoded.clear()
    indexed.clear()
    warm = catalog()
    assert decoded == indexed == []
    assert warm.resolve("resources", "renamed")[1] == "key_not_found"
    assert warm.resolve("resources", "item/1999")[0] == {"asset:" + assets[-1]["guid"]}


@pytest.mark.parametrize(
    "fallback", ["lost_revision", "lost_delta", "eviction", "restart", "upgrade"]
)
def test_missing_history_or_cold_cache_rebuilds_complete_catalog(tmp_path, fallback):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    workspace = GraphWorkspace(store)
    assert workspace.acquire("worker")
    rows = [{"guid": str(index), "path": f"Assets/{index}"} for index in range(20)]
    workspace.record_page("worker", rows, {}, "one")
    workspace.key_admission()
    workspace.record_page("worker", [{**rows[0], "path": "Assets/Renamed"}], {}, "one")
    workspace.remove_path("worker", "Assets/1")
    if fallback in {"lost_revision", "lost_delta"}:
        table = "graph_catalog_revisions" if fallback == "lost_revision" else "graph_catalog_deltas"
        with store._connect(write=True) as db:
            db.execute(f"DELETE FROM {table} WHERE revision=2")
    elif fallback == "eviction":
        workspace._key_cache = None
    else:
        if fallback == "upgrade":
            with store._connect(write=True) as db:
                db.execute("DROP TABLE graph_catalog_revisions")
                db.execute("DROP TABLE graph_catalog_deltas")
                db.execute("UPDATE graph_workspace SET version=8")
        workspace = GraphWorkspace(GraphStore.open(tmp_path, store.scope))
    result = workspace.key_admission()
    assert result.resolve("path", "Assets/0")[1] == "key_not_found"
    assert result.resolve("path", "Assets/1")[1] == "key_not_found"
    assert result.resolve("path", "Assets/Renamed")[0] == {"asset:0"}
    assert result.resolve("path", "Assets/19")[0] == {"asset:19"}


def test_delta_retention_limits_history_and_rebuilds_old_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_delta, "MAX_GUIDS", 4)
    monkeypatch.setattr(catalog_delta, "MAX_REVISIONS", 3)
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    assert workspace.acquire("worker")
    workspace.key_admission()
    for index in range(8):
        workspace.record_page(
            "worker", [{"guid": str(index), "path": f"Assets/{index}"}], {}, "one"
        )
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_catalog_revisions").fetchone()[0] <= 3
        assert db.execute("SELECT count(*) FROM graph_catalog_deltas").fetchone()[0] <= 4
    assert workspace.key_admission().resolve("path", "Assets/0")[0] == {"asset:0"}
    workspace.record_page(
        "worker",
        [{"guid": str(index), "path": f"Assets/{index}"} for index in range(8, 18)],
        {},
        "one",
    )
    catalog = workspace.key_admission()
    assert catalog.resolve("path", "Assets/0")[0] == {"asset:0"}
    assert catalog.resolve("path", "Assets/17")[0] == {"asset:17"}
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_catalog_deltas").fetchone()[0] <= 4


def test_incremental_catalog_preserves_shared_keys_exclusion_and_provider_status():
    shared = [
        {"scheme": "resources", "value": "Shared"},
        {"scheme": "addressables_label", "value": "label"},
    ]
    catalog = Catalog(
        [
            {"guid": "a", "path": "Assets/A", "keys": shared},
            {"guid": "b", "path": "Assets/B", "keys": shared},
            {"guid": "c", "path": "Assets/C", "keys": shared, "_excluded": True},
            {"guid": "d", "path": "Assets/D", "keys": shared, "_excluded": True},
            {"guid": "e", "path": "Assets/E", "addressables": "failed"},
        ]
    )
    assert catalog.resolve("resources", "shared")[1] == "ambiguous_key"
    catalog.update([], removed=["a"])
    assert catalog.resolve("resources", "shared")[0] == {"asset:b"}
    assert catalog.resolve("resources", "", "all")[0] == {"asset:b"}
    catalog.update([], removed=["b", "c"])
    assert catalog.resolve("resources", "shared")[1] == "excluded"
    assert catalog.resolve("resources", "", "all")[1] == "key_not_found"
    catalog.update([], removed=["d"])
    assert catalog.resolve("addressables", "label")[1] == "provider_unavailable"
    catalog.update([{"guid": "e", "path": "Assets/NewE"}])
    assert catalog.resolve("addressables", "label")[1] == "key_not_found"
    assert catalog.resolve("path", "Assets/E")[1] == "key_not_found"


def test_warm_catalog_retires_displaced_guid_and_rebuilds_after_policy_reset(tmp_path):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    assert workspace.acquire("worker")
    old = {"guid": "a", "path": "Assets/One", "keys": [{"scheme": "resources", "value": "old"}]}
    workspace.record_page("worker", [old], {}, "one")
    workspace.key_admission()
    new = {"guid": "b", "path": "Assets/One", "keys": [{"scheme": "resources", "value": "new"}]}
    workspace.record_page("worker", [new], {}, "one")
    catalog = workspace.key_admission()
    assert catalog.resolve("guid", "a")[1] == "key_not_found"
    assert catalog.resolve("resources", "old")[1] == "key_not_found"
    assert catalog.resolve("path", "Assets/One")[0] == {"asset:b"}
    workspace.set_policy({"exclude": ["Assets/One"]}, expected_version=0)
    assert workspace.acquire("worker")
    workspace.record_page("worker", [new], {}, "two")
    assert workspace.key_admission().resolve("resources", "new")[1] == "excluded"
    workspace.set_policy({"exclude": []}, expected_version=1)
    assert workspace.acquire("worker")
    workspace.record_page("worker", [new], {}, "three")
    assert workspace.key_admission().resolve("resources", "new")[0] == {"asset:b"}
