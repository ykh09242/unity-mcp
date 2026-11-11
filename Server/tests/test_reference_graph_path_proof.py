"""Durable path proof scales independently from response and validation batches."""

import json
import threading
import time

import pytest

from services.reference_graph.queries import GraphQueries
from services.reference_graph import path_proof, service as service_module
from services.reference_graph.operations import cleanup
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.store import (
    Edge,
    GraphScope,
    GraphStore,
    Observation,
    RevisionConflict,
)
from services.reference_graph.workspace import GraphWorkspace


@pytest.mark.parametrize("revocation", ["grant", "file", "oversize_metadata"])
def test_external_proof_progress_reauthorizes_accumulated_owners(tmp_path, monkeypatch, revocation):
    from services.reference_graph import response_budget
    from services.reference_graph.external import ExternalSource
    from services.reference_graph.store import _json

    root = tmp_path / "source"
    root.mkdir()
    config = {"root": str(root), "include": ["*.json"], "exclude": ["x" * 2500], "mappings": []}
    store = GraphStore.open(tmp_path / "graph", GraphScope("user", "editor", "project"))
    workspace = GraphWorkspace(store)
    store.set_source("external:fixture", config, expected_version=0)
    adapter = ExternalSource(config)
    for index in range(3):
        path = f"{index}.json"
        (root / path).write_text("{}", encoding="utf-8")
        owner = f"owner{index}"
        generation = store.observe_batch(
            [
                Observation(
                    owner,
                    "external:fixture",
                    "one",
                    path=path,
                    source_version=1,
                    provenance={"repository": {"state": "disabled"}},
                )
            ]
        )[0]
        assert store.publish(owner, "external:fixture", generation, [Edge("root", "external")])
        with store._connect(write=True) as db:
            db.execute(
                "INSERT INTO graph_external VALUES (?,?,?,?,?,?,?)",
                ("external:fixture", owner, path, "one", _json(adapter.stamp(path)), "one", "one"),
            )
    revoked = False

    def grant(scope, configuration):
        assert scope == store.scope and configuration == config
        if revoked:
            raise PermissionError("revoked fixture grant")

    service = ReferenceGraphService(tmp_path / "graph")
    # _grant is static and deliberately rechecks the operator boundary itself.
    monkeypatch.setattr(ReferenceGraphService, "_operator_grant", staticmethod(grant))
    monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
    for index in range(3):
        assert not service._validate_external(
            workspace,
            {
                "revision": store.revision(),
                "edges": [{"provider": "external:fixture", "owner": f"owner{index}"}],
            },
        )
    earliest_owner = None
    for restart in range(2):
        queries = GraphQueries(GraphStore.open(tmp_path / "graph", store.scope))
        cursor = None
        for _ in range(12):
            result = queries.path("root", "missing", limit=1, cursor=cursor)
            if cursor is None:
                earliest_owner = next(path_proof.batches(store, result))[0]["owner"]
            assert not service._validate_exploration(workspace, result)
            if cursor is not None:
                replay = queries.path("root", "missing", limit=1, cursor=cursor)
                assert replay == result
                assert not service._validate_exploration(workspace, replay)
            if result["next_cursor"] is None:
                break
            cursor = result["next_cursor"]
        else:
            pytest.fail(f"External proof did not finish after restart {restart}")
        assert result["path_found"] is False
        assert result["_search_proof"]["count"] == 3
    assert earliest_owner is not None
    if revocation == "grant":
        revoked = True
        error = PermissionError
    elif revocation == "file":
        (root / f"{earliest_owner[-1]}.json").unlink()
        error = FileNotFoundError
    else:
        with store._connect(write=True) as db:
            db.execute(
                "UPDATE graph_external SET stamp=? WHERE owner=?", ("x" * 9000, earliest_owner)
            )
        error = response_budget.GraphResponseTooLarge
    with pytest.raises(error):
        service._validate_exploration(workspace, result)


@pytest.mark.parametrize("reachable", [False, True])
def test_high_fanin_runtime_search_finishes_with_bounded_proof_pages(tmp_path, reachable):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    workspace = GraphWorkspace(store)
    queries = workspace.queries()
    with store._connect(write=True) as db:
        db.execute("INSERT INTO sources VALUES ('runtime:fixture',1,1,'{}')")
    owners = [f"owner{index:05}" for index in range(5001)]
    generations = store.observe_batch(
        Observation(owner, "runtime:fixture", "one", path=f"Assets/{owner}.prefab")
        for owner in owners
    )
    assert all(
        store.publish_batch(
            (owner, "runtime:fixture", generation, [Edge("root", "runtime")])
            for owner, generation in zip(owners, generations)
        )
    )
    # A reachable target is only discoverable after the complete incoming fan-in.
    if reachable:
        generation = store.observe("destination", "code", "one")
        store.publish("destination", "code", generation, [Edge(owners[-1], "code")])
    service = ReferenceGraphService(tmp_path)
    cursor = None
    largest = 0
    for page_number in range(150):
        result = queries.path("root", "destination", limit=100, cursor=cursor)
        service._validate_exploration(workspace, result)
        largest = max(largest, len(json.dumps(result)))
        if not result["next_cursor"]:
            break
        cursor = result["next_cursor"]
        # Reopening must retain the proof and the traversal together.
        if page_number == 20:
            queries = GraphQueries(GraphStore.open(tmp_path, store.scope))
    else:
        pytest.fail("High-fan-in path failed to finish")
    assert largest < 20_000
    assert page_number > 50
    assert result["path_found"] is reachable
    assert result["visited_nodes"] >= 5002
    assert queries.path("root", "destination", limit=100, cursor=cursor) == result
    assert queries.release(result["query_id"])
    with pytest.raises(ValueError, match="released query"):
        queries.path("root", "destination", limit=100, cursor=cursor)
    for _ in range(20):
        batch = cleanup(workspace, before=0, limit=1000)
        assert batch["query_scratch_rows_removed"] <= 1000
        if not batch["more"]:
            break
    else:
        pytest.fail("Released high-fan-in proof was not reclaimed")
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_query_proofs").fetchone()[0] == 0


@pytest.fixture
def protected_path(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    workspace = GraphWorkspace(store)
    with store._connect(write=True) as db:
        db.execute("INSERT INTO sources VALUES ('runtime:fixture',1,1,'{}')")
    generation = store.observe("owner", "runtime:fixture", "one", path="Assets/Private.prefab")
    store.publish(
        "owner", "runtime:fixture", generation, [Edge(f"target{i}", "runtime") for i in range(260)]
    )
    result = workspace.queries().path("owner", "missing", direction="outgoing", limit=260)
    return workspace, result


@pytest.mark.parametrize("change", ["source", "policy", "scope", "release"])
def test_durable_path_proof_rechecks_all_fences(protected_path, tmp_path, change):
    workspace, result = protected_path
    service = ReferenceGraphService(tmp_path)
    assert not service._validate_exploration(workspace, result)
    if change == "source":
        with workspace.store._connect(write=True) as db:
            db.execute("UPDATE sources SET enabled=0 WHERE provider='runtime:fixture'")
    elif change == "policy":
        workspace.set_policy({"exclude": ["Assets/Private.prefab"]}, expected_version=0)
    elif change == "scope":
        result["_search_proof"]["scope"] = "foreign"
    else:
        workspace.queries().release(result["query_id"])
    with pytest.raises(RevisionConflict):
        service._validate_exploration(workspace, result)


def test_proof_stream_checks_cancellation_between_batches_and_cleanup_is_bounded(
    protected_path, tmp_path, monkeypatch
):
    workspace, result = protected_path
    service = ReferenceGraphService(tmp_path)
    original = service._validate_external
    stop = threading.Event()
    sizes = []

    def validate(workspace, page):
        if page["edges"]:
            sizes.append(len(page["edges"]))
            stop.set()
        return original(workspace, page)

    monkeypatch.setattr(service, "_validate_external", validate)
    token = service_module._exploration_stop.set(stop)
    try:
        with pytest.raises(InterruptedError):
            service._validate_exploration(workspace, result)
    finally:
        service_module._exploration_stop.reset(token)
    assert sizes == [128]
    workspace.queries().abandon(result["query_id"])
    for _ in range(100):
        removed = cleanup(workspace, before=time.time() + 1, limit=20)
        assert removed["query_scratch_rows_removed"] <= 20
        if not removed["more"]:
            break
    else:
        pytest.fail("Abandoned proof cleanup did not finish")
    with workspace.store._connect() as db:
        assert not db.execute("SELECT 1 FROM graph_query_proofs").fetchone()


def test_legacy_inline_path_proof_migrates_without_losing_prior_evidence(protected_path):
    workspace, first = protected_path
    store = workspace.store
    legacy = {
        **first,
        "_search_evidence": [edge for batch in path_proof.batches(store, first) for edge in batch],
    }
    del legacy["_search_proof"]
    with store._connect(write=True) as db:
        db.execute(
            "UPDATE graph_queries SET result=? WHERE id=?", (json.dumps(legacy), first["query_id"])
        )
        db.execute("DROP TABLE graph_query_proofs")
        db.execute("UPDATE graph_queries_schema SET version=2")
    queries = GraphQueries(store)
    result = queries.path(
        "owner", "missing", direction="outgoing", limit=260, cursor=first["next_cursor"]
    )
    assert sum(map(len, path_proof.batches(store, result))) == 260


def test_proof_counter_migration_backfills_transactionally_then_resumes(
    protected_path, monkeypatch
):
    workspace, first = protected_path
    store, job = workspace.store, first["query_id"]
    with store._connect(write=True) as db:
        db.execute("DROP TRIGGER IF EXISTS graph_query_proof_insert")
        columns = {row[1] for row in db.execute("PRAGMA table_info(graph_queries)")}
        for column in ("proof_count", "proof_through"):
            if column in columns:
                db.execute(f"ALTER TABLE graph_queries DROP COLUMN {column}")
        db.execute("UPDATE graph_queries_schema SET version=5")
    migrate = getattr(path_proof, "initialize_counters", lambda db: None)

    def interrupted(db):
        migrate(db)
        raise InterruptedError("proof migration rollback")

    with monkeypatch.context() as patch:
        patch.setattr(path_proof, "initialize_counters", interrupted, raising=False)
        with pytest.raises(InterruptedError, match="migration"):
            GraphQueries(store)
    with store._connect() as db:
        assert db.execute("SELECT version FROM graph_queries_schema").fetchone()[0] == 5
        assert "proof_count" not in {
            row[1] for row in db.execute("PRAGMA table_info(graph_queries)")
        }
        assert (
            db.execute("SELECT count(*) FROM graph_query_proofs WHERE job=?", (job,)).fetchone()[0]
            == 260
        )
    queries = GraphQueries(GraphStore(store.path, store.scope))
    with store._connect() as db:
        assert db.execute("SELECT version FROM graph_queries_schema").fetchone()[0] == 6
        assert path_proof.marker(db, store.scope.key, job) == first["_search_proof"]
    result = queries.path(
        "owner", "missing", direction="outgoing", limit=260, cursor=first["next_cursor"]
    )
    assert result["_search_proof"] == first["_search_proof"]
    assert (
        queries.path(
            "owner", "missing", direction="outgoing", limit=260, cursor=first["next_cursor"]
        )
        == result
    )
    assert sum(map(len, path_proof.batches(store, result))) == 260


def test_proof_counters_ignore_duplicates_and_rollback_new_evidence(protected_path):
    workspace, result = protected_path
    store, job = workspace.store, result["query_id"]
    with store._connect(write=True) as db:
        row = db.execute(
            "SELECT identity,payload FROM graph_query_proofs WHERE job=? LIMIT 1", (job,)
        ).fetchone()
        before = tuple(
            db.execute(
                "SELECT proof_count,proof_through FROM graph_queries WHERE id=?", (job,)
            ).fetchone()
        )
        db.execute(
            "INSERT OR IGNORE INTO graph_query_proofs(job,identity,payload) VALUES (?,?,?)",
            (job, *row),
        )
        assert (
            tuple(
                db.execute(
                    "SELECT proof_count,proof_through FROM graph_queries WHERE id=?", (job,)
                ).fetchone()
            )
            == before
        )
    with pytest.raises(InterruptedError):
        with store._connect(write=True) as db:
            db.execute(
                "INSERT INTO graph_query_proofs(job,identity,payload) VALUES (?,'new','{}')", (job,)
            )
            assert path_proof.marker(db, store.scope.key, job)["count"] == before[0] + 1
            raise InterruptedError("page failed")
    with store._connect() as db:
        assert path_proof.marker(db, store.scope.key, job) == result["_search_proof"]
        assert not db.execute(
            "SELECT 1 FROM graph_query_proofs WHERE job=? AND identity='new'", (job,)
        ).fetchone()


def test_failed_path_page_rolls_back_proof_and_continuation_together(tmp_path, monkeypatch):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    queries = GraphQueries(store)
    with store._connect(write=True) as db:
        db.execute("INSERT INTO sources VALUES ('runtime:fixture',1,1,'{}')")
        for index in range(3):
            store._insert_edge(
                db, "root", "runtime:fixture", Edge(f"n{index}", "runtime"), f"edge{index}"
            )
    first = queries.path("root", "missing", direction="outgoing", limit=1)

    def fail(*args):
        raise InterruptedError("page delivery admission")

    with monkeypatch.context() as patch:
        patch.setattr(queries, "_finish_page", fail)
        with pytest.raises(InterruptedError, match="page delivery"):
            queries.path(
                "root", "missing", direction="outgoing", limit=1, cursor=first["next_cursor"]
            )
    with store._connect() as db:
        assert path_proof.marker(db, store.scope.key, first["query_id"]) == first["_search_proof"]
        assert db.execute("SELECT count(*) FROM graph_query_proofs").fetchone()[0] == 1
    reopened = GraphQueries(GraphStore(store.path, store.scope))
    result = reopened.path(
        "root", "missing", direction="outgoing", limit=1, cursor=first["next_cursor"]
    )
    assert result["_search_proof"]["count"] == 2
    assert (
        reopened.path("root", "missing", direction="outgoing", limit=1, cursor=first["next_cursor"])
        == result
    )
