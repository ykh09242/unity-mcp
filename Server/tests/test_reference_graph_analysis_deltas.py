"""Analysis metadata follows committed document identities and pending publications."""

import asyncio
from dataclasses import asdict
import json
import threading

import pytest

from services.reference_graph import document_delta
from services.reference_graph.analysis import parse_csharp
from services.reference_graph.reconcile import Reconciler
from services.reference_graph.store import GraphScope, GraphStore, Observation, RevisionConflict
from services.reference_graph.workspace import GraphWorkspace
from tests.test_reference_graph_reconcile import Editor, allowed
from tests.timing import watchdog_timeout


@pytest.fixture
def workspace(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("analysis-delta", "editor", "project"))
    return GraphWorkspace(store)


def summary(owner):
    return {
        "owner": owner,
        "path": "Assets/" + owner + ".prefab",
        "coverage": "partial_static",
        "unresolved": [],
        "edges": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [100, 1000])
@pytest.mark.parametrize("format_", ["summary", "csharp"])
async def test_one_warm_document_edit_reads_only_changed_metadata(
    workspace, monkeypatch, size, format_
):
    engine = Reconciler(workspace, Editor(), allowed)
    assert workspace.acquire(engine.worker)

    def payload(i):
        if format_ == "csharp":
            return asdict(parse_csharp(str(i), f"Assets/T{i}.cs", f"class T{i} {{ }}"))
        return summary(str(i))

    # Restore an already analyzed population in one transaction.
    with workspace.store._connect(write=True) as db:
        db.executemany(
            "INSERT INTO graph_documents VALUES (?,?,'same',?,NULL)",
            ((str(i), format_, json.dumps(payload(i))) for i in range(size)),
        )
        db.executemany(
            "INSERT INTO observations VALUES (?,'code','same',1,'ready',NULL)",
            ((str(i),) for i in range(size)),
        )
        workspace._set(db, "analyzed_rules_revision", 0)
        workspace._set(db, "analysis_dirty", True)
        db.execute("DELETE FROM graph_changed_inputs")
    await engine._analysis()
    visited, hydrated = [], []
    deltas, pending = [], []
    manifest, documents = workspace.document_manifest, workspace.documents
    changed_owners, pending_page = document_delta.changed_owners, document_delta.pending

    def count_manifest():
        result = manifest()
        visited.extend(result)
        return result

    def count_documents(owners=None):
        result = documents(owners)
        hydrated.extend(row["owner"] for row in result)
        return result

    def count_deltas(*args):
        result = changed_owners(*args)
        deltas.extend(result)
        return result

    def count_pending(*args):
        result = pending_page(*args)
        pending.extend(result)
        return result

    monkeypatch.setattr(workspace, "document_manifest", count_manifest)
    monkeypatch.setattr(workspace, "documents", count_documents)
    monkeypatch.setattr(document_delta, "changed_owners", count_deltas)
    monkeypatch.setattr(document_delta, "pending", count_pending)
    if format_ == "csharp":
        assert save_csharp(workspace, engine.worker, "1", "class Renamed {}")
    else:
        assert workspace.save_document(
            engine.worker, "1", "summary", "same", {**summary("1"), "kind": "updated"}
        )
    await engine._analysis()
    print(
        f"warm analysis {format_}, size={size}: manifest={len(visited)}, "
        f"deltas={len(deltas)}, pending={len(pending)}, hydrated={len(hydrated)}"
    )
    assert visited == []
    assert deltas == pending == ["1"]
    assert hydrated == (["1", "1"] if format_ == "csharp" else ["1"])


def test_document_events_and_observation_queue_commit_together(workspace):
    with workspace.store._connect(write=True) as db:
        start = document_delta.generation(db)
        db.execute("INSERT INTO graph_documents VALUES ('a','csharp','same','{}',NULL)")
        db.execute("INSERT INTO observations VALUES ('a','unity','same',1,'ready',NULL)")
        assert document_delta.pending(db, "", 100) == ["a"]
        db.execute("INSERT INTO observations VALUES ('a','code','same',1,'ready',NULL)")
        assert not document_delta.pending(db, "", 100)
        db.execute("UPDATE graph_documents SET payload='{\"updated\":true}' WHERE owner='a'")
        assert document_delta.pending(db, "", 100) == ["a"]
        db.execute("UPDATE observations SET state='ready' WHERE owner='a' AND provider='code'")
        assert not document_delta.pending(db, "", 100)
        db.execute("DELETE FROM observations WHERE owner='a' AND provider='code'")
        assert document_delta.pending(db, "", 100) == ["a"]
        db.execute("DELETE FROM graph_documents WHERE owner='a'")
        assert not document_delta.pending(db, "", 100)
        end = document_delta.generation(db)
        assert end == start + 3
        assert document_delta.changed_owners(db, start, end) == {"a"}
        db.execute("SAVEPOINT interrupted")
        db.execute("INSERT INTO graph_documents VALUES ('b','csharp','same','{}',NULL)")
        db.execute("ROLLBACK TO interrupted")
        assert document_delta.generation(db) == end
        assert not document_delta.pending(db, "", 100)


def test_bounded_history_and_indexed_pending_work(workspace, monkeypatch):
    # Install a small history in an isolated real schema to exhaust it cheaply.
    monkeypatch.setattr(document_delta, "MAX_EVENTS", 4)
    with workspace.store._connect(write=True) as db:
        for action in ("INSERT", "UPDATE", "DELETE"):
            db.execute(f"DROP TRIGGER IF EXISTS graph_document_{action}")
        document_delta.initialize(db)
        counts = []
        for first, stop in ((0, 100), (100, 1000)):
            db.executemany(
                "INSERT INTO graph_documents VALUES (?,'summary','s','{}',NULL)",
                ((str(i),) for i in range(first, stop)),
            )
            db.executemany(
                "INSERT INTO observations VALUES (?,'code','s',1,'ready',NULL)",
                ((str(i),) for i in range(first, stop)),
            )
            before = document_delta.generation(db)
            db.execute("UPDATE graph_documents SET fingerprint='changed' WHERE owner='3'")
            db.execute("UPDATE graph_documents SET format='csharp' WHERE owner='3'")
            after = document_delta.generation(db)
            assert document_delta.changed_owners(db, 0, after) is None
            assert db.execute("SELECT count(*) FROM graph_document_changes").fetchone()[0] == 4
            ticks = []
            db.set_progress_handler(lambda ticks=ticks: ticks.append(1) or 0, 1)
            try:
                assert document_delta.changed_owners(db, before, after) == {"3"}
                assert document_delta.changed_csharp(db, ["3", "absent"]) == {"3"}
                assert document_delta.pending(db, "", 100) == ["3"]
            finally:
                db.set_progress_handler(None, 0)
            counts.append(len(ticks))
            db.execute("DELETE FROM graph_document_changes WHERE generation=?", (after,))
            assert document_delta.changed_owners(db, before, after) is None
            db.execute("UPDATE observations SET state='ready' WHERE owner='3' AND provider='code'")
        print(f"document delta and pending VM steps at 100 / 1000 documents: {counts}")
        assert counts[1] <= counts[0] * 2 + 50, counts


def test_policy_reset_breaks_document_history_and_retires_pending(workspace):
    assert workspace.acquire("worker")
    workspace.save_document("worker", "old", "summary", "same", summary("old"))
    with workspace.store._connect() as db:
        known = document_delta.generation(db)
    workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    with workspace.store._connect() as db:
        current = document_delta.generation(db)
        assert current > known
        assert document_delta.changed_owners(db, known, current) is None
        assert not document_delta.pending(db, "", 100)


def save_csharp(workspace, worker, owner, source):
    facts = parse_csharp(owner, "Assets/" + owner + ".cs", source)
    return workspace.save_document(worker, owner, "csharp", "same", asdict(facts))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fallback", ["warm", "journal_gap", "eviction", "reopen", "queue_interrupted"]
)
async def test_rebinding_after_same_identity_document_edits_and_removal(
    workspace, monkeypatch, fallback
):
    engine = Reconciler(workspace, Editor(), allowed)
    worker = engine.worker
    assert workspace.acquire(worker)
    target = {
        "guid": "a" * 32,
        "path": "Assets/Resources/Icon.prefab",
        "keys": [{"scheme": "resources", "value": "Icon"}],
    }
    workspace.record_page(worker, [target], {}, "scan")
    caller = 'using static Lib; partial class C { void M() { Load("Icon"); } }'
    save_csharp(workspace, worker, "caller", caller)
    save_csharp(
        workspace,
        worker,
        "lib",
        "class Lib { public static void Load(string key) { UnityEngine.Resources.Load(key); } }",
    )
    save_csharp(workspace, worker, "part", "partial class C { }")
    await engine._analysis()

    def targets():
        return [edge["target"] for edge in workspace.store.query("caller")["edges"]]

    assert targets() == ["asset:" + target["guid"]]
    save_csharp(workspace, worker, "part", "partial class C { void Load(object key) { } }")
    if fallback == "journal_gap":
        with workspace.store._connect(write=True) as db:
            db.execute("DELETE FROM graph_document_changes")
    elif fallback == "eviction":
        engine.clear_caches()
    elif fallback == "reopen":
        workspace.release(worker)
        workspace = GraphWorkspace(GraphStore(workspace.store.path, workspace.store.scope))
        engine = Reconciler(workspace, Editor(), allowed)
        worker = engine.worker
        assert workspace.acquire(worker)
    elif fallback == "queue_interrupted":

        def interrupt(*args, **kwargs):
            raise RuntimeError("interrupted before enqueue")

        with monkeypatch.context() as patch:
            patch.setattr(workspace, "queue_analysis", interrupt)
            with pytest.raises(RuntimeError, match="interrupted before enqueue"):
                await engine._analysis()
        assert engine._analysis_cache is None
        assert "part" in workspace.changed_inputs()
    await engine._analysis()
    assert targets() == []
    workspace.save_document(worker, "part", "summary", "same", summary("part"))
    await engine._analysis()
    assert targets() == ["asset:" + target["guid"]]
    save_csharp(workspace, worker, "part", "partial class C { void Load(object key) { } }")
    await engine._analysis()
    assert targets() == []
    workspace.drop_document(worker, "part")
    await engine._analysis()
    assert targets() == ["asset:" + target["guid"]]


def test_upgrade_seeds_only_unfinished_documents_and_tracks_rules_and_streaming(workspace):
    with workspace.store._connect(write=True) as db:
        for action in ("INSERT", "UPDATE", "DELETE"):
            db.execute(f"DROP TRIGGER IF EXISTS graph_document_{action}")
            db.execute(f"DROP TRIGGER IF EXISTS graph_code_observation_{action}")
        for table in ("graph_document_clock", "graph_document_changes", "graph_pending_analysis"):
            db.execute(f"DROP TABLE IF EXISTS {table}")
        db.execute("DROP INDEX IF EXISTS graph_documents_format_owner")
        db.execute("UPDATE graph_workspace SET version=10")
        for owner in ("ready", "missing", "pending"):
            db.execute("INSERT INTO graph_documents VALUES (?,'summary','x','{}',NULL)", (owner,))
        db.executemany(
            "INSERT INTO observations VALUES (?,'code','x',1,?,NULL)",
            [("ready", "ready"), ("pending", "pending"), ("orphan", "pending")],
        )
        workspace._set(db, "analysis_dirty", False)

    workspace = GraphWorkspace(workspace.store)
    assert workspace.state("analysis_dirty")
    assert workspace.acquire("worker")
    with workspace.store._connect() as db:
        assert document_delta.pending(db, "", 100) == ["missing", "pending"]
        assert document_delta.generation(db) == 0
    workspace.store.put_rule("disabled", {"enabled": False}, expected_version=0)
    generation = workspace.observe_many(
        "worker", [Observation("ready", "code", "x", force=True, rules_revision=1)]
    )[0]
    with workspace.store._connect() as db:
        assert document_delta.pending(db, "", 100) == ["missing", "pending", "ready"]
    workspace.publish_events("worker", "ready", generation, [])
    with workspace.store._connect() as db:
        assert document_delta.pending(db, "", 100) == ["missing", "pending"]


def test_streamed_promotion_rejects_interleaved_document_generation(workspace):
    assert workspace.acquire("worker")
    workspace.save_document("worker", "first", "summary", "x", summary("first"))
    with workspace.store._connect() as db:
        current = document_delta.generation(db)
    generation = workspace.observe_many("worker", [Observation("first", "code", "x")])[0]
    with pytest.raises(RevisionConflict):
        workspace.publish_events(
            "worker",
            "first",
            generation,
            [{"reason": "synthetic"}],
            before_publish=lambda: save_csharp(workspace, "worker", "other", "class Other {}"),
            document_generation=current,
        )
    with workspace.store._connect() as db:
        assert document_delta.pending(db, "", 100) == ["first", "other"]
        assert not db.execute("SELECT 1 FROM graph_staging LIMIT 1").fetchone()
        assert not db.execute("SELECT 1 FROM graph_issues LIMIT 1").fetchone()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["observe_many", "publish_many", "finish_analysis"])
async def test_concurrent_document_change_fences_analysis(workspace, monkeypatch, stage):
    engine = Reconciler(workspace, Editor(), allowed)
    assert workspace.acquire(engine.worker)
    workspace.save_document(engine.worker, "first", "summary", "same", summary("first"))
    original = getattr(workspace, stage)

    def concurrent(*args, **kwargs):
        save_csharp(workspace, engine.worker, "new", "class New {}")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(workspace, stage, concurrent)
        with pytest.raises(RevisionConflict):
            await engine._analysis()
    assert workspace.state("analysis_dirty")
    assert "new" in workspace.changed_inputs()
    if stage != "finish_analysis":
        assert workspace.document_manifest()["first"]["state"] != "ready"
    await engine._analysis()
    assert workspace.document_manifest()["new"]["state"] == "ready"
    assert not workspace.changed_inputs()


@pytest.mark.asyncio
async def test_invalidation_behind_completed_cursor_remains_durable(workspace, monkeypatch):
    engine = Reconciler(workspace, Editor(), allowed)
    assert workspace.acquire(engine.worker)
    workspace.save_document(engine.worker, "first", "summary", "same", summary("first"))
    original = workspace.finish_analysis

    def concurrent(*args, **kwargs):
        workspace.store.rebuild()
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(workspace, "finish_analysis", concurrent)
        with pytest.raises(RevisionConflict):
            await engine._analysis()
    assert workspace.state("analysis_dirty")
    assert workspace.document_manifest()["first"]["state"] == "pending"
    await engine._analysis()
    assert workspace.document_manifest()["first"]["state"] == "ready"


@pytest.mark.asyncio
async def test_cancel_during_enqueue_drains_writer_and_rebuilds_on_retry(workspace, monkeypatch):
    engine = Reconciler(workspace, Editor(), allowed)
    assert workspace.acquire(engine.worker)
    save_csharp(workspace, engine.worker, "owner", "class Before {}")
    await engine._analysis()
    save_csharp(workspace, engine.worker, "owner", "class After {}")
    enqueue = workspace.queue_analysis
    entered, release = threading.Event(), threading.Event()

    def held_enqueue(*args, **kwargs):
        entered.set()
        assert release.wait(watchdog_timeout())
        enqueue(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(workspace, "queue_analysis", held_enqueue)
        task = asyncio.create_task(engine._analysis())
        try:
            assert await asyncio.to_thread(entered.wait, watchdog_timeout())
            task.cancel()
            assert await asyncio.to_thread(engine.stopped.wait, watchdog_timeout())
            assert not task.done()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert engine._analysis_cache is None
    assert workspace.changed_inputs() == {"owner"}
    engine.stopped.clear()
    await engine._analysis()
    assert engine._analysis_cache.index.facts["owner"].types == ("After",)
    assert workspace.document_manifest()["owner"]["state"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("evict", [False, True])
async def test_blocked_declaration_and_unchanged_recovery_rebind_eligible_callers(workspace, evict):
    engine = Reconciler(workspace, Editor(), allowed)
    worker = engine.worker
    assert workspace.acquire(worker)
    guid = "a" * 32
    part = "asset:" + guid
    target = {
        "guid": "b" * 32,
        "path": "Assets/Icon.prefab",
        "keys": [{"scheme": "resources", "value": "Icon"}],
    }
    workspace.record_page(worker, [target], {}, "scan")
    save_csharp(
        workspace,
        worker,
        "caller",
        'using static Lib; partial class C { void M() { Load("Icon"); } }',
    )
    save_csharp(
        workspace,
        worker,
        "lib",
        "class Lib { public static void Load(string key) { UnityEngine.Resources.Load(key); } }",
    )
    save_csharp(workspace, worker, part, "partial class C { void Load(object key) { } }")
    await engine._analysis()
    assert not workspace.store.query("caller")["edges"]
    workspace.fail_asset(worker, {"guid": guid}, ValueError("synthetic failure"))
    if evict:
        engine.clear_caches()
    await engine._analysis()
    assert workspace.blocked_analysis() == {part}
    assert [e["target"] for e in workspace.store.query("caller")["edges"]] == [
        "asset:" + target["guid"]
    ]
    with workspace.store._connect() as db:
        assert document_delta.pending(db, "", 100) == [part]
    assert not workspace.state("analysis_dirty")
    workspace.clean_asset(worker, guid)
    await engine._analysis()
    assert not workspace.store.query("caller")["edges"]
    assert workspace.document_manifest()[part]["state"] == "ready"
