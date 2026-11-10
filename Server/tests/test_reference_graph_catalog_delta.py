"""Warm catalogs apply changed asset payloads while preserving all lookup outcomes."""

import json

import pytest

from services.reference_graph.analysis import Catalog
from services.reference_graph import analysis_cache, catalog_delta
from services.reference_graph.store import GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace


class CountedResourceKeys(list):
    """Measure index shifts and ordering reads independently of elapsed time."""

    def __init__(self, values):
        super().__init__(values)
        self.shifts = 0
        self.visits = 0

    def pop(self, index=-1):
        self.shifts += len(self) - (index % len(self)) - 1
        return super().pop(index)

    def insert(self, index, value):
        self.shifts += len(self) - index
        return super().insert(index, value)

    def __iter__(self):
        for value in super().__iter__():
            self.visits += 1
            yield value

    def __getitem__(self, index):
        self.visits += 1
        return super().__getitem__(index)


@pytest.mark.parametrize("size", [10000, 100000])
@pytest.mark.parametrize("membership_changes", [False, True])
def test_resources_delta_work_tracks_membership_and_batches_ordering(size, membership_changes):
    # Given an ordered Resources catalog and a batch of one hundred asset deltas.
    assets = [
        {
            "guid": str(index),
            "path": f"Assets/{index}.prefab",
            "keys": [{"scheme": "resources", "value": f"item/{index:06d}"}],
        }
        for index in range(size)
    ]
    catalog = Catalog(assets)
    keys = CountedResourceKeys(catalog.resource_keys)
    catalog.resource_keys = keys
    changes = [
        {
            **asset,
            "path": "Assets/Renamed/" + asset["guid"],
            "dependency_hash": "updated",
            "keys": [{"scheme": "resources", "value": f"renamed/{index:06d}"}]
            if membership_changes
            else asset["keys"],
        }
        for index, asset in enumerate(assets[:100])
    ]
    # When one metadata or actual membership batch is applied.
    catalog.update(iter(changes))
    print(
        f"Resources size={size}, membership_changes={membership_changes}: "
        f"shifts={keys.shifts}, ordering_visits={keys.visits}"
    )
    # Then metadata never rewrites ordering, and membership merges scan it once.
    assert keys.shifts == 0
    assert keys.visits <= (size + 200 if membership_changes else 0)
    fresh = Catalog([*changes, *assets[100:]])
    assert catalog.resource_keys == fresh.resource_keys
    assert catalog.resolve("resources", "", "all") == fresh.resolve("resources", "", "all")
    for index in (0, 99, 100, size - 1):
        for key in (f"item/{index:06d}", f"renamed/{index:06d}"):
            assert catalog.resolve("resources", key) == fresh.resolve("resources", key)


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
    cache = None

    def catalog():
        nonlocal cache
        if consumer == "runtime":
            return workspace.key_admission()
        cache, _ = analysis_cache.refresh(
            workspace, cache, {"revision": 0, "rules": []}, lambda: None
        )
        return cache.index.catalog

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


def test_interrupted_membership_batch_keeps_applied_prefix_resolvable():
    # Given a valid delta followed by a failing producer.
    catalog = Catalog(
        [{"guid": "a", "path": "Assets/A", "keys": [{"scheme": "resources", "value": "old"}]}]
    )

    def changes():
        yield {"guid": "a", "path": "Assets/A", "keys": [{"scheme": "resources", "value": "new"}]}
        raise RuntimeError("delta producer interrupted")

    # When the producer stops after the first applied membership change.
    with pytest.raises(RuntimeError, match="delta producer interrupted"):
        catalog.update(changes())
    # Then exact and streamed prefix resolution still describe the applied prefix.
    assert catalog.resolve("resources", "old")[1] == "key_not_found"
    assert catalog.resolve("resources", "new")[0] == {"asset:a"}
    assert catalog.resolve("resources", "", "all")[0] == {"asset:a"}
    assert list(catalog.iter_resolved_detail("resources", "", "all")) == [("asset:a", None, "")]


def test_batched_membership_swaps_preserve_collisions_and_admission_counts():
    def asset(guid, key, **metadata):
        return {
            "guid": guid,
            "path": "Assets/" + guid,
            "keys": [{"scheme": "resources", "value": key}],
            **metadata,
        }

    # Given shared, excluded and unavailable memberships.
    catalog = Catalog(
        [asset("a", "X"), asset("b", "X"), asset("c", "X", _excluded=True), asset("d", "Y")]
    )
    keys = CountedResourceKeys(catalog.resource_keys)
    catalog.resource_keys = keys
    final = [
        asset("a", "X", _excluded=True),
        asset("b", "Y"),
        asset("c", "X"),
        asset("d", "X", addressables="failed"),
    ]
    # When the same batch swaps keys, re-adds removals and changes admission.
    catalog.update(final, removed=["a", "c"])
    # Then the final bucket set needs no ordering work and matches a fresh catalog.
    assert keys.shifts == keys.visits == 0
    fresh = Catalog(final)
    for key in ("", "X", "Y", "missing"):
        for mode in ("single", "all"):
            assert catalog.resolve("resources", key, mode) == fresh.resolve("resources", key, mode)
    assert list(catalog.iter_resolved_detail("resources", "", "all")) == list(
        fresh.iter_resolved_detail("resources", "", "all")
    )
    assert catalog.resolve("addressables", "missing")[1] == "provider_unavailable"
    catalog.update([], removed=["c", "d"])
    assert catalog.resolve("resources", "X")[1] == "excluded"
    assert catalog.resolve("addressables", "missing")[1] == "key_not_found"


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
