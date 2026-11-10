"""SQL-work regressions for live staging and explicit graph backing release."""

from contextlib import contextmanager
import time

import pytest

from services.reference_graph import reclamation, snapshot_storage
from services.reference_graph.operations import cleanup
from services.reference_graph.queries import GraphQueries
from services.reference_graph.store import Edge, GraphScope, GraphStore, Observation
from services.reference_graph.workspace import GraphWorkspace


@contextmanager
def sql_steps(store, monkeypatch):
    counts = [0]
    connect = store._connect

    def progress():
        counts[0] += 1
        return 0

    @contextmanager
    def measured(*args, **kwargs):
        with connect(*args, **kwargs) as db:
            db.set_progress_handler(progress, 1)
            try:
                yield db
            finally:
                db.set_progress_handler(None, 0)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", measured)
        yield counts


@pytest.fixture
def workspace(tmp_path):
    value = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    value.queries()
    return value


def stage(db, worker, count, *, start=0):
    db.executemany(
        "INSERT INTO graph_staging VALUES (?,'owner',1,?,'edge','{}',?)",
        ((worker, str(index), index) for index in range(start, start + count)),
    )


def drain(workspace, monkeypatch, iterations):
    total = maximum = 0
    for _ in range(iterations):
        with sql_steps(workspace.store, monkeypatch) as steps:
            result = cleanup(workspace, before=0, limit=1000)
        maximum = max(maximum, steps[0])
        assert steps[0] < 80000
        assert result["query_scratch_rows_removed"] <= 1000
        total += result["query_scratch_rows_removed"]
        if not result["more"]:
            return total, maximum
    pytest.fail("Released backing did not drain within the fixture's row budget")


@pytest.mark.parametrize("count", [1000, 10000])
def test_live_staging_candidate_work_is_bounded(workspace, monkeypatch, count):
    with workspace.store._connect(write=True) as db:
        db.execute("INSERT INTO graph_worker VALUES (1,'live',?)", (time.time() + 3600,))
        stage(db, "live", count)
        # Sort abandoned owners after the live prefix in the staging PK index.
        stage(db, "z-abandoned", 20)
    for page in range(3):
        with sql_steps(workspace.store, monkeypatch) as steps:
            result = cleanup(workspace, before=10**12, limit=10)
        print(f"staging count={count} page={page} sql_steps={steps[0]}")
        assert steps[0] < 5000
        assert result["more"]
        assert result["abandoned_staging_removed"] == 0
    with workspace.store._connect() as db:
        assert (
            db.execute("SELECT count(*) FROM graph_staging WHERE worker='live'").fetchone()[0]
            == count
        )
    workspace = GraphWorkspace(GraphStore(workspace.store.path, workspace.store.scope))
    removed = 0
    for _ in range(count // 1000 + 5):
        with sql_steps(workspace.store, monkeypatch) as steps:
            result = cleanup(workspace, before=0, limit=1000)
        assert steps[0] < 30000
        removed += result["abandoned_staging_removed"]
        if not result["more"]:
            break
    else:
        pytest.fail("Large live staging prevented eventual abandoned-row reclamation")
    assert removed == 20
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_staging").fetchone()[0] == count


@pytest.mark.parametrize("count", [1000, 10000])
def test_reclamation_queue_candidate_work_is_bounded(workspace, monkeypatch, count):
    with workspace.store._connect(write=True) as db:
        db.executemany(
            "INSERT INTO graph_reclamation VALUES ('query',?)",
            ((f"released{index}",) for index in range(count)),
        )
    for _ in range(3):
        with sql_steps(workspace.store, monkeypatch) as steps:
            result = cleanup(workspace, before=0, limit=10)
        print(f"queue count={count} sql_steps={steps[0]}")
        assert steps[0] < 5000 and result["more"]
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_reclamation").fetchone()[0] == count - 30


@pytest.mark.parametrize("change", ["expire", "replace"])
def test_staging_sweep_restarts_for_worker_change_and_survives_restart(workspace, change):
    store = workspace.store
    with store._connect(write=True) as db:
        db.execute("INSERT INTO graph_worker VALUES (1,'live',?)", (time.time() + 3600,))
        stage(db, "live", 25)
        stage(db, "abandoned", 5)
    assert cleanup(workspace, before=10**12, limit=4)["more"]
    with store._connect(write=True) as db:
        if change == "expire":
            db.execute("UPDATE graph_worker SET expires=0")
        else:
            db.execute("UPDATE graph_worker SET owner='new'")
            stage(db, "new", 10)
    workspace = GraphWorkspace(GraphStore(store.path, store.scope))
    total = 0
    for _ in range(20):
        result = cleanup(workspace, before=10**12, limit=4)
        total += result["abandoned_staging_removed"]
        assert result["abandoned_staging_removed"] <= 4
        if not result["more"]:
            break
    else:
        pytest.fail("Staging sweep did not finish after the protected owner changed")
    assert total == 30
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_staging").fetchone()[0] == (
            10 if change == "replace" else 0
        )


def test_staging_sweep_finishes_despite_continuing_publications(workspace):
    with workspace.store._connect(write=True) as db:
        db.execute("INSERT INTO graph_worker VALUES (1,'live',?)", (time.time() + 3600,))
        stage(db, "live", 40)
        stage(db, "abandoned", 7)
    total = 0
    for iteration in range(20):
        result = cleanup(workspace, before=10**12, limit=5)
        total += result["abandoned_staging_removed"]
        with workspace.store._connect(write=True) as db:
            stage(db, "live", 10, start=40 + iteration * 10)
        if not result["more"]:
            break
    else:
        pytest.fail("A continuing live publisher prevented sweep completion")
    assert total == 7
    with workspace.store._connect() as db:
        assert (
            db.execute("SELECT count(*) FROM graph_staging WHERE worker='live'").fetchone()[0]
            == 40 + (iteration + 1) * 10
        )


def test_cleanup_during_streamed_publication_preserves_every_edge(workspace, monkeypatch):
    assert workspace.acquire("publisher")
    generation = workspace.observe("publisher", Observation("owner", "code", "new"))

    def events():
        for index in range(2048):
            if index and index % 256 == 0:
                with sql_steps(workspace.store, monkeypatch) as steps:
                    result = cleanup(workspace, before=10**12, limit=10)
                assert steps[0] < 5000
                assert result["abandoned_staging_removed"] == 0
            yield Edge(f"target{index}", "load")

    workspace.publish_events("publisher", "owner", generation, events())
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM edges WHERE owner='owner'").fetchone()[0] == 2048
        assert db.execute("SELECT count(*) FROM graph_staging").fetchone()[0] == 0
        assert (
            db.execute("SELECT state FROM observations WHERE owner='owner'").fetchone()[0]
            == "ready"
        )


def seed_scratch(db, identity, count):
    db.executemany(
        "INSERT INTO graph_query_proofs(job,identity,payload) VALUES (?,?,'{}')",
        ((identity, f"proof{index}") for index in range(count)),
    )
    db.executemany(
        "INSERT INTO graph_query_nodes VALUES (?,?,0,NULL)",
        ((identity, f"scratch{index}") for index in range(count)),
    )
    db.executemany(
        "INSERT INTO graph_query_queue(job,node) VALUES (?,?)",
        ((identity, f"scratch{index}") for index in range(count)),
    )


@pytest.mark.parametrize("count", [1000, 10000])
def test_explicit_query_release_invalidates_now_and_reclaims_bounded(workspace, monkeypatch, count):
    store, queries = workspace.store, workspace.queries()
    store.put_declaration("edge", "root", Edge("leaf", "manual"), expected_version=0)
    page = queries.impact("root", direction="outgoing", limit=1)
    identity = page["query_id"]
    with store._connect(write=True) as db:
        seed_scratch(db, identity, count)
    with sql_steps(store, monkeypatch) as steps:
        assert queries.release(identity)
    print(f"release query count={count} sql_steps={steps[0]}")
    assert steps[0] < 1000
    assert not queries.release(identity)
    with pytest.raises(ValueError, match="released query"):
        queries.impact("root", direction="outgoing", limit=1, cursor=page["next_cursor"])
    with store._connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM graph_query_proofs WHERE job=?", (identity,)
            ).fetchone()[0]
            == count
        )
    for _ in range(3):
        with sql_steps(store, monkeypatch) as steps:
            result = cleanup(workspace, before=0, limit=10)
        print(f"reclaim query count={count} sql_steps={steps[0]}")
        assert steps[0] < 5000
        assert result["query_scratch_rows_removed"] == 10 and result["more"]
    workspace = GraphWorkspace(GraphStore(store.path, store.scope))
    total, maximum = drain(workspace, monkeypatch, 3 * count // 1000 + 5)
    print(f"drained query count={count} rows={total} max_sql_steps={maximum}")
    with store._connect() as db:
        for table in ("graph_query_proofs", "graph_query_nodes", "graph_query_queue"):
            assert not db.execute(f"SELECT 1 FROM {table} WHERE job=?", (identity,)).fetchone()


@pytest.mark.parametrize("count", [1000, 10000])
def test_final_snapshot_alias_release_is_bounded_and_preserves_shared_backing(
    workspace, monkeypatch, count
):
    store, queries = workspace.store, workspace.queries()
    original = queries.snapshot(name="original")["snapshot"]["id"]
    alias = queries.snapshot(name="alias")["snapshot"]["id"]
    with store._connect(write=True) as db:
        db.executemany(
            "INSERT INTO graph_snapshot_edges VALUES (?,?,'{}')",
            ((original, f"edge{index}") for index in range(count)),
        )
        db.executemany(
            "INSERT INTO graph_snapshot_versions VALUES (?,?)",
            ((f"old-version{index}", original) for index in range(count)),
        )
    assert queries.delete_snapshot(original)
    with store._connect() as db:
        assert snapshot_storage.backing(db, alias) == original
        assert db.execute("SELECT count(*) FROM graph_snapshot_edges").fetchone()[0] == count
    with sql_steps(store, monkeypatch) as steps:
        assert queries.delete_snapshot(alias)
    print(f"release snapshot count={count} sql_steps={steps[0]}")
    assert steps[0] < 1500
    assert not queries.delete_snapshot(alias)
    with pytest.raises(ValueError, match="not ready"):
        queries.diff(alias, alias)
    workspace = GraphWorkspace(GraphStore(store.path, store.scope))
    fresh = workspace.queries().snapshot(name="fresh")["snapshot"]["id"]
    with store._connect() as db:
        assert snapshot_storage.backing(db, fresh) != original
    for _ in range(3):
        with sql_steps(workspace.store, monkeypatch) as steps:
            result = cleanup(workspace, before=0, limit=10)
        print(f"reclaim snapshot count={count} sql_steps={steps[0]}")
        assert steps[0] < 5000
        assert result["query_scratch_rows_removed"] == 10 and result["more"]
    assert workspace.queries().diff(fresh, fresh)["changes"] == []

    total, maximum = drain(workspace, monkeypatch, 2 * count // 1000 + 5)
    print(f"drained snapshot count={count} rows={total} max_sql_steps={maximum}")
    with store._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM graph_snapshot_edges WHERE snapshot=?", (original,)
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM graph_snapshot_versions WHERE backing=?", (original,)
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM graph_snapshot_contents WHERE backing=?", (original,)
        ).fetchone()
    assert workspace.queries().diff(fresh, fresh)["changes"] == []


@pytest.mark.parametrize("count", [1001, 10001])
def test_snapshot_dedup_final_page_defers_bounded_reclamation(workspace, monkeypatch, count):
    store, queries = workspace.store, workspace.queries()
    with store._connect(write=True) as db:
        for index in range(count):
            store._insert_edge(db, "root", "manual", Edge(str(index), "manual"))
    first = queries.snapshot(name="first", limit=1000)
    second = queries.snapshot(name="second", limit=1000)
    for _ in range(count // 1000):
        first = queries.snapshot(name="first", limit=1000, cursor=first["next_cursor"])
    assert first["next_cursor"] is None
    for _ in range(count // 1000 - 1):
        second = queries.snapshot(name="second", limit=1000, cursor=second["next_cursor"])
    cursor, private = second["next_cursor"], second["snapshot"]["id"]
    with sql_steps(store, monkeypatch) as steps:
        completed = queries.snapshot(name="second", limit=1000, cursor=cursor)
    print(f"dedup final count={count} sql_steps={steps[0]}")
    assert steps[0] < 5000
    assert completed["next_cursor"] is None and len(completed["edges"]) == 1
    with store._connect() as db:
        assert snapshot_storage.backing(db, private) == first["snapshot"]["id"]
        assert (
            db.execute(
                "SELECT count(*) FROM graph_snapshot_edges WHERE snapshot=?", (private,)
            ).fetchone()[0]
            == count
        )
    workspace = GraphWorkspace(GraphStore(store.path, store.scope))
    reopened = workspace.queries()
    assert reopened.snapshot(name="second", limit=1000, cursor=cursor) == completed
    for _ in range(count // 100 + 2):
        with sql_steps(workspace.store, monkeypatch) as steps:
            result = cleanup(workspace, before=0, limit=100)
        assert steps[0] < 10000
        assert result["query_scratch_rows_removed"] <= 100
        if not result["more"]:
            break
    else:
        pytest.fail("Discarded snapshot backing did not drain")
    with store._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM graph_snapshot_edges WHERE snapshot=?", (private,)
        ).fetchone()
        assert db.execute("SELECT count(*) FROM graph_snapshot_edges").fetchone()[0] == count
    assert reopened.snapshot(name="second", limit=1000, cursor=cursor) == completed
    assert reopened.diff(first["snapshot"]["id"], private)["changes"] == []


@pytest.mark.parametrize("legacy_snapshot", [False, True])
def test_released_backing_reclaims_fully_after_restart_and_legacy_migration(
    workspace, legacy_snapshot
):
    store, queries = workspace.store, workspace.queries()
    store.put_declaration("edge", "root", Edge("leaf", "manual"), expected_version=0)
    saved = queries.snapshot()["snapshot"]["id"]
    page = queries.impact("root", direction="outgoing", limit=1)
    with store._connect(write=True) as db:
        seed_scratch(db, page["query_id"], 17)
        # Recreate the previous auxiliary schema without touching retained evidence.
        for table in ("graph_reclamation", "graph_staging_cleanup"):
            db.execute(f"DROP TABLE IF EXISTS {table}")
        for index in ("graph_snapshot_contents_backing", "graph_snapshot_versions_backing"):
            db.execute(f"DROP INDEX IF EXISTS {index}")
        if legacy_snapshot:
            for table in (
                "graph_snapshot_refs",
                "graph_snapshot_contents",
                "graph_snapshot_versions",
            ):
                db.execute(f"DROP TABLE {table}")
        db.execute("UPDATE graph_queries_schema SET version=4")
    reopened = GraphQueries(store)
    assert reopened.diff(saved, saved)["changes"] == []
    assert reopened.release(page["query_id"])
    assert reopened.delete_snapshot(saved)
    first = cleanup(workspace, before=0, limit=4)
    assert first["more"]
    workspace = GraphWorkspace(GraphStore(store.path, store.scope))
    for _ in range(30):
        result = cleanup(workspace, before=0, limit=4)
        assert result["query_scratch_rows_removed"] <= 4
        if not result["more"]:
            break
    else:
        pytest.fail("Released backing was not fully reclaimed after restart")
    with store._connect() as db:
        for table in (
            "graph_queries",
            "graph_query_proofs",
            "graph_query_nodes",
            "graph_query_queue",
            "graph_snapshots",
            "graph_snapshot_edges",
            "graph_snapshot_refs",
            "graph_snapshot_contents",
            "graph_snapshot_versions",
            "graph_reclamation",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_reclamation_migration_rolls_back_without_losing_saved_history(workspace, monkeypatch):
    store = workspace.store
    saved = workspace.queries().snapshot()["snapshot"]["id"]
    with store._connect(write=True) as db:
        db.execute("DROP TABLE graph_reclamation")
        db.execute("DROP TABLE graph_staging_cleanup")
        db.execute("UPDATE graph_queries_schema SET version=4")
    initialize = reclamation.initialize

    def interrupted(db):
        initialize(db)
        raise RuntimeError("interrupted migration")

    with monkeypatch.context() as patch:
        patch.setattr(reclamation, "initialize", interrupted)
        with pytest.raises(RuntimeError, match="interrupted migration"):
            GraphQueries(store)
    with store._connect() as db:
        assert db.execute("SELECT version FROM graph_queries_schema").fetchone()[0] == 4
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='graph_reclamation'"
        ).fetchone()
    assert GraphQueries(store).diff(saved, saved)["changes"] == []


def test_release_and_reclamation_rollback_are_atomic(workspace, monkeypatch):
    store, queries = workspace.store, workspace.queries()
    store.put_declaration("edge", "root", Edge("leaf", "manual"), expected_version=0)
    saved = queries.snapshot()["snapshot"]["id"]
    connect = store._connect

    @contextmanager
    def interrupted(*args, **kwargs):
        with connect(*args, **kwargs) as db:
            yield db
            if kwargs.get("write"):
                raise RuntimeError("interrupted transaction")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", interrupted)
        with pytest.raises(RuntimeError, match="interrupted transaction"):
            queries.delete_snapshot(saved)
    assert queries.diff(saved, saved)["changes"] == []
    assert queries.delete_snapshot(saved)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", interrupted)
        with pytest.raises(RuntimeError, match="interrupted transaction"):
            cleanup(workspace, before=0, limit=10)
    with store._connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM graph_snapshot_edges WHERE snapshot=?", (saved,)
            ).fetchone()[0]
            == 1
        )
        assert db.execute(
            "SELECT 1 FROM graph_reclamation WHERE kind='snapshot' AND identity=?", (saved,)
        ).fetchone()
    assert not cleanup(workspace, before=0, limit=10)["more"]


def test_releasing_partial_and_ready_snapshot_queries_preserves_saved_alias(workspace):
    store, queries = workspace.store, workspace.queries()
    for index in range(3):
        store.put_declaration(str(index), "root", Edge(str(index), "manual"), expected_version=0)
    saved = queries.snapshot(name="saved")["snapshot"]["id"]
    partial = queries.snapshot(name="partial", limit=1)
    assert queries.release(partial["query_id"])
    with pytest.raises(ValueError, match="released query"):
        queries.snapshot(name="partial", limit=1, cursor=partial["next_cursor"])
    assert queries.release(saved)
    assert not cleanup(workspace, before=0, limit=10)["more"]
    assert queries.diff(saved, saved)["changes"] == []
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_snapshot_edges").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM graph_snapshot_refs").fetchone()[0] == 1
