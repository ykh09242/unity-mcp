"""Durable admission and shared snapshot evidence preserve existing graph work."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from services.reference_graph.queries import GraphQueries
from services.reference_graph.store import Edge, GraphScope, GraphStore


def capture(queries, name="saved", limit=100):
    cursor = None
    while True:
        result = queries.snapshot(name=name, limit=limit, cursor=cursor)
        cursor = result["next_cursor"]
        if cursor is None:
            return result["snapshot"]["id"]


def test_repeated_ready_snapshots_share_evidence_and_survive_original_deletion(tmp_path):
    # Given a published graph and an independently saved snapshot.
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    store.put_declaration(
        "manual",
        "owner",
        Edge("target", "declared", evidence="unique-evidence" * 5000),
        expected_version=0,
    )
    queries = GraphQueries(store)
    original = capture(queries)

    # When the unchanged graph is saved with distinct names and its first name is deleted.
    aliases = [capture(queries, f"saved-{index}") for index in range(20)]
    assert queries.delete_snapshot(original)
    reopened = GraphQueries(GraphStore.open(tmp_path, store.scope))

    # Then evidence has one physical copy and every saved alias remains usable.
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_snapshot_edges").fetchone()[0] == 1
        assert db.execute("SELECT max(length(result)) FROM graph_queries").fetchone()[0] < 5000
    assert len(set(aliases)) == 20
    assert reopened.diff(aliases[0], aliases[-1])["changes"] == []
    assert store.declarations()[0]["id"] == "manual"


def test_concurrent_captures_merge_backing_and_last_alias_reclaims_rows(tmp_path):
    # Given two captures begun before either has completed.
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    for index in range(3):
        store.put_declaration(str(index), "owner", Edge(str(index), "declared"), expected_version=0)
    queries = GraphQueries(store)
    first = queries.snapshot(name="first", limit=1)
    second = queries.snapshot(name="second", limit=1)

    # When both finish, then both names refer to one copy of the graph.
    for name, page in (("first", first), ("second", second)):
        while page["next_cursor"]:
            page = queries.snapshot(name=name, limit=1, cursor=page["next_cursor"])
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_snapshot_edges").fetchone()[0] == 3
    queries.delete_snapshot(first["snapshot"]["id"])
    queries.delete_snapshot(second["snapshot"]["id"])
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_snapshot_edges").fetchone()[0] == 0


def test_scope_admission_survives_restart_and_concurrent_identity_rotation(tmp_path, monkeypatch):
    # Given a root with a finite operator-owned scope allowance.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_SCOPES", "4")
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_USER_SCOPES", "2")

    def open_scope(index):
        try:
            return GraphStore.open(tmp_path, GraphScope("user", str(index), "project"))
        except ValueError as error:
            assert "budget" in str(error).lower()
            return None

    # When parallel callers rotate attacker-controlled identities.
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [store for store in pool.map(open_scope, range(12)) if store is not None]

    # Then only two durable scopes exist, with existing scopes still reopenable.
    assert len(accepted) == 2
    assert len(list(tmp_path.glob("[!.]*.sqlite"))) == 2
    for store in accepted:
        assert GraphStore.open(tmp_path, store.scope).status()["backend"] == "sqlite"
    other = GraphStore.open(tmp_path, GraphScope("other-user", "editor", "project"))
    assert other.status()["backend"] == "sqlite"
    assert open_scope(100) is None
    GraphStore.open(tmp_path, GraphScope("third-user", "editor", "project"))
    with pytest.raises(ValueError, match="(?i)budget"):
        GraphStore.open(tmp_path, GraphScope("fourth-user", "editor", "project"))


@pytest.mark.parametrize("interval", [1000, 5000, 10000])
def test_full_store_rolls_back_without_losing_previous_manual_evidence(
    tmp_path, monkeypatch, interval
):
    # Given existing saved evidence and a deliberately tiny test-only disk allowance.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_RESERVATION_BYTES", str(256 * 1024))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_SCOPE_BYTES", str(256 * 1024))
    from services.reference_graph import storage_budget

    monkeypatch.setattr(storage_budget, "PROGRESS_INSTRUCTIONS", interval)
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    store.put_declaration("saved", "owner", Edge("old", "declared"), expected_version=0)
    queries = GraphQueries(store)
    saved = capture(queries)

    # When a replacement cannot fit, then it fails atomically and old work is queryable.
    with pytest.raises(ValueError, match="(?i)budget"):
        store.put_declaration(
            "saved", "owner", Edge("new", "declared", evidence="x" * 500_000), expected_version=1
        )
    reopened = GraphStore.open(tmp_path, store.scope)
    assert reopened.declarations()[0]["edge"]["target"] == "old"
    assert GraphQueries(reopened).diff(saved, saved)["changes"] == []
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_existing_larger_graph_stays_queryable_after_operator_lowers_budget(tmp_path, monkeypatch):
    # Given legitimate saved work that predates a smaller operator allowance.
    scope = GraphScope("user", "editor", "project")
    store = GraphStore.open(tmp_path, scope)
    store.put_declaration(
        "saved", "owner", Edge("target", "declared", evidence="x" * 300_000), expected_version=0
    )
    saved = capture(GraphQueries(store))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_SCOPE_BYTES", str(256 * 1024))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_BYTES", str(256 * 1024))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_USER_BYTES", str(256 * 1024))

    # When the existing scope reopens, then both authored work and snapshots survive.
    reopened = GraphStore.open(tmp_path, scope)
    assert reopened.query("owner")["edges"][0]["target"] == "target"
    assert GraphQueries(reopened).diff(saved, saved)["changes"] == []
    with pytest.raises(ValueError, match="(?i)budget"):
        GraphStore.open(tmp_path, GraphScope("user", "new-editor", "project"))


def test_snapshot_sharing_invalidates_on_manual_edits_and_source_grants(tmp_path):
    # Given a snapshot with both authored evidence and a source grant.
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    store.set_source("external:test", {}, expected_version=0)
    store.put_declaration("manual", "owner", Edge("first", "declared"), expected_version=0)
    queries = GraphQueries(store)
    before = capture(queries)

    # When authored evidence changes, then the next snapshot records the change.
    store.put_declaration("manual", "owner", Edge("second", "declared"), expected_version=1)
    after = capture(queries)
    assert queries.diff(before, after)["changes"][0]["after"]["target"] == "second"
    store.set_source("external:test", {}, expected_version=1, enabled=False)
    changed_grant = capture(queries)
    with pytest.raises(ValueError, match="source grant changed"):
        queries.diff(after, changed_grant)


def test_pinned_reader_allows_bounded_writes_then_recovers_after_backpressure(
    tmp_path, monkeypatch
):
    # Given a reader holding an old WAL snapshot while writers replace the same declaration.
    capacity = 512 * 1024
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_RESERVATION_BYTES", str(capacity))
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_STORAGE_MAX_SCOPE_BYTES", str(capacity))
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    store.put_declaration("saved", "owner", Edge("old", "declared"), expected_version=0)
    reader = sqlite3.connect(store.path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM declarations").fetchall()
    version = 1
    try:
        # When the pinned WAL reaches its allowance, writes stop without blocking reads.
        for _ in range(100):
            try:
                store.put_declaration(
                    "saved",
                    "owner",
                    Edge(str(version), "declared", evidence="x" * 20_000),
                    expected_version=version,
                )
            except ValueError as error:
                assert "reader" in str(error).lower()
                break
            version += 1
        else:
            pytest.fail("A pinned reader allowed unbounded WAL growth")
        assert version > 2
        assert store.query("owner")["edges"][0]["target"] == str(version - 1)
        assert sum(path.stat().st_size for path in tmp_path.iterdir() if path.is_file()) < (
            6 * capacity + 1024 * 1024
        )
    finally:
        reader.close()

    # Then releasing the reader permits retry and keeps the prior authored version valid.
    assert (
        store.put_declaration(
            "saved", "owner", Edge("recovered", "declared"), expected_version=version
        )
        == version + 1
    )


def test_redundant_revision_changes_do_not_duplicate_identical_snapshot_payloads(tmp_path):
    # Given a snapshot of a declaration that is rewritten without changing its evidence.
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    queries = GraphQueries(store)
    saved = []

    # When each distinct revision is saved, then unchanged payloads still share storage.
    for version in range(5):
        store.put_declaration(
            "manual", "owner", Edge("target", "declared"), expected_version=version
        )
        saved.append(capture(queries, str(version)))
    assert queries.diff(saved[0], saved[-1])["changes"] == []
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_snapshot_edges").fetchone()[0] == 1


def test_snapshot_last_page_replay_uses_backing_after_first_alias_is_deleted(tmp_path):
    # Given an immutable backing with a second snapshot captured over several pages.
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    for index in range(3):
        store.put_declaration(str(index), "owner", Edge(str(index), "declared"), expected_version=0)
    queries = GraphQueries(store)
    original = capture(queries)
    page = queries.snapshot(name="alias", limit=2)
    cursor = page["next_cursor"]
    completed = queries.snapshot(name="alias", limit=2, cursor=cursor)

    # When the original alias is deleted, then the last delivered response still replays exactly.
    queries.delete_snapshot(original)
    reopened = GraphQueries(GraphStore.open(tmp_path, store.scope))
    assert reopened.snapshot(name="alias", limit=2, cursor=cursor) == completed


def test_legacy_snapshot_survives_auxiliary_schema_upgrade_and_rollback(tmp_path, monkeypatch):
    # Given an older database containing a saved snapshot but no sharing tables.
    from services.reference_graph import snapshot_storage

    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    store.put_declaration("saved", "owner", Edge("target", "declared"), expected_version=0)
    saved = capture(GraphQueries(store))
    with store._connect(write=True) as db:
        for table in ("graph_snapshot_refs", "graph_snapshot_contents", "graph_snapshot_versions"):
            db.execute(f"DROP TABLE {table}")
    initialize = snapshot_storage.initialize

    def interrupted(connection):
        initialize(connection)
        raise RuntimeError("migration interrupted")

    # When a schema upgrade fails, then prior snapshots survive both rollback and retry.
    with monkeypatch.context() as patch:
        patch.setattr(snapshot_storage, "initialize", interrupted)
        with pytest.raises(RuntimeError, match="migration interrupted"):
            GraphQueries(store)
    reopened = GraphQueries(GraphStore.open(tmp_path, store.scope))
    assert reopened.diff(saved, saved)["changes"] == []
    assert reopened.delete_snapshot(saved)


def test_snapshot_evidence_format_change_gets_distinct_backing(tmp_path, monkeypatch):
    # Given persisted evidence encoded by one snapshot format.
    from services.reference_graph import snapshot_storage

    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    store.put_declaration("saved", "owner", Edge("target", "declared"), expected_version=0)
    queries = GraphQueries(store)
    before = capture(queries)

    # When the encoder version changes, then it cannot reuse the earlier-format backing.
    monkeypatch.setattr(snapshot_storage, "EVIDENCE_FORMAT", snapshot_storage.EVIDENCE_FORMAT + 1)
    after = capture(queries)
    with store._connect() as db:
        assert snapshot_storage.backing(db, before) != snapshot_storage.backing(db, after)
    assert queries.diff(before, after)["changes"] == []
