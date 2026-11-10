"""Runtime retirement fences visibility before bounded durable reclamation."""

import asyncio
from contextlib import contextmanager
import threading

import pytest

from core.config import config
from services.reference_graph.operations import cleanup
from services.reference_graph.runtime import RuntimeObservations
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.store import Edge, GraphStore, RevisionConflict, check_graph_work
from services.reference_graph.workspace import GraphWorkspace
from tests.test_reference_graph_release_progress import sql_steps, workspace
from tests.test_reference_graph_runtime import Context, Native, event, page
from tests.timing import await_signal


def seed(runtime, identity, count):
    for start in range(0, count, 1000):
        end = min(start + 1000, count)
        runtime.ingest(
            identity,
            start,
            page([event(i + 1, handle=str(i)) for i in range(start, end)], offset=end),
        )


@contextmanager
def transaction_changes(store, monkeypatch):
    changes, connect = [], store._connect

    @contextmanager
    def measured(*args, **kwargs):
        with connect(*args, **kwargs) as db:
            before = db.total_changes
            yield db
            if kwargs.get("write"):
                changes.append(db.total_changes - before)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", measured)
        yield changes


@pytest.mark.parametrize("count", [1000, 10000])
def test_runtime_release_work_is_bounded_and_history_reclaims_after_reopen(
    workspace, monkeypatch, count
):
    store, runtime = workspace.store, RuntimeObservations(workspace)
    identity = runtime.begin()["id"]
    seed(runtime, identity, count)
    other = runtime.begin()["id"]
    runtime.ingest(other, 0, page([event()]))
    store.put_declaration("kept", "authored", Edge("target", "manual"), expected_version=0)
    queries = workspace.queries()
    before = queries.snapshot(limit=1)
    old_edge = store.query(f"runtime:{identity}:1")["edges"][0]["id"]
    with sql_steps(store, monkeypatch) as steps:
        assert runtime.release(identity)
    print(f"runtime release count={count} sql_steps={steps[0]}")
    assert steps[0] < 2000
    assert runtime.release(identity)
    assert [row["id"] for row in runtime.sessions()["captures"]] == [other]
    for method in (runtime.status, runtime.native, runtime.events, runtime.handles):
        with pytest.raises(ValueError, match="Unknown runtime capture"):
            method(identity)
    with pytest.raises(ValueError, match="Unknown runtime capture"):
        runtime.ingest(identity, count, page([], offset=count))
    assert not store.query(f"runtime:{identity}:1")["edges"]
    assert not queries.explain(old_edge)["edges"]
    assert not queries.impact(f"runtime:{identity}:1", direction="outgoing")["edges"]
    assert not queries.path(f"runtime:{identity}:1", "target", direction="outgoing")["edges"]
    with pytest.raises(RevisionConflict):
        queries.snapshot(limit=1, cursor=before["next_cursor"])
    with store._connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM graph_runtime_events WHERE session=?", (identity,)
            ).fetchone()[0]
            == count
        )
        assert not db.execute(
            "SELECT 1 FROM sources WHERE provider=?", ("runtime:" + identity,)
        ).fetchone()
    workspace = GraphWorkspace(GraphStore(store.path, store.scope))
    maximum_steps = maximum_changes = 0
    for _ in range(count // 10 + 50):
        with transaction_changes(workspace.store, monkeypatch) as changes:
            with sql_steps(workspace.store, monkeypatch) as steps:
                result = cleanup(workspace, before=0, limit=100)
        assert result["query_scratch_rows_removed"] <= 100
        # Two maintenance cursor rows and at most one retired queue identity.
        assert all(changed <= 103 for changed in changes)
        assert steps[0] < 50000
        maximum_steps = max(maximum_steps, steps[0])
        maximum_changes = max(maximum_changes, *changes)
        if not result["more"]:
            break
    else:
        pytest.fail("Retired runtime history did not drain")
    print(
        f"runtime reclaim count={count} max_sql_steps={maximum_steps} max_changes={maximum_changes}"
    )
    with store._connect() as db:
        for table in ("edges", "observations", "evidence_context"):
            assert not db.execute(
                f"SELECT 1 FROM {table} WHERE provider=?", ("runtime:" + identity,)
            ).fetchone()
        for table in (
            "graph_runtime_events",
            "graph_runtime_handles",
            "graph_runtime_bindings",
            "graph_runtime_blocked_handles",
        ):
            assert not db.execute(f"SELECT 1 FROM {table} WHERE session=?", (identity,)).fetchone()
        assert not db.execute(
            "SELECT 1 FROM nodes WHERE id LIKE ?", (f"runtime:{identity}:%",)
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM graph_excluded_nodes WHERE id LIKE ?", (f"runtime:{identity}:%",)
        ).fetchone()
    assert RuntimeObservations(workspace).status(other)["events"] == 1
    assert workspace.store.declarations()[0]["id"] == "kept"
    assert RuntimeObservations(workspace).release(identity)


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.asyncio
async def test_runtime_service_release_cooperates_with_cancellation(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    service = ReferenceGraphService(tmp_path, send=Native())
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    ctx = Context(service)
    entered, proceed, stopped = asyncio.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    release = RuntimeObservations.release

    def held(self, identity):
        if phase == "after":
            release(self, identity)
        loop.call_soon_threadsafe(entered.set)
        assert proceed.wait(10), "release test gate was not opened"
        try:
            check_graph_work()
        except InterruptedError:
            stopped.set()
            raise
        return release(self, identity)

    try:
        started = await service.execute(ctx, "runtime_start", {})
        identity = started["data"]["capture"]["id"]
        with monkeypatch.context() as patch:
            patch.setattr(RuntimeObservations, "release", held)
            task = asyncio.create_task(service.execute(ctx, "runtime_release", {"id": identity}))
            await await_signal(entered, "runtime release entered")
            task.cancel()
            for _ in range(3):
                await asyncio.sleep(0)
            proceed.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert stopped.is_set()
        entry, _ = await service.resolve(ctx, "runtime_status")
        reopened = GraphWorkspace(
            GraphStore(entry.workspace.store.path, entry.workspace.store.scope)
        )
        restored = RuntimeObservations(reopened)
        assert bool(restored.sessions()["captures"]) == (phase == "before")
        if phase == "after":
            assert not cleanup(reopened, before=0, limit=1)["more"]
        assert (await service.execute(ctx, "runtime_release", {"id": identity}))["data"]["released"]
        assert (await service.execute(ctx, "runtime_release", {"id": identity}))["data"]["released"]
    finally:
        proceed.set()
        await service.aclose()


def test_policy_refresh_does_not_resurrect_retired_owners_and_preserves_shared_exclusions(
    workspace,
):
    store, runtime = workspace.store, RuntimeObservations(workspace)
    identity, other = runtime.begin()["id"], runtime.begin()["id"]
    seed(runtime, identity, 2)
    seed(runtime, other, 1)
    exclusive, shared = f"runtime:{identity}:1", f"runtime:{identity}:2"
    store.put_declaration("shared", "authored", Edge(shared, "manual"), expected_version=0)
    workspace.set_policy({"exclude": ["Assets/Scripts/**"]}, expected_version=0)
    assert runtime.release(identity)
    workspace.set_policy({"exclude": ["Assets/Scripts/**", "Assets/Other/**"]}, expected_version=1)
    with store._connect(write=True) as db:
        workspace._refresh_runtime_admission(db, workspace._admission(db))
        excluded = {row[0] for row in db.execute("SELECT id FROM graph_excluded_nodes")}
        assert exclusive not in excluded
        assert shared in excluded and f"runtime:{other}:1" in excluded
        assert not db.execute("SELECT 1 FROM nodes WHERE id=?", (exclusive,)).fetchone()
    for _ in range(30):
        result = cleanup(workspace, before=0, limit=1)
        assert result["query_scratch_rows_removed"] <= 1
        if not result["more"]:
            break
    else:
        pytest.fail("Small runtime cleanup pages did not progress")
    with store._connect() as db:
        assert db.execute("SELECT 1 FROM graph_excluded_nodes WHERE id=?", (shared,)).fetchone()
    workspace.set_policy({"exclude": []}, expected_version=2)
    with store._connect(write=True) as db:
        workspace._refresh_runtime_admission(db, workspace._admission(db))
        assert not db.execute("SELECT 1 FROM nodes WHERE id=?", (exclusive,)).fetchone()
    assert store.declarations()[0]["id"] == "shared"
    assert runtime.status(other)["events"] == 1


def test_retired_node_metadata_and_snapshot_sources_are_immediately_hidden(workspace):
    store, runtime = workspace.store, RuntimeObservations(workspace)
    identity = runtime.begin()["id"]
    seed(runtime, identity, 1)
    owner = f"runtime:{identity}:1"
    store.put_declaration("shared", "authored", Edge(owner, "manual"), expected_version=0)
    queries = workspace.queries()
    saved = queries.snapshot()["snapshot"]["id"]
    assert queries.explain("declared:shared")["edges"][0]["target_path"]
    runtime.release(identity)
    explained = queries.explain("declared:shared")["edges"][0]
    assert explained["target_path"] == "" and explained["target_exists"] is None
    assert store.query("authored")["edges"][0]["target_exists"] is None
    with pytest.raises(RevisionConflict, match="source grant changed"):
        queries.diff(saved, saved)
    snapshot = queries.snapshot()
    assert "runtime:" + identity not in snapshot["sources"]
    assert all(edge["provider"] == "manual" for edge in snapshot["edges"])
    with store._connect() as db:
        assert db.execute("SELECT 1 FROM nodes WHERE id=?", (owner,)).fetchone()


def test_runtime_retirement_and_reclamation_rollback_preserve_retry(workspace, monkeypatch):
    store, runtime = workspace.store, RuntimeObservations(workspace)
    identity = runtime.begin()["id"]
    seed(runtime, identity, 3)
    connect = store._connect

    @contextmanager
    def interrupted(*args, **kwargs):
        with connect(*args, **kwargs) as db:
            yield db
            if kwargs.get("write"):
                raise InterruptedError("cancelled transaction")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", interrupted)
        with pytest.raises(InterruptedError):
            runtime.release(identity)
    assert runtime.status(identity)["events"] == 3
    assert runtime.release(identity)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", interrupted)
        with pytest.raises(InterruptedError):
            cleanup(workspace, before=0, limit=2)
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_runtime_events").fetchone()[0] == 3
        assert db.execute("SELECT 1 FROM graph_reclamation WHERE kind='runtime'").fetchone()
    assert cleanup(workspace, before=0, limit=2)["query_scratch_rows_removed"] == 2


def test_runtime_reclamation_indexes_migrate_without_losing_legacy_events(workspace, monkeypatch):
    from services.reference_graph import runtime as runtime_module

    store, runtime = workspace.store, RuntimeObservations(workspace)
    identity = runtime.begin()["id"]
    seed(runtime, identity, 2)
    with store._connect(write=True) as db:
        db.execute("UPDATE graph_runtime_schema SET version=2")
        for index in ("edges_provider", "observations_provider", "evidence_context_provider"):
            db.execute(f"DROP INDEX {index}")
    initialize = runtime_module.initialize_retirement

    def interrupted(connection):
        initialize(connection)
        raise InterruptedError("cancelled runtime migration")

    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, "initialize_retirement", interrupted)
        with pytest.raises(InterruptedError):
            GraphWorkspace(GraphStore(store.path, store.scope))
    with store._connect() as db:
        assert db.execute("SELECT version FROM graph_runtime_schema").fetchone()[0] == 2
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='edges_provider'").fetchone()
    reopened = GraphWorkspace(GraphStore(store.path, store.scope))
    runtime = RuntimeObservations(reopened)
    assert len(runtime.events(identity)["events"]) == 2
    with store._connect() as db:
        assert db.execute("SELECT version FROM graph_runtime_schema").fetchone()[0] == 3
    assert runtime.release(identity)
    for _ in range(20):
        if not cleanup(reopened, before=0, limit=1)["more"]:
            break
    else:
        pytest.fail("Migrated runtime history did not drain")
