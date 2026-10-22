"""Exercise durable reconciliation against the native paging protocol."""

import asyncio
import base64
import hashlib
import json
import threading

import pytest

from services.reference_graph.reconcile import Reconciler, drained_thread
from services.reference_graph.store import GraphScope, GraphStore, RevisionConflict
from services.reference_graph.workspace import GraphWorkspace


async def allowed():
    pass


class Editor:
    def __init__(self):
        self.assets = {}
        self.sources = {}
        self.events = []
        self.epoch = "epoch"
        self.calls = []
        self.compiling = False
        self.snapshot = None
        self.fail_offset = None

    def add(self, number, path, text=None, keys=None):
        guid = f"{number:032x}"
        self.assets[guid] = {
            "guid": guid,
            "present": True,
            "path": path,
            "kind": "script" if path.endswith(".cs") else "asset",
            "source_format": "csharp" if path.endswith(".cs") else "none",
            "keys": keys or [],
        }
        if text is not None:
            self.sources[guid] = text.encode()
        self.event(path)
        return "asset:" + guid

    def event(self, path, deleted=False):
        self.events.append({"revision": len(self.events) + 1, "path": path, "deleted": deleted})

    async def __call__(self, action, **params):
        self.calls.append((action, params))
        if action == "status":
            return {
                "instance_id": "editor",
                "project_id": "project",
                "epoch": self.epoch,
                "revision": len(self.events),
                "compiling": self.compiling,
            }
        if action == "inventory":
            if self.snapshot is None:
                self.snapshot = (list(self.assets), len(self.events))
            offset = params["offset"]
            if offset == self.fail_offset:
                raise OSError("interrupted page")
            ids, revision = self.snapshot
            # Small pages exercise continuation without a production workload cap.
            end = min(offset + 2, len(ids))
            return {
                "snapshot": "snapshot",
                "epoch": self.epoch,
                "revision": revision,
                "assets": [self.assets[guid] for guid in ids[offset:end] if guid in self.assets],
                "next_offset": end if end < len(ids) else None,
            }
        if action == "changes":
            rows = self.events[params["after"] : params["after"] + 2]
            after = rows[-1]["revision"] if rows else len(self.events)
            return {
                "requires_census": params["epoch"] != self.epoch,
                "changes": rows,
                "next_after": after,
                "has_more": after < len(self.events),
            }
        if action == "asset":
            return next(
                (row.copy() for row in self.assets.values() if row["path"] == params["path"]),
                {"present": False},
            )
        guid = params["guid"]
        if action == "dependencies":
            if guid not in self.assets:
                return {"present": False}
            return {
                **self.assets[guid],
                "snapshot": "dependencies",
                "fingerprint": hashlib.sha256(self.sources.get(guid, b"")).hexdigest(),
                "targets": [],
                "next_offset": None,
            }
        if action == "source":
            content = self.sources[guid]
            offset = params["offset"]
            end = min(offset + 37, len(content))
            return {
                "source_format": "csharp",
                "fingerprint": hashlib.sha256(content).hexdigest(),
                "bytes_base64": base64.b64encode(content[offset:end]).decode(),
                "next_offset": end if end < len(content) else None,
            }
        raise AssertionError(action)


@pytest.fixture
def setup(tmp_path):
    store = GraphStore.open(tmp_path / "state", GraphScope("user", "editor", "project"))
    workspace = GraphWorkspace(store)
    editor = Editor()
    return store, workspace, editor, Reconciler(workspace, editor, allowed)


@pytest.mark.asyncio
async def test_add_edit_wrapper_move_delete_and_idle_do_not_reread_unchanged_sources(setup):
    store, workspace, editor, engine = setup
    first = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    second = editor.add(
        2, "Assets/Resources/Two.prefab", keys=[{"scheme": "resources", "value": "Two"}]
    )
    wrapper = editor.add(
        3,
        "Assets/Loader.cs",
        'using UnityEngine; class Loader { public static Object Load() { return Resources.Load("One"); } }',
    )
    caller = editor.add(4, "Assets/Caller.cs", "class Caller { void M() { Loader.Load(); } }")
    assert await engine.reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {first}
    editor.calls.clear()
    assert await engine.reconcile()
    assert {call[0] for call in editor.calls} == {"status", "changes"}
    editor.sources[wrapper[6:]] = (
        b'using UnityEngine; class Loader { public static Object Load() { return Resources.Load("Two"); } }'
    )
    editor.event("Assets/Loader.cs")
    assert await engine.reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {second}
    editor.assets[wrapper[6:]]["path"] = "Assets/Moved.cs"
    editor.event("Assets/Loader.cs", True)
    editor.event("Assets/Moved.cs")
    assert await engine.reconcile()
    facts = next(row for row in workspace.documents() if row["owner"] == wrapper)
    assert facts["data"]["path"] == "Assets/Moved.cs"
    del editor.assets[wrapper[6:]]
    editor.event("Assets/Moved.cs", True)
    assert await engine.reconcile()
    assert not store.query(caller)["edges"]


@pytest.mark.asyncio
async def test_census_failure_keeps_previous_evidence_and_resumes_complete_scan(setup):
    store, workspace, editor, engine = setup
    for number in range(5):
        editor.add(number, f"Assets/{number}.asset")
    editor.fail_offset = 2
    with pytest.raises(OSError):
        await engine.reconcile()
    assert workspace.counts()["assets"] == 2
    assert not workspace.progress()["initialized"]
    editor.fail_offset = None
    assert await Reconciler(workspace, editor, allowed).reconcile()
    assert workspace.counts()["assets"] == 5
    assert store.status()["queue_idle"]


@pytest.mark.asyncio
async def test_compile_failure_and_journal_overflow_do_not_claim_freshness(setup):
    _, workspace, editor, engine = setup
    editor.add(1, "Assets/One.asset")
    editor.compiling = True
    assert not await engine.reconcile()
    assert workspace.progress()["phase"] == "waiting_for_editor"
    editor.compiling = False
    assert await engine.reconcile()
    editor.epoch = "new"
    assert await engine.reconcile()
    assert workspace.progress()["epoch"] == "new"


@pytest.mark.asyncio
async def test_addressables_catalog_replay_handles_old_snapshot_without_loop(setup):
    store, _, editor, engine = setup
    target = editor.add(1, "Assets/A.prefab", keys=[{"scheme": "addressables", "value": "old"}])
    caller = editor.add(
        2,
        "Assets/A.cs",
        'using UnityEngine.AddressableAssets; class C { void M() { Addressables.LoadAssetAsync<object>("new"); } }',
    )
    await engine.reconcile()
    assert not store.query(caller)["edges"]
    editor.assets[target[6:]]["keys"] = [{"scheme": "addressables", "value": "new"}]
    editor.events.append({"revision": len(editor.events) + 1, "kind": "addressables_catalog"})
    assert await asyncio.wait_for(engine.reconcile(), 10)
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {target}


@pytest.mark.asyncio
async def test_external_changes_deletion_and_mapping_revision_are_automatic(
    setup, tmp_path, monkeypatch
):
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/Fixture.prefab", keys=[{"scheme": "resources", "value": "Fixture"}]
    )
    root = tmp_path / "settings"
    root.mkdir()
    path = root / "a.json"
    path.write_text(json.dumps({"path": "Fixture"}))
    config = {
        "root": str(root),
        "include": ["*.json"],
        "mappings": [{"field": "/path", "scheme": "resources"}],
    }
    store.set_source("external:settings", config, expected_version=0)
    assert await engine.reconcile()
    assert len(store.query(target, direction="incoming")["edges"]) == 1
    from services.reference_graph.external import ExternalSource

    original = ExternalSource.read
    monkeypatch.setattr(
        ExternalSource, "read", lambda *_: pytest.fail("Idle file was loaded again")
    )
    await engine.reconcile()
    monkeypatch.setattr(ExternalSource, "read", original)
    path.write_text('{"path":"missing"}')
    await engine.reconcile()
    assert not store.query(target, direction="incoming")["edges"]
    path.write_text('{"other":"Fixture"}')
    config["mappings"] = [{"field": "/other", "scheme": "resources"}]
    store.set_source("external:settings", config, expected_version=1)
    await engine.reconcile()
    assert len(store.query(target, direction="incoming")["edges"]) == 1
    path.unlink()
    await engine.reconcile()
    assert not store.query(target, direction="incoming")["edges"]
    assert not workspace.external_inputs("external:settings")


@pytest.mark.asyncio
async def test_cancel_waits_for_owned_thread_before_returning():
    started, release = threading.Event(), threading.Event()

    def operation():
        started.set()
        release.wait(5)
        return "finished"

    task = asyncio.create_task(drained_thread(operation))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_replaced_worker_cannot_readmit_completed_analysis(setup):
    store, workspace, editor, engine = setup
    owner = editor.add(1, "Assets/A.cs", 'class C { void M() { Resources.Load("missing"); } }')
    assert await engine.reconcile()
    documents = workspace.documents()
    rules = store.rules_state()
    index = engine._build_index(documents, workspace.catalog(), rules)
    before = store.status()
    assert workspace.acquire("replacement")
    with pytest.raises(RevisionConflict):
        engine._analyze_batch(documents, index, rules["revision"])
    assert store.status() == before
    workspace.release("replacement")
    assert await engine.reconcile()
    assert workspace.diagnostics(owner)["coverage"][0]["state"] == "ready"
    assert store.status()["queue_idle"]


@pytest.mark.asyncio
async def test_cancellation_during_lease_acquisition_does_not_orphan_worker(setup, monkeypatch):
    _, workspace, _, engine = setup
    entered, release = threading.Event(), threading.Event()
    original = workspace.acquire

    def held_acquire(owner):
        result = original(owner)
        entered.set()
        release.wait(5)
        return result

    monkeypatch.setattr(workspace, "acquire", held_acquire)
    task = asyncio.create_task(engine.reconcile())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert original("next")
    workspace.release("next")


@pytest.mark.asyncio
async def test_interrupted_forced_external_analysis_resumes_without_file_change(
    setup, tmp_path, monkeypatch
):
    import services.reference_graph.reconcile as module

    store, workspace, _, engine = setup
    root = tmp_path / "settings"
    root.mkdir()
    (root / "a.json").write_text('{"path":"missing"}')
    workspace.set_source(
        "external:settings", {"root": str(root), "include": ["*.json"]}, expected_version=0
    )
    assert await engine.reconcile()
    original = module.analyze_document

    def interrupted(*_):
        raise ValueError("interrupted after admission")

    monkeypatch.setattr(module, "analyze_document", interrupted)
    assert not await engine.reconcile(force_external=True)
    assert not store.status()["queue_idle"]
    monkeypatch.setattr(module, "analyze_document", original)
    assert await engine.reconcile()
    assert store.status()["queue_idle"]


@pytest.mark.asyncio
async def test_addressables_observer_error_never_claims_current(setup):
    _, workspace, editor, _ = setup
    editor.add(1, "Assets/A.asset")

    async def collect(action, **params):
        data = await editor(action, **params)
        if action == "status":
            data["addressables_event_error"] = "observer unavailable"
        return data

    assert not await Reconciler(workspace, collect, allowed).reconcile()
    assert workspace.counts()["assets"] == 1
    assert workspace.progress()["phase"] == "changes_pending"


@pytest.mark.asyncio
async def test_unchanged_reimport_recovers_wrapper_and_dependent_evidence(setup):
    store, _, editor, engine = setup
    editor.add(1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}])
    wrapper = editor.add(
        2,
        "Assets/Loader.cs",
        'using UnityEngine; class Loader { public static Object Get() => Resources.Load("One"); }',
    )
    caller = editor.add(3, "Assets/Caller.cs", "class Caller { void Go() { Loader.Get(); } }")
    assert await engine.reconcile()
    expected = {owner: store.query(owner)["edges"] for owner in (wrapper, caller)}
    editor.event("Assets/Loader.cs")
    assert await engine.reconcile()
    assert store.status()["queue_idle"]
    assert {owner: store.query(owner)["edges"] for owner in (wrapper, caller)} == expected


@pytest.mark.asyncio
async def test_bad_source_does_not_starve_good_assets_and_repair_bypasses_backoff(setup):
    store, workspace, editor, engine = setup
    bad = editor.add(1, "Assets/Bad.cs", "class Bad {}")
    editor.sources[bad[6:]] = b"// unsupported source encoding \xff\nclass Bad {}"
    target = editor.add(
        2, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    good = editor.add(
        3,
        "Assets/Good.cs",
        'using UnityEngine; class Good { void Go() { Resources.Load("One"); } }',
    )
    assert not await engine.reconcile()
    assert store.query(good)["edges"][0]["target"] == target
    assert store.status()["errors"]
    assert workspace.diagnostics(bad)["issues"][0]["reason"] == "collection_failed"
    restored = GraphWorkspace(GraphStore(store.path, store.scope))
    assert restored.counts()["failed_assets"] == 1
    editor.calls.clear()
    assert not await engine.reconcile()
    assert not [params for action, params in editor.calls if action == "source"]
    editor.sources[bad[6:]] = b"class Bad {}"
    editor.event("Assets/Bad.cs")
    assert await engine.reconcile()
    assert workspace.counts()["failed_assets"] == 0
    assert store.status()["queue_idle"]


@pytest.mark.asyncio
async def test_failed_wrapper_keeps_previous_dependents_stale_until_repaired(setup):
    store, _, editor, engine = setup
    editor.add(1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}])
    wrapper = editor.add(
        2,
        "Assets/Loader.cs",
        'using UnityEngine; class Loader { public static Object Get() => Resources.Load("One"); }',
    )
    caller = editor.add(3, "Assets/Caller.cs", "class Caller { void Go() { Loader.Get(); } }")
    assert await engine.reconcile()
    previous = editor.sources[wrapper[6:]]
    editor.sources[wrapper[6:]] = b"\xffinvalid"
    editor.event("Assets/Loader.cs")
    assert not await engine.reconcile()
    assert store.query(caller)["edges"][0]["stale"]
    assert not await engine.reconcile()
    assert store.query(caller)["edges"][0]["stale"]
    editor.sources[wrapper[6:]] = previous
    editor.event("Assets/Loader.cs")
    assert await engine.reconcile()
    assert not store.query(caller)["edges"][0]["stale"]


@pytest.mark.asyncio
async def test_malformed_csv_isolated_from_good_file_and_recovers_on_repair(setup, tmp_path):
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    root = tmp_path / "settings"
    root.mkdir()
    bad = root / "a.csv"
    bad.write_text('prefab\n"unterminated', encoding="utf-8")
    (root / "b.csv").write_text("prefab\nOne", encoding="utf-8")
    workspace.set_source(
        "external:settings",
        {
            "root": str(root),
            "include": ["*.csv"],
            "mappings": [{"field": "prefab", "scheme": "resources"}],
        },
        expected_version=0,
    )
    assert not await engine.reconcile()
    assert len(store.query(target, direction="incoming")["edges"]) == 1
    assert store.status()["errors"] == 1
    inputs = workspace.external_inputs("external:settings")
    assert set(inputs) == {"a.csv", "b.csv"}
    assert (
        workspace.diagnostics(inputs["a.csv"]["owner"])["issues"][0]["reason"] == "analysis_failed"
    )
    bad.write_text("prefab\nOne", encoding="utf-8")
    assert await engine.reconcile()
    assert len(store.query(target, direction="incoming")["edges"]) == 2
    assert store.status()["queue_idle"]


@pytest.mark.asyncio
async def test_supplementary_unicode_escape_is_resolved_and_persisted(setup):
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/Icon.prefab", keys=[{"scheme": "resources", "value": "\U0001f600"}]
    )
    caller = editor.add(
        2,
        "Assets/Caller.cs",
        r'using UnityEngine; class Caller { void Go() { Resources.Load("\uD83D\uDE00"); } }',
    )
    assert await engine.reconcile()
    assert store.query(caller)["edges"][0]["target"] == target
    restored = GraphWorkspace(GraphStore(store.path, store.scope))
    assert restored.documents() == workspace.documents()


@pytest.mark.asyncio
async def test_external_decode_failure_does_not_skip_later_files(setup, tmp_path):
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    root = tmp_path / "settings"
    root.mkdir()
    (root / "a.json").write_bytes(b'{"prefab":"\xff"}')
    (root / "b.json").write_text('{"prefab":"One"}', encoding="utf-8")
    workspace.set_source(
        "external:settings", {"root": str(root), "include": ["*.json"]}, expected_version=0
    )
    assert not await engine.reconcile()
    assert len(store.query(target, direction="incoming")["edges"]) == 1
    assert store.status()["errors"] == 1
