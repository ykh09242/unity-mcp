"""Aggregate summaries stay exact across transactions and independent writers."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from services.reference_graph.store import Edge, GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace


@pytest.fixture
def graph(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    workspace = GraphWorkspace(store)
    assert workspace.acquire("worker")
    asset = {
        "guid": "a",
        "path": "Assets/A.prefab",
        "source_format": "none",
        "addressables": "ready",
        "addressables_version": "1",
    }
    workspace.record_page("worker", [asset], {}, "scan")
    generation = store.observe("a", "code", "1")
    store.publish("a", "code", generation, [Edge("b", "load")])
    return store, workspace, asset


def test_unchanged_summaries_skip_repeated_aggregates(graph, monkeypatch):
    store, workspace, _ = graph
    statements = []
    connect = store._connect

    @contextmanager
    def traced(**kwargs):
        with connect(**kwargs) as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, "_connect", traced)
    for read in (store.status, workspace.counts, workspace.provider_status):
        first = read()
        statements.clear()
        assert read() == first
        assert not any("count(*)" in sql.lower() for sql in statements)


def test_summary_copies_and_other_writer_invalidation(graph):
    store, workspace, asset = graph
    status = store.status()
    counts = workspace.counts()
    providers = workspace.provider_status()
    status["ready"] = -1
    counts["dirty_assets"] = -1
    providers["addressables"]["states"]["ready"] = -1
    providers["addressables"]["versions"].append("wrong")
    assert store.status()["ready"] == 1
    assert workspace.counts()["dirty_assets"] == 1
    assert workspace.provider_status()["addressables"]["versions"] == ["1"]
    other = GraphWorkspace(GraphStore(store.path, store.scope))
    revision = store.status()["revision"]
    other.clean_asset("worker", asset["guid"])
    # Cleaning doesn't invalidate graph cursors but must invalidate counts.
    assert store.status()["revision"] == revision
    assert workspace.counts()["dirty_assets"] == 0
    other.fail_asset("worker", asset, ValueError("unavailable"))
    assert workspace.counts()["failed_assets"] == 1
    assert store.status()["errors"] == 1
    other.clean_asset("worker", asset["guid"])
    assert workspace.counts()["failed_assets"] == 0
    other.record_page("worker", [{**asset, "addressables": "unavailable"}], {}, "scan2")
    assert workspace.provider_status()["addressables"]["errors"] == 1
    other.remove_path("worker", asset["path"])
    assert workspace.counts()["assets"] == 0
    assert workspace.provider_status()["addressables"]["states"] == {}


def test_summary_rollback_and_source_policy_changes(graph, monkeypatch):
    store, workspace, asset = graph
    original = workspace.counts()
    write = workspace._write

    @contextmanager
    def rollback(owner):
        with write(owner) as db:
            yield db
            raise RuntimeError("rollback")

    with monkeypatch.context() as patch:
        patch.setattr(workspace, "_write", rollback)
        with pytest.raises(RuntimeError):
            workspace.clean_asset("worker", asset["guid"])
    assert workspace.counts() == original
    workspace.set_source("external:sample", {}, expected_version=0)
    other = GraphStore(store.path, store.scope)
    generation = other.observe("external-owner", "external:sample", "1", source_version=1)
    other.publish("external-owner", "external:sample", generation, [])
    assert store.status()["ready"] == 2
    other.set_source("external:sample", {}, expected_version=1, enabled=False)
    assert store.status()["ready"] == 1
    workspace.set_policy({"exclude": ["Assets/**"]}, expected_version=0)
    assert workspace.counts() == {
        "assets": 0,
        "dirty_assets": 0,
        "failed_assets": 0,
        "unresolved": 0,
    }
    assert workspace.provider_status()["addressables"]["states"] == {}
    assert store.status()["ready"] == 0


def test_old_reader_cannot_poison_new_revision_summary(graph, monkeypatch):
    store, _, _ = graph
    connect = store._connect
    reached, resume = threading.Event(), threading.Event()
    first = True

    class PausedConnection:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            nonlocal first
            if "GROUP BY o.state" in sql and first:
                first = False
                reached.set()
                assert resume.wait(10)
            return self.db.execute(sql, *args)

    @contextmanager
    def paused(**kwargs):
        with connect(**kwargs) as db:
            yield PausedConnection(db)

    monkeypatch.setattr(store, "_connect", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(store.status)
        try:
            assert reached.wait(5)
            other = GraphStore(store.path, store.scope)
            other.observe("new", "code", "1")
            current = store.status()
            assert current["pending"] == 1
        finally:
            resume.set()
        assert old.result()["pending"] == 0
    assert store.status() == current


def test_replaced_database_with_same_revision_does_not_reuse_summary(graph, tmp_path):
    import os

    store, workspace, _ = graph
    before = store.status()
    workspace.counts()
    workspace.provider_status()
    replacement = GraphStore(tmp_path / "replacement.sqlite", store.scope)
    GraphWorkspace(replacement)
    with replacement._connect(write=True) as db:
        db.execute("UPDATE graph_meta SET revision=?", (before["revision"],))
    os.replace(replacement.path, store.path)
    assert store.status()["ready"] == 0
    assert workspace.counts()["assets"] == 0
    assert workspace.provider_status()["addressables"]["states"] == {}


def test_progress_and_external_verification_do_not_rescan_workspace_counts(graph, monkeypatch):
    store, workspace, _ = graph
    workspace.set_source("external:sample", {}, expected_version=0)
    assert workspace.acquire("worker")
    before = workspace.counts()
    statements = []
    connect = store._connect

    @contextmanager
    def traced(**kwargs):
        with connect(**kwargs) as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, "_connect", traced)
    workspace.set_progress("worker", {"phase": "idle"})
    workspace.external_scan(
        "worker",
        {"provider": "external:sample", "version": 1},
        {"state": "verified_at_scan", "last_verified_at": 123},
    )
    assert workspace.counts() == before
    assert not any("count(*)" in sql.lower() for sql in statements)
    assert workspace.provider_status()["external:sample"]["last_verified_at"] == 123
    workspace.external_scan(
        "worker",
        {"provider": "external:sample", "version": 1},
        {"state": "error", "last_verified_at": 456},
    )
    assert workspace.provider_status()["external:sample"]["state"] == "error"


def test_repeated_clean_keeps_summary_generation_and_cached_counts(graph):
    store, workspace, asset = graph
    workspace.clean_asset("worker", asset["guid"])
    generation = workspace.state("summary_generation")
    counts = workspace.counts()
    workspace.clean_asset("worker", asset["guid"])
    workspace.clean_asset("worker", "absent")
    assert workspace.state("summary_generation") == generation
    assert workspace.counts() == counts
    assert store.status()["ready"] == 1
