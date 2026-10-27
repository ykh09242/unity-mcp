"""Persistence and publication races for generated and declared references."""

from tests.lifecycle import FixtureProgress
from tests.timing import watchdog_timeout

import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from services.reference_graph.store import (
    Edge,
    GraphScope,
    GraphStore,
    Observation,
    RevisionConflict,
)

SCOPE = GraphScope("user-a", "editor-a", "project-a")


@pytest.fixture
def graph(tmp_path):
    return GraphStore(tmp_path / "graph.sqlite", SCOPE)


def test_initial_wal_transition_retries_real_writer_lock(graph, monkeypatch):
    retry_started = threading.Event()
    sleep = time.sleep

    def record_retry(seconds):
        retry_started.set()
        sleep(seconds)

    monkeypatch.setattr("services.reference_graph.store.time.sleep", record_retry)
    with closing(sqlite3.connect(graph.path)) as writer:
        assert writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        writer.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(graph._ensure_wal)
            try:
                assert retry_started.wait(timeout=watchdog_timeout())
                assert not future.done()
            finally:
                writer.rollback()
            future.result(timeout=watchdog_timeout())
    with closing(sqlite3.connect(graph.path)) as reopened:
        assert reopened.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_last_connection_may_remove_sidecars_during_ownership_check(graph, monkeypatch):
    keeper = sqlite3.connect(graph.path)
    keeper.execute("SELECT * FROM graph_meta").fetchall()
    wal = Path(str(graph.path) + "-wal")
    assert wal.exists()
    exists = Path.exists

    def close_after_existence(path, *args, **kwargs):
        result = exists(path, *args, **kwargs)
        if path == wal and result:
            keeper.close()
        return result

    monkeypatch.setattr(Path, "exists", close_after_existence)
    try:
        assert graph.status()["backend"] == "sqlite"
    finally:
        keeper.close()


def publish(graph, owner, fingerprint, targets, provider="unity"):
    source_version = next(
        source["version"] for source in graph.sources() if source["provider"] == provider
    )
    generation = graph.observe(
        owner, provider, fingerprint, path=f"Assets/{owner}.asset", source_version=source_version
    )
    assert graph.publish(owner, provider, generation, [Edge(t, "serialized") for t in targets])
    return generation


def test_late_worker_cannot_overwrite_newer_file_revision(graph):
    old = graph.observe("a", "code", "old")
    current = graph.observe("a", "code", "new")
    assert not graph.publish("a", "code", old, [Edge("wrong", "resources")])
    assert graph.publish("a", "code", current, [Edge("right", "resources")])
    assert [e["target"] for e in graph.query("a")["edges"]] == ["right"]


def test_duplicate_event_is_noop_and_pending_work_survives_restart(graph):
    generation = publish(graph, "a", "same", ["b"])
    revision = graph.status()["revision"]
    assert graph.observe("a", "unity", "same") == generation
    assert graph.status()["revision"] == revision
    graph.observe("c", "code", "pending")
    reopened = GraphStore(graph.path, SCOPE)
    assert [(job["owner"], job["provider"]) for job in reopened.pending()] == [("c", "code")]


def test_failed_scan_retains_previous_edges_with_explicit_staleness(graph):
    publish(graph, "a", "old", ["b"])
    generation = graph.observe("a", "unity", "changed")
    assert graph.fail("a", "unity", generation, "source unavailable")
    edge = graph.query("a")["edges"][0]
    assert edge["target"] == "b"
    assert edge["stale"] is True
    assert graph.status()["errors"] == 1


def test_deletion_keeps_incoming_edges_and_rejects_predelete_publication(graph):
    generation = publish(graph, "a", "a1", ["b"])
    publish(graph, "b", "b1", ["a"])
    graph.delete("a")
    assert not graph.publish("a", "unity", generation, [Edge("c", "serialized")])
    assert graph.query("a")["edges"] == []
    incoming = graph.query("a", direction="incoming")["edges"]
    assert len(incoming) == 1
    assert incoming[0]["owner"] == "b"
    assert incoming[0]["target_exists"] is False


def test_deletion_marks_previously_unobserved_target_as_missing(graph):
    publish(graph, "a", "v1", ["unseen"])
    graph.delete("unseen")
    assert graph.query("unseen", direction="incoming")["edges"][0]["target_exists"] is False


def test_old_external_enumeration_cannot_register_new_owner_under_new_grant(graph):
    graph.set_source("external:settings", {"root": "before"}, expected_version=0)
    graph.set_source("external:settings", {"root": "after"}, expected_version=1)
    with pytest.raises(RevisionConflict):
        graph.observe("new-owner", "external:settings", "old-evidence", source_version=1)
    assert graph.pending() == []
    graph.observe("new-owner", "external:settings", "new-evidence", source_version=2)
    assert graph.pending()[0]["source_version"] == 2


def test_changed_rules_reject_previously_unseen_code_analyzed_under_old_rules(graph):
    graph.put_rule("wrapper", {"function": "Assets.Load"}, expected_version=0)
    with pytest.raises(RevisionConflict):
        graph.observe("new-script", "code", "same-bytes", rules_revision=0)
    rules = graph.rules_state()
    graph.observe("new-script", "code", "same-bytes", rules_revision=rules["revision"])
    assert graph.pending()[0]["rules_revision"] == rules["revision"]


def test_publication_failure_rolls_back_all_previous_edges(graph):
    publish(graph, "a", "old", ["b"])
    generation = graph.observe("a", "unity", "new")

    def broken_stream():
        yield Edge("c", "serialized")
        raise OSError("cancelled file read")

    with pytest.raises(OSError):
        graph.publish("a", "unity", generation, broken_stream())
    assert [e["target"] for e in graph.query("a")["edges"]] == ["b"]
    assert graph.pending()[0]["generation"] == generation


def test_batch_publication_preserves_newer_source_and_publishes_other_sources(graph):
    first, second = graph.observe_batch(
        [Observation("a", "unity", "old"), Observation("b", "unity", "current")]
    )
    graph.observe("a", "unity", "new")
    assert graph.publish_batch(
        [
            ("a", "unity", first, [Edge("wrong", "serialized")]),
            ("b", "unity", second, [Edge("right", "serialized")]),
        ]
    ) == [False, True]
    assert graph.query("a")["edges"] == []
    assert graph.query("b")["edges"][0]["target"] == "right"


def test_process_exit_during_publication_recovers_previous_committed_edges(graph):
    publish(graph, "a", "old", ["b"])
    graph.observe("a", "unity", "pending")
    program = """
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from services.reference_graph.store import Edge, GraphScope, GraphStore
store = GraphStore(Path(sys.argv[2]), GraphScope('user-a', 'editor-a', 'project-a'))
def interrupted():
    yield Edge('uncommitted', 'serialized')
    os._exit(77)
store.publish('a', 'unity', 2, interrupted())
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(Path(__file__).parents[1] / "src"), str(graph.path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 77, result.stderr
    recovered = GraphStore(graph.path, SCOPE)
    assert recovered.query("a")["edges"][0]["target"] == "b"
    assert recovered.pending()[0]["generation"] == 2


def test_concurrent_sessions_share_only_their_exact_scope_and_serialize_writes(tmp_path):
    def update(index):
        store = GraphStore.open(tmp_path, SCOPE)
        publish(store, f"owner-{index}", "v1", ["target"])

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(update, range(12)))
    store = GraphStore.open(tmp_path, SCOPE)
    assert len(store.query("target", direction="incoming")["edges"]) == 12
    moved = store.path.with_name("moved.sqlite")
    store.path.rename(moved)
    assert GraphStore(moved, SCOPE).status()["ready"] == 12


def test_source_revocation_hides_cached_edges_and_rejects_old_worker(graph):
    graph.set_source("external:settings", {"folders": ["GameData"]}, expected_version=0)
    generation = publish(graph, "config", "v1", ["prefab"], provider="external:settings")
    graph.set_source("external:settings", {}, expected_version=1, enabled=False)
    assert graph.query("prefab", direction="incoming")["edges"] == []
    assert not graph.publish("config", "external:settings", generation, [Edge("prefab", "config")])
    assert graph.pending() == []


def test_source_configuration_change_invalidates_existing_worker(graph):
    graph.set_source("external:settings", {"prefix": "before"}, expected_version=0)
    publish(graph, "config", "initial", ["previous-root-asset"], provider="external:settings")
    old = graph.observe("config", "external:settings", "unchanged", source_version=1)
    graph.set_source("external:settings", {"prefix": "after"}, expected_version=1)
    assert not graph.publish("config", "external:settings", old, [Edge("before", "config")])
    assert graph.pending()[0]["generation"] > old
    assert graph.query("previous-root-asset", direction="incoming")["edges"] == []


def test_moving_unchanged_file_updates_observation_path(graph):
    old = graph.observe("a", "unity", "same-bytes", path="Assets/Before.asset")
    current = graph.observe("a", "unity", "same-bytes", path="Assets/After.asset")
    assert current > old
    assert graph.pending()[0]["path"] == "Assets/After.asset"


def test_deleted_and_recreated_declaration_cannot_accept_old_update(graph):
    graph.put_declaration("rule", "a", Edge("first", "declared"), expected_version=0)
    graph.remove_declaration("rule", expected_version=1)
    graph.put_declaration("rule", "a", Edge("second", "declared"), expected_version=2)
    with pytest.raises(RevisionConflict):
        graph.put_declaration("rule", "a", Edge("late", "declared"), expected_version=1)
    assert graph.declarations()[0]["version"] == 3


def test_manual_declarations_survive_rebuild_and_require_matching_version(graph):
    publish(graph, "a", "v1", ["generated"])
    graph.put_declaration("rule-1", "a", Edge("manual", "declared"), expected_version=0)
    with pytest.raises(RevisionConflict):
        graph.put_declaration("rule-1", "a", Edge("lost", "declared"), expected_version=0)
    graph.rebuild()
    assert [e["target"] for e in graph.query("a")["edges"]] == ["manual"]
    assert graph.query("a")["edges"][0]["confidence"] == "declared"
    assert graph.declarations()[0]["version"] == 1
    with pytest.raises(RevisionConflict):
        graph.remove_declaration("rule-1", expected_version=0)
    graph.remove_declaration("rule-1", expected_version=1)
    assert graph.query("a")["edges"] == []


def test_paginated_fanout_is_complete_and_rejects_mixed_revision(graph):
    publish(graph, "a", "v1", [f"target-{i}" for i in range(2101)])
    seen = set()
    cursor = None
    progress = FixtureProgress(2101 + 32, "store fanout fixture")
    while True:
        progress.step(cursor)
        page = graph.query("a", limit=100, cursor=cursor)
        seen.update(e["target"] for e in page["edges"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 2101
    first = graph.query("a", limit=2)
    publish(graph, "a", "v2", ["new"])
    with pytest.raises(RevisionConflict):
        graph.query("a", limit=2, cursor=first["next_cursor"])


@pytest.mark.parametrize(
    "scope",
    [
        GraphScope("other", "editor-a", "project-a"),
        GraphScope("user-a", "other", "project-a"),
        GraphScope("user-a", "editor-a", "other"),
    ],
)
def test_graph_identity_prevents_cross_scope_database_reuse(graph, scope):
    with pytest.raises(ValueError, match="identity"):
        GraphStore(graph.path, scope)
    assert GraphStore(graph.path, SCOPE).status()["revision"] == 0


def test_scope_keys_separate_data_grants_and_cursors(tmp_path):
    first = GraphStore.open(tmp_path, SCOPE)
    second = GraphStore.open(tmp_path, GraphScope("user-b", "editor-a", "project-a"))
    assert first.path != second.path
    first.set_source("external:settings", {"root": "allowed"}, expected_version=0)
    assert not any(source["provider"] == "external:settings" for source in second.sources())
    publish(first, "a", "v1", ["b", "c"])
    publish(second, "a", "v1", ["d", "e"])
    cursor = first.query("a", limit=1)["next_cursor"]
    with pytest.raises(ValueError, match="cursor"):
        second.query("a", cursor=cursor)
    assert {edge["target"] for edge in second.query("a")["edges"]} == {"d", "e"}


def test_unowned_database_is_preserved(tmp_path):
    path = tmp_path / "unrelated.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE unrelated(value)")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="owned"):
        GraphStore(path, SCOPE)
    assert path.read_bytes() == before


def test_symlink_store_path_cannot_write_outside_owned_directory(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable for this account")
    with pytest.raises(ValueError, match="link"):
        GraphStore(link / "graph.sqlite", SCOPE)
    assert list(actual.iterdir()) == []
