"""A replacement Editor gets independently verified facts in a new owned DB."""

import hashlib
import sqlite3

import pytest

from services.reference_graph.collection_protocol import (
    CollectionBudget,
    CollectionBudgetExceeded,
    CollectionLimits,
)
from services.reference_graph.reconcile import Reconciler
from services.reference_graph.restart import open_workspace, reuse_assets
from services.reference_graph.store import Edge, GraphScope, GraphStore, RevisionConflict
from services.reference_graph.workspace import GraphWorkspace
from tests.test_reference_graph_reconcile import Editor, allowed


class VersionedEditor(Editor):
    instance = "first"
    binding = "binding"

    def __init__(self):
        super().__init__()
        self.targets = {}

    async def __call__(self, action, **params):
        if action == "probe":
            self.calls.append((action, params))
            return {
                "epoch": self.epoch,
                "revision": len(self.events),
                "targets": [
                    {
                        "guid": guid,
                        "path": row["path"],
                        "stamp": "stamp",
                        "present": True,
                        "folder": False,
                        "dependency": hashlib.sha256(self.sources.get(guid, b"")).hexdigest(),
                        "dependency_count": len(self.targets.get(guid, [])),
                        "binding": self.binding,
                    }
                    for path in params["paths"]
                    for guid, row in self.assets.items()
                    if row["path"] == path
                ],
            }
        row = await super().__call__(action, **params)
        if action == "status":
            row.update(
                instance_id=self.instance,
                compilation_context={"state": "available", "revision": "build"},
            )
        elif action == "dependencies":
            row["file_stamp"] = "stamp"
            row["targets"] = self.targets.get(params["guid"], [])
        elif action == "source":
            row["compilation_context"] = {
                "state": "available",
                "revision": "build",
                "binding_revision": self.binding,
                "defines": [],
                "assembly": "scripts",
                "references": [],
            }
        return row


@pytest.mark.asyncio
async def test_restart_verifies_legacy_inputs_and_preserves_exclusions(tmp_path):
    # Manufacture the 2.3.1 layout via the actual reconciler, without a
    # restart marker or any project-to-instance registry.
    old = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("alice", "first", "project")))
    old.set_policy(
        {"exclude": ["Assets/Private/**"], "respect_gitignore": False}, expected_version=0
    )
    editor = VersionedEditor()
    stable = editor.add(1, "Assets/Stable.cs", "class Stable {}")
    changed = editor.add(2, "Assets/Changed.cs", "class Old {}")
    removed = editor.add(3, "Assets/Removed.cs", "class Gone {}")
    editor.add(4, "Assets/Private/Hidden.cs", "class Hidden {}")
    editor.add(5, "Assets/Ignored.cs", "class Previous {}")
    assert await Reconciler(old, editor, allowed).reconcile()
    old.store.put_declaration("only-old", stable, Edge(changed, "manual"), expected_version=0)

    editor.instance, editor.epoch, editor.snapshot = "next", "new-epoch", None
    editor.calls.clear()
    editor.events.clear()
    editor.sources[changed[6:]] = b"class New {}"
    del editor.assets[removed[6:]]
    editor.ignore_files[".gitignore"] = b"Assets/Ignored.cs\n"
    added = editor.add(6, "Assets/Fresh.cs", "class Fresh {}")
    new = open_workspace(tmp_path, GraphScope("alice", "next", "project"))
    assert new.policy()["exclude"] == ["Assets/Private/**"] and new.policy()["respect_gitignore"]
    assert not new.ignore_current(await editor("status"))
    assert new.store.query(stable)["edges"] == [] and not new.documents()
    assert await Reconciler(new, editor, allowed).reconcile()

    collected = {params["guid"] for action, params in editor.calls if action == "dependencies"}
    assert collected == {changed[6:], added[6:]}
    assert set(new.document_manifest()) == {stable, changed, added}
    assert new.documents([stable]) == old.documents([stable])
    assert (
        new.documents([changed])[0]["data"]["types"] != old.documents([changed])[0]["data"]["types"]
    )
    assert new.store.declarations() == [] and old.store.declarations()
    probed = [
        path for action, params in editor.calls if action == "probe" for path in params["paths"]
    ]
    assert "Assets/Private/Hidden.cs" not in probed and "Assets/Ignored.cs" not in probed
    assert (
        open_workspace(tmp_path, GraphScope("other", "third", "project")).state("restart_from")
        is None
    )
    assert (
        open_workspace(tmp_path, GraphScope("alice", "fourth", "different")).state("restart_from")
        is None
    )
    with pytest.raises(RevisionConflict):
        reuse_assets(
            new, "retired", "first", [{"guid": stable[6:]}], CollectionBudget(CollectionLimits())
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "partial",
        "missing_proof",
        "filtered",
        "binding",
        "old_schema",
        "archived",
        "busy",
        "old_parser",
    ],
)
async def test_partial_cache_requires_positive_owner_and_policy_proofs(tmp_path, mode):
    old = open_workspace(tmp_path, GraphScope("alice", "first", "project"))
    editor = VersionedEditor()
    stable = editor.add(1, "Assets/Stable.cs", "class Stable {}")
    target = editor.add(2, "Assets/Target.txt")
    editor.targets[stable[6:]] = [target[6:]]
    assert await Reconciler(old, editor, allowed).reconcile()
    with old.store._connect(write=True) as db:
        db.execute("UPDATE graph_admission SET ready=0")
        old._set(db, "progress", {**old._state(db, "progress"), "phase": "collecting"})
        db.execute("UPDATE graph_assets SET dirty=1 WHERE guid=?", (target[6:],))
        if mode == "missing_proof":
            db.execute("DELETE FROM graph_state WHERE key='ignore_status'")
        elif mode == "filtered":
            db.execute("DELETE FROM edges WHERE owner=? AND provider='unity'", (stable,))
        elif mode == "old_parser":
            old._set(db, "analysis_version", 0)
    new = open_workspace(tmp_path, GraphScope("alice", "second", "project"))
    # Losing access to an optional donor *after* inheriting its policy
    # cannot stop the receiver from indexing its currently admitted files.
    locked = None
    if mode == "archived":
        old.store.path.rename(old.store.path.with_suffix(".archived"))
    elif mode == "old_schema":
        with old.store._connect(write=True) as db:
            db.execute("DROP TABLE graph_collected_versions")
    elif mode == "busy":
        locked = sqlite3.connect(old.store.path)
        locked.execute("PRAGMA locking_mode=EXCLUSIVE")
        locked.execute("BEGIN EXCLUSIVE")
    elif mode == "binding":
        editor.binding = "new-binding"
    editor.instance, editor.epoch, editor.snapshot = "second", "two", None
    editor.calls.clear()
    try:
        assert await Reconciler(new, editor, allowed).reconcile()
    finally:
        if locked:
            locked.close()
    fetched = {params["guid"] for action, params in editor.calls if action == "dependencies"}
    assert fetched == (
        {target[6:]} if mode in {"partial", "binding", "old_parser"} else {stable[6:], target[6:]}
    )
    assert {e["target"] for e in new.store.query(stable)["edges"]} == {target}


def test_policy_frontier_preserves_unincorporated_edits_and_later_explicit_repair(tmp_path):
    def scope(instance):
        return open_workspace(tmp_path, GraphScope("alice", instance, "project"))

    a, b = scope("first"), scope("second")
    a.set_policy({"exclude": ["Assets/A/**"]}, expected_version=0)
    b.set_policy({"exclude": ["Assets/B/**"]}, expected_version=0)
    c = scope("third")
    assert c.policy()["exclude"] == ["Assets/A/**", "Assets/B/**"]
    c.set_policy({"exclude": ["Assets/B/**"]}, expected_version=1)
    assert scope("fourth").policy()["exclude"] == ["Assets/B/**"]


def test_unknown_legacy_restrictions_block_then_explicit_policy_recovers(tmp_path):
    first = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("alice", "lost", "unidentified")))
    first.set_policy({"exclude": ["Assets/Secret/**"]}, expected_version=0)
    new = open_workspace(tmp_path, GraphScope("alice", "second", "another"))
    with pytest.raises(RevisionConflict, match="set_policy"):
        new.require_restart_policy()
    assert new.policy()["exclude"] == []
    new.set_policy({"exclude": []}, expected_version=0)
    new.require_restart_policy()
    third = open_workspace(tmp_path, GraphScope("alice", "third", "another"))
    third.require_restart_policy()
    assert third.policy()["exclude"] == []


def test_locked_policy_candidate_allows_explicit_recovery(tmp_path, monkeypatch):
    first = open_workspace(tmp_path, GraphScope("alice", "first", "project"))
    first.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    second = GraphScope("alice", "second", "project")
    connect = sqlite3.connect

    def busy(database, *args, **kwargs):
        if kwargs.get("uri") and "mode=ro" in str(database):
            raise sqlite3.OperationalError("database is locked")
        return connect(database, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", busy)
        pending = open_workspace(tmp_path, second)
        with pytest.raises(RevisionConflict, match="set_policy"):
            pending.require_restart_policy()
    pending.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    assert open_workspace(tmp_path, second).policy()["exclude"] == ["Assets/Private/**"]


@pytest.mark.parametrize("known_project", [False, True])
@pytest.mark.parametrize("failure", ["corrupt", "unreadable"])
def test_corrupt_foreign_project_isolated_without_losing_recovery(
    tmp_path, known_project, failure, monkeypatch
):
    old = open_workspace(tmp_path, GraphScope("alice", "old", "foreign"))
    if not known_project:
        # Recreate the existing v1 ledger without project identity metadata.
        with sqlite3.connect(tmp_path / ".storage.sqlite") as db:
            db.execute("ALTER TABLE scopes RENAME TO old_scopes")
            db.execute(
                "CREATE TABLE scopes(name TEXT PRIMARY KEY,owner TEXT NOT NULL,capacity INTEGER NOT NULL,reserved INTEGER NOT NULL)"
            )
            db.execute("INSERT INTO scopes SELECT name,owner,capacity,reserved FROM old_scopes")
            db.execute("DROP TABLE old_scopes")
            db.execute("CREATE INDEX scopes_owner ON scopes(owner)")
            db.execute("PRAGMA user_version=1")
    corrupt = b"corrupt owned fixture"
    if failure == "corrupt":
        old.store.path.write_bytes(corrupt)
    else:
        from services.reference_graph import restart

        readonly = restart._readonly

        def unreadable(path):
            if path == old.store.path:
                raise PermissionError("fixture access denied")
            return readonly(path)

        monkeypatch.setattr(restart, "_readonly", unreadable)
    current = open_workspace(tmp_path, GraphScope("alice", "new", "project"))
    if failure == "corrupt":
        assert old.store.path.read_bytes() == corrupt
    if not known_project:
        with pytest.raises(RevisionConflict, match="set_policy"):
            current.require_restart_policy()
        current.set_policy({"exclude": []}, expected_version=0)
        with current.store._connect() as db:
            assert db.execute("SELECT ready FROM graph_admission").fetchone()[0] == 0
    current.require_restart_policy()
    next_scope = open_workspace(tmp_path, GraphScope("alice", "next", "project"))
    next_scope.require_restart_policy()


@pytest.mark.asyncio
async def test_corrupt_prior_policy_keeps_status_and_policy_recovery_available(
    tmp_path, monkeypatch
):
    from services.reference_graph.service import local_user, ReferenceGraphService
    from tests.test_reference_graph_service import Context, Sender

    sender = Sender()
    native = await sender("one", {"action": "status"})
    old = open_workspace(tmp_path, GraphScope(local_user(), "old", native["project_id"]))
    old.store.path.write_bytes(b"damaged prior fixture")
    service = ReferenceGraphService(tmp_path, send=sender)
    monkeypatch.setattr(service, "start", lambda *_args, **_kwargs: None)
    ctx = Context(service)
    try:
        for action in ("status", "policy"):
            result = await service.execute(ctx, action, {})
            assert result["data"]["graph_status"]["policy_choice_required"]
        with pytest.raises(RevisionConflict, match="set_policy"):
            await service.execute(ctx, "query", {"node": "asset:example"})
        await service.execute(ctx, "set_policy", {"expected_version": 0, "config": {"exclude": []}})
        result = await service.execute(ctx, "policy", {})
        assert result["data"]["version"] == 1
        assert not result["data"]["graph_status"]["current"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_copying_dependencies_charges_attempt_before_any_owner_is_published(tmp_path):
    first = open_workspace(tmp_path, GraphScope("alice", "first", "project"))
    editor = VersionedEditor()
    stable = editor.add(1, "Assets/Stable.txt")
    editor.targets[stable[6:]] = [editor.add(n, f"Assets/Target{n}.txt")[6:] for n in range(2, 8)]
    assert await Reconciler(first, editor, allowed).reconcile()
    new = open_workspace(tmp_path, GraphScope("alice", "second", "project"))
    editor.instance, editor.epoch, editor.snapshot = "second", "two", None
    engine = Reconciler(new, editor, allowed)
    assert new.acquire(engine.worker)
    try:
        status = await editor("status")
        engine.progress = {"editor": status}
        await engine._ignore_rules(status)
        await engine._census(status, force=True)
        budget = CollectionBudget(CollectionLimits(max_items=3))
        proofs = (await editor("probe", paths=["Assets/Stable.txt"]))["targets"]
        with pytest.raises(CollectionBudgetExceeded, match="max_items"):
            reuse_assets(new, engine.worker, "first", proofs, budget)
        assert not new.store.query(stable)["edges"]
        assert any(row["guid"] == stable[6:] for row in new.dirty_assets())
    finally:
        new.release(engine.worker)


@pytest.mark.asyncio
async def test_version7_legacy_graph_reuses_native_edges_and_reparses_code(tmp_path):
    from services.reference_graph.runtime import RuntimeObservations

    # Only fields and checkpoints genuinely persisted by 2.3.1 are present.
    old = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("alice", "first", "project")))
    editor = VersionedEditor()
    source = editor.add(1, "Assets/Older.cs", "class Older {}")
    prefab = editor.add(2, "Assets/Same.txt")
    target = editor.add(3, "Assets/Target.txt")
    editor.targets[source[6:]] = [target[6:]]
    editor.targets[prefab[6:]] = [target[6:]]
    assert await Reconciler(old, editor, allowed).reconcile()
    with old.store._connect(write=True) as db:
        old._set(db, "analysis_version", 7)
        db.execute("UPDATE graph_documents SET payload=json_set(payload,'$.version',7)")
    receipt = old.mutations().begin(
        [{"guid": target[6:], "path": "Assets/Target.txt", "present": True, "folder": False}],
        {"epoch": editor.epoch, "revision": len(editor.events)},
    )["id"]
    runtime = RuntimeObservations(old).begin()["id"]
    old.set_source(
        "external:old",
        {"root": str(tmp_path / "old-root"), "include": ["*.json"]},
        expected_version=0,
    )
    editor.instance, editor.epoch, editor.snapshot = "second", "two", None
    editor.calls.clear()
    new = open_workspace(tmp_path, GraphScope("alice", "second", "project"))
    assert await Reconciler(new, editor, allowed).reconcile()
    assert not any(action == "dependencies" for action, _ in editor.calls)
    assert {p["guid"] for a, p in editor.calls if a == "source"} == {source[6:]}
    assert {row["target"] for row in new.store.query(prefab)["edges"]} == {target}
    assert new.documents([source])[0]["data"]["version"] > 7
    assert old.documents([source])[0]["data"]["version"] == 7
    assert {s["provider"] for s in new.store.sources()} == {"unity", "manual", "code"}
    with pytest.raises(ValueError, match="Unknown"):
        new.mutations().read(receipt)
    with pytest.raises(ValueError, match="Unknown"):
        RuntimeObservations(new).status(runtime)
    with new.store._connect() as db:
        assert not db.execute("SELECT 1 FROM graph_snapshots").fetchone()


@pytest.mark.asyncio
async def test_oversized_combined_policy_is_inspectable_and_repairable(tmp_path, monkeypatch):
    from services.reference_graph import response_budget
    from services.reference_graph.service import local_user, ReferenceGraphService
    from tests.test_reference_graph_service import Context, Sender

    user = local_user()
    sender = Sender()
    native = await sender("one", {"action": "status"})
    project = native["project_id"]
    first = open_workspace(tmp_path, GraphScope(user, "policy-a", project))
    second = open_workspace(tmp_path, GraphScope(user, "policy-b", project))
    monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 760)
    for prefix, workspace in [("a", first), ("b", second)]:
        workspace.set_policy({"exclude": [prefix * 120]}, expected_version=0)
    service = ReferenceGraphService(tmp_path, send=sender)
    context = Context(service)
    monkeypatch.setattr(service, "start", lambda *_args, **_kwargs: None)
    # Use normal delivery limits for the status envelope. Only the bootstrap
    # policy size is limited here so this synthetic threshold remains local.
    new = open_workspace(tmp_path, GraphScope(user, native["instance_id"], project))
    assert new.policy()["version"] == 0
    with pytest.raises(RevisionConflict, match="set_policy"):
        new.require_restart_policy()
    try:
        entry, _ = await service.resolve(context, "status")
        monkeypatch.undo()
        service.start = lambda *_args, **_kwargs: None
        for action in ("policy", "status"):
            response = await service.execute(context, action, {})
            assert response["data"]["graph_status"]["policy_choice_required"]
            assert not response["data"]["graph_status"]["current"]
        with pytest.raises(RevisionConflict, match="set_policy"):
            await service.execute(context, "query", {"node": "asset:example"})
        await service.execute(
            context, "set_policy", {"expected_version": 0, "config": {"exclude": []}}
        )
        entry.workspace.require_restart_policy()
        assert (await service.execute(context, "policy", {}))["data"]["version"] == 1
    finally:
        await service.aclose()


@pytest.mark.parametrize(
    ("prior_project", "current_project"),
    [("resolved", "default"), ("default", "resolved"), ("default", "default")],
)
@pytest.mark.parametrize("restricted", [False, True])
def test_native_default_project_id_never_identifies_a_static_donor(
    tmp_path, prior_project, current_project, restricted
):
    old = open_workspace(tmp_path, GraphScope("alice", "first", prior_project))
    if restricted:
        old.set_policy({"exclude": ["Assets/Confidential/**"]}, expected_version=0)
    new = open_workspace(tmp_path, GraphScope("alice", "second", current_project))
    assert new.state("restart_from") is None
    assert new.policy()["exclude"] == []
    if restricted:
        with pytest.raises(RevisionConflict, match="set_policy"):
            new.require_restart_policy()
        new.set_policy({"exclude": []}, expected_version=0)
    new.require_restart_policy()
    if current_project != "default":
        subsequent = open_workspace(tmp_path, GraphScope("alice", "third", current_project))
        subsequent.require_restart_policy()
        assert not subsequent.policy()["exclude"]


def test_unresolved_intermediate_session_does_not_block_a_proven_distinct_project(tmp_path):
    old = open_workspace(tmp_path, GraphScope("alice", "first", "project-a"))
    old.set_policy({"exclude": ["Assets/ProjectA/**"]}, expected_version=0)
    unknown = open_workspace(tmp_path, GraphScope("alice", "second", "default"))
    with pytest.raises(RevisionConflict, match="set_policy"):
        unknown.require_restart_policy()
    distinct = open_workspace(tmp_path, GraphScope("alice", "third", "project-b"))
    distinct.require_restart_policy()
    assert distinct.state("restart_from") is None
    assert distinct.policy()["exclude"] == []


def test_missing_policy_requires_explicit_repair_and_restored_policy_is_reconsidered(tmp_path):
    def scope(instance):
        return open_workspace(tmp_path, GraphScope("alice", instance, "project"))

    old = scope("first")
    old.set_policy({"exclude": ["Assets/Restricted/**"]}, expected_version=0)
    before_archive = scope("second")
    assert before_archive.policy()["exclude"] == ["Assets/Restricted/**"]
    archive = old.store.path.with_suffix(".archived")
    old.store.path.rename(archive)

    unresolved = scope("third")
    with pytest.raises(RevisionConflict, match="set_policy"):
        unresolved.require_restart_policy()
    # Reopening the same pending scope cannot grant a permissive policy.
    with pytest.raises(RevisionConflict, match="set_policy"):
        scope("third").require_restart_policy()
    unresolved.set_policy({"exclude": []}, expected_version=0)
    repaired = scope("fourth")
    repaired.require_restart_policy()
    assert repaired.policy()["exclude"] == []

    archive.rename(old.store.path)
    restored = scope("fifth")
    restored.require_restart_policy()
    assert restored.policy()["exclude"] == ["Assets/Restricted/**"]
    old.store.path.rename(archive)
    with pytest.raises(RevisionConflict, match="set_policy"):
        scope("sixth").require_restart_policy()
