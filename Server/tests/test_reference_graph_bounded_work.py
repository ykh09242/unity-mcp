"""Synthetic bounded-work and cancellation regressions; no project data is read."""

import asyncio
from functools import partial
from threading import Event

import pytest

from services.reference_graph.store import Edge, GraphScope, GraphStore, Observation, _hash
from services.reference_graph.workspace import GraphWorkspace


def seed_external(workspace, count, *, with_edges=False):
    provider = "external:bounded"
    workspace.set_source(
        provider, {"root": "/synthetic", "include": ["*.json"]}, expected_version=0
    )
    assert workspace.acquire("worker")
    workspace.record_external_many(
        "worker",
        [
            (
                provider,
                provider + ":" + _hash(f"{i}.json"),
                f"{i}.json",
                "fp",
                "stamp",
                "key",
                "old",
            )
            for i in range(count)
        ],
    )
    if with_edges:
        for start in range(0, count, 128):
            observations = [
                Observation(
                    provider + ":" + _hash(f"{i}.json"),
                    provider,
                    "fp",
                    path=f"{i}.json",
                    source_version=1,
                )
                for i in range(start, min(start + 128, count))
            ]
            generations = workspace.store.observe_batch(observations)
            assert all(
                workspace.store.publish_batch(
                    [
                        (
                            observation.owner,
                            provider,
                            generation,
                            [Edge("shared-target", "external")],
                        )
                        for observation, generation in zip(observations, generations)
                    ]
                )
            )
    return provider


def test_external_prior_metadata_has_keyset_pages(tmp_path):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("u", "e", "p")))
    provider = seed_external(workspace, 601)
    after, owners, sizes = "", [], []
    while True:
        page = workspace.external_input_page(provider, after=after, unseen="new")
        if not page:
            break
        sizes.append(len(page))
        owners.extend(row["owner"] for row in page.values())
        after = next(reversed(page.values()))["owner"]
    assert sizes == [256, 256, 89]
    assert len(set(owners)) == 601


def test_external_cleanup_checks_between_pages_and_rolls_back(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("u", "e", "p"))
    workspace = GraphWorkspace(store)
    provider = seed_external(workspace, 601)
    revision = store.revision()
    checks = []

    def interrupt():
        checks.append(True)
        if len(checks) == 2:
            raise InterruptedError("stop cleanup")

    with pytest.raises(InterruptedError, match="stop cleanup"):
        workspace.finish_external("worker", provider, "new", check=interrupt)
    assert len(workspace.external_inputs(provider)) == 601
    assert store.revision() == revision
    workspace.finish_external("worker", provider, "new")
    assert workspace.external_inputs(provider) == {}


@pytest.mark.parametrize("operation", ["query", "impact", "snapshot"])
def test_excluded_manual_candidates_continue_without_unbounded_filter_scan(tmp_path, operation):
    from services.reference_graph.queries import GraphQueries

    store = GraphStore.open(tmp_path, GraphScope("u", "e", "p"))
    queries = GraphQueries(store)
    with store._connect(write=True) as db:
        for i in range(701):
            target = f"hidden:{i:04}"
            store._insert_edge(db, "root", "manual", Edge(target, "declared"), f"declared:{i:04}")
            db.execute("INSERT INTO graph_excluded_nodes VALUES (?,?)", (target, "private"))
        store._insert_edge(db, "root", "manual", Edge("visible", "declared"), "declared:9999")
    if operation == "snapshot":
        call = lambda cursor: queries.snapshot(limit=10, cursor=cursor)
    elif operation == "impact":
        call = lambda cursor: queries.impact("root", direction="outgoing", limit=10, cursor=cursor)
    else:
        call = lambda cursor: store.query("root", limit=10, cursor=cursor)
    first = call(None)
    assert first["edges"] == []
    assert first["next_cursor"], "Filtered candidates must advance a truthful continuation"
    cursor, edges, pages = first["next_cursor"], [], 1
    while cursor:
        assert pages < 100
        page = call(cursor)
        if operation != "query":
            assert call(cursor) == page
        edges.extend(page["edges"])
        cursor, pages = page["next_cursor"], pages + 1
    assert [row["target"] for row in edges] == ["visible"]


def test_sql_work_cancellation_composes_with_writer_meter(tmp_path, monkeypatch):
    from services.reference_graph.store import guard_graph_work
    from services.reference_graph.storage_budget import WriteMeter

    store = GraphStore.open(tmp_path, GraphScope("u", "e", "p"))
    samples, checks = [], []
    sample = WriteMeter.sample

    def sampled(meter):
        samples.append(True)
        return sample(meter)

    def check():
        checks.append(True)
        if len(checks) >= 3:
            raise InterruptedError("stop SQL")

    monkeypatch.setattr(WriteMeter, "sample", sampled)
    with pytest.raises(InterruptedError, match="stop SQL"), guard_graph_work(check):
        with store._connect(write=True) as db:
            db.execute("CREATE TABLE cancellation_probe(n INTEGER)")
            db.execute(
                "WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<1000000) INSERT INTO cancellation_probe SELECT n FROM x"
            )
    assert samples and len(checks) == 3
    with store._connect() as db:
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='cancellation_probe'").fetchone()
            is None
        )


def test_sql_guard_inside_borrowed_snapshot_restores_outer_guard(tmp_path):
    from services.reference_graph.store import guard_graph_work

    store = GraphStore.open(tmp_path, GraphScope("u", "e", "p"))
    sql = "WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<10000) SELECT sum(n) FROM x"
    outer, inner = [], []

    def check_outer():
        outer.append(True)

    def check_inner():
        inner.append(True)
        if len(inner) > 1:
            raise InterruptedError("inner stopped")

    with store.read_snapshot(), guard_graph_work(check_outer):
        with pytest.raises(InterruptedError, match="inner stopped"), guard_graph_work(check_inner):
            with store._connect() as db:
                db.execute(sql).fetchone()
        with store._connect() as db:
            assert db.execute(sql).fetchone()[0] == 50005000
    assert len(inner) == 3 and len(outer) > 1


@pytest.mark.asyncio
async def test_cancelled_sql_thread_is_drained_before_returning(tmp_path):
    from services.reference_graph.reconcile import cancellable_thread

    store = GraphStore.open(tmp_path, GraphScope("u", "e", "p"))
    started, finished = Event(), Event()

    def work():
        try:
            with store._connect(write=True) as db:
                db.execute("CREATE TABLE cancelled_worker(n INTEGER)")
                started.set()
                db.execute(
                    "WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<10000000) INSERT INTO cancelled_worker SELECT n FROM x"
                )
        finally:
            finished.set()

    task = asyncio.create_task(cancellable_thread(work))
    assert await asyncio.to_thread(started.wait, 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    with store._connect() as db:
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='cancelled_worker'").fetchone()
            is None
        )


def test_cancelled_cleanup_restart_matches_uninterrupted_digest(tmp_path):
    def digest(store):
        with store._connect() as db:
            return _hash(
                {
                    "metadata": [
                        tuple(row)
                        for row in db.execute("SELECT * FROM graph_external ORDER BY owner")
                    ],
                    "edges": [tuple(row) for row in db.execute("SELECT * FROM edges ORDER BY id")],
                    "observations": [
                        tuple(row)
                        for row in db.execute(
                            "SELECT owner,provider,generation,state FROM observations ORDER BY owner,provider"
                        )
                    ],
                }
            )

    def stop(calls):
        calls.append(True)
        if len(calls) == 2:
            raise InterruptedError("restart")

    digests = []
    for name in ("clean", "restarted"):
        store = GraphStore.open(tmp_path / name, GraphScope("u", "e", "p"))
        workspace = GraphWorkspace(store)
        provider = seed_external(workspace, 601, with_edges=True)
        removed_owner = provider + ":" + _hash("0.json")
        store.put_declaration(
            "kept-incoming", "manual-owner", Edge(removed_owner, "declared"), expected_version=0
        )
        store.put_declaration(
            "kept-owned", removed_owner, Edge("manual-target", "declared"), expected_version=0
        )
        # Retain a known subset in both runs; final deletion must produce the
        # same graph after rollback, reopening and worker lease reacquisition.
        with workspace._write("worker") as db:
            db.execute("UPDATE graph_external SET seen='new' WHERE path IN ('1.json','2.json')")
        if name == "restarted":
            before = digest(store)
            calls = []

            with pytest.raises(InterruptedError, match="restart"):
                workspace.finish_external("worker", provider, "new", check=partial(stop, calls))
            assert digest(store) == before
            assert store.query("manual-owner")["edges"][0]["target_exists"] is True
            assert len(store.query(removed_owner)["edges"]) == 2
            workspace.release("worker")
            store = GraphStore(store.path, store.scope)
            workspace = GraphWorkspace(store)
            assert workspace.acquire("worker")
        workspace.finish_external("worker", provider, "new")
        assert set(workspace.external_inputs(provider)) == {"1.json", "2.json"}
        assert {
            edge["owner"] for edge in store.query("shared-target", direction="incoming")["edges"]
        } == {provider + ":" + _hash(path) for path in ("1.json", "2.json")}
        assert store.query("manual-owner")["edges"][0]["target_exists"] is False
        assert [edge["id"] for edge in store.query(removed_owner)["edges"]] == [
            "declared:kept-owned"
        ]
        digests.append(digest(store))
    assert digests[0] == digests[1]
