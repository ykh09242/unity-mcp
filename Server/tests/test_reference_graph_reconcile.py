"""Exercise durable reconciliation against the native paging protocol."""

from tests.timing import watchdog_timeout

import asyncio
from contextlib import contextmanager
import base64
import hashlib
import json
import threading
import time
import weakref

import pytest

from services.reference_graph.reconcile import Reconciler, drained_thread
from services.reference_graph.analysis import ANALYSIS_VERSION
from services.reference_graph.store import GraphScope, GraphStore, RevisionConflict, _hash
from services.reference_graph.workspace import GraphWorkspace


@pytest.mark.asyncio
async def test_analysis_reuses_unchanged_facts_and_reloads_after_cache_eviction(setup, monkeypatch):
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    caller = editor.add(
        2,
        "Assets/Caller.cs",
        "using UnityEngine; class C { void M() { Resources.Load(Keys.Value); } }",
    )
    keys = editor.add(3, "Assets/Keys.cs", 'class Keys { public const string Value = "One"; }')
    for number in range(4, 104):
        editor.add(
            number, f"Assets/T{number}.cs", f'class T{number} {{ const string Key = "unused"; }}'
        )
    assert await engine.reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {target}
    read = workspace.documents
    hydrated = []

    def capture(owners=None):
        result = read(owners)
        hydrated.extend(row["owner"] for row in result)
        return result

    monkeypatch.setattr(workspace, "documents", capture)
    editor.add(3, "Assets/Keys.cs", 'class Keys { public const string Value = "Missing"; }')
    editor.event("Assets/Keys.cs")
    assert await engine.reconcile()
    assert set(hydrated) <= {keys, caller}
    assert not store.query(caller)["edges"]
    engine.clear_caches()
    hydrated.clear()
    editor.add(3, "Assets/Keys.cs", 'class Keys { public const string Value = "One"; }')
    editor.event("Assets/Keys.cs")
    assert await engine.reconcile()
    assert len(set(hydrated)) == 102
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {target}
    cached = weakref.ref(engine._analysis_cache[3])
    engine._analysis_used = time.monotonic() - 301
    assert await engine.reconcile()
    assert cached() is None


@pytest.mark.asyncio
async def test_idle_external_scan_batches_bookkeeping_and_reuses_catalog(
    setup, tmp_path, monkeypatch
):
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    root = tmp_path / "settings"
    root.mkdir()
    for index in range(16):
        (root / f"{index}.json").write_text('{"prefab":"One"}', encoding="utf-8")
    workspace.set_source(
        "external:fixture",
        {
            "root": str(root),
            "include": ["*.json"],
            "mappings": [{"field": "/prefab", "scheme": "resources"}],
        },
        expected_version=0,
    )
    assert await engine.reconcile()
    before = store.query(target, direction="incoming")
    writes = 0
    connect = store._connect

    @contextmanager
    def counted(**kwargs):
        nonlocal writes
        writes += int(kwargs.get("write", False))
        with connect(**kwargs) as connection:
            yield connection

    assert workspace.acquire(engine.worker)
    monkeypatch.setattr(store, "_connect", counted)
    monkeypatch.setattr(workspace, "catalog", lambda: pytest.fail("Unchanged catalog was reloaded"))
    try:
        await engine._external(force=False)
        assert writes <= 5
        assert store.query(target, direction="incoming") == before
    finally:
        workspace.release(engine.worker)


@pytest.mark.asyncio
async def test_external_repository_head_refreshes_provenance_without_reparsing(
    setup, tmp_path, monkeypatch
):
    from services.reference_graph.external import ExternalSource

    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    root = tmp_path / "settings"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("1" * 40)
    (root / "data.json").write_text('{"prefab":"One"}')
    workspace.set_source(
        "external:fixture",
        {
            "root": str(root),
            "include": ["*.json"],
            "git_revision": True,
            "mappings": [{"field": "/prefab", "scheme": "resources"}],
        },
        expected_version=0,
    )
    assert await engine.reconcile()
    before = store.query(target, direction="incoming")["edges"][0]
    assert before["provenance"]["repository"]["commit"] == "1" * 40
    (root / ".git" / "HEAD").write_text("2" * 40)
    monkeypatch.setattr(
        ExternalSource, "read", lambda *_: pytest.fail("Unchanged source was reread")
    )
    assert await engine.reconcile()
    after = store.query(target, direction="incoming")["edges"][0]
    assert before["id"] == after["id"]
    assert after["provenance"]["repository"]["commit"] == "2" * 40
    assert before["provenance"]["sha256"] == after["provenance"]["sha256"]


@pytest.mark.asyncio
async def test_addressables_collection_failure_is_durable_and_not_missing_key(setup):
    store, workspace, editor, engine = setup
    target = editor.add(1, "Assets/A.prefab")
    editor.assets[target[6:]].update(
        addressables="unavailable",
        addressables_error="MissingMethodException",
        addressables_version="2.7",
    )
    caller = editor.add(
        2,
        "Assets/Caller.cs",
        'using UnityEngine.AddressableAssets; class C { void M() { Addressables.LoadAssetAsync<object>("enemy"); } }',
    )
    assert not await engine.reconcile()
    coverage = workspace.diagnostics(target)["coverage"]
    assert any(
        row["provider"] == "addressables" and row["state"] == "unsupported" for row in coverage
    )
    assert workspace.provider_status()["addressables"]["errors"] == 1
    assert workspace.diagnostics(caller)["issues"][0]["reason"] == "provider_unavailable"
    restored = GraphWorkspace(store)
    assert restored.provider_status()["addressables"]["errors"] == 1
    editor.assets[target[6:]].update(
        addressables="available",
        addressables_error=None,
        keys=[{"scheme": "addressables", "value": "enemy"}],
    )
    editor.event("Assets/A.prefab")
    assert await engine.reconcile()
    assert workspace.provider_status()["addressables"]["errors"] == 0
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {target}


@pytest.mark.asyncio
async def test_external_catalog_cache_is_invalidated_when_policy_empties_inventory(setup, tmp_path):
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    root = tmp_path / "settings"
    root.mkdir()
    (root / "a.json").write_text('{"prefab":"One"}', encoding="utf-8")
    workspace.set_source(
        "external:fixture",
        {
            "root": str(root),
            "include": ["*.json"],
            "mappings": [{"field": "/prefab", "scheme": "resources"}],
        },
        expected_version=0,
    )
    assert await engine.reconcile()
    assert store.query(target, direction="incoming")["edges"]
    workspace.set_policy({"exclude": ["Assets/**"]}, expected_version=0)
    assert await engine.reconcile()
    assert not workspace.catalog()
    assert not store.query(target, direction="incoming")["edges"]


@pytest.mark.asyncio
async def test_external_owner_survives_interrupted_enumeration_and_is_removed_after_deletion(
    setup, tmp_path, monkeypatch
):
    from services.reference_graph.external import ExternalSource

    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    root = tmp_path / "settings"
    root.mkdir()
    path = root / "a.json"
    path.write_text('{"prefab":"One"}', encoding="utf-8")
    workspace.set_source(
        "external:fixture",
        {
            "root": str(root),
            "include": ["*.json"],
            "mappings": [{"field": "/prefab", "scheme": "resources"}],
        },
        expected_version=0,
    )
    files = ExternalSource.files

    def interrupted(_):
        yield "a.json"
        raise OSError("interrupted enumeration")

    monkeypatch.setattr(ExternalSource, "files", interrupted)
    assert not await engine.reconcile()
    assert store.query(target, direction="incoming")["edges"]
    monkeypatch.setattr(ExternalSource, "files", files)
    path.unlink()
    assert await engine.reconcile()
    assert not store.query(target, direction="incoming")["edges"]
    assert store.status()["queue_idle"]


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
        self.ignore_files = {}
        self.ignore_revision = 0

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
                "ignore_state": "available",
                "ignore_revision": self.ignore_revision,
            }
        if action == "ignore_rules":
            entries = [
                {
                    "path": path,
                    "fingerprint": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
                for path, content in sorted(
                    self.ignore_files.items(), key=lambda item: item[0].encode("utf-16-be")
                )
            ]
            offset = params.get("offset", 0)
            end = min(offset + params.get("page_size", 256), len(entries))
            return {
                "files": entries[offset:end],
                "snapshot": "ignore-" + str(self.ignore_revision),
                "epoch": self.epoch,
                "revision": len(self.events),
                "ignore_revision": self.ignore_revision,
                "ignore_state": "available",
                "next_offset": end if end < len(entries) else None,
            }
        if action == "ignore_source":
            content = self.ignore_files[params["path"]]
            offset = params["offset"]
            end = min(offset + 11, len(content))
            return {
                "path": params["path"],
                "fingerprint": hashlib.sha256(content).hexdigest(),
                "bytes_base64": base64.b64encode(content[offset:end]).decode(),
                "next_offset": end if end < len(content) else None,
                "snapshot": "ignore-" + str(self.ignore_revision),
                "epoch": self.epoch,
                "ignore_revision": self.ignore_revision,
            }
        if action == "inventory":
            if self.snapshot is None:
                self.snapshot = (sorted(self.assets), len(self.events))
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
                "epoch": self.epoch,
                "revision": len(self.events),
                "changes": rows,
                "next_after": after,
                "has_more": after < len(self.events),
            }
        if action == "asset":
            if "guid" in params:
                return self.assets.get(params["guid"], {"present": False}).copy()
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
@pytest.mark.parametrize("coalesced", [False, True])
async def test_same_path_guid_replacement_retires_identity_and_survives_reopen(setup, coalesced):
    from services.reference_graph.store import Edge

    store, workspace, editor, engine = setup
    path = "Assets/Resources/One.prefab"
    old = editor.add(1, path, keys=[{"scheme": "resources", "value": "One"}])
    caller = editor.add(
        2, "Assets/Caller.cs", 'class C { void M() { UnityEngine.Resources.Load("One"); } }'
    )
    assert await engine.reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {old}
    store.put_declaration("kept", old, Edge(caller, "manual"), expected_version=0)
    dangling = store.observe("dangling-owner", "unity", "old-guid")
    store.publish("dangling-owner", "unity", dangling, [Edge(old, "unity_dependency")])
    del editor.assets[old[6:]]
    if not coalesced:
        editor.event(path, deleted=True)
    replacement = editor.add(3, path, keys=[{"scheme": "resources", "value": "One"}])
    if coalesced:
        # ReferenceGraphJournal.Record applies imported paths after deleted paths
        # in one batch, producing one deleted=False row with the current stamp.
        editor.events[-1]["file_stamp"] = "replacement-stamp"
    assert await engine.reconcile()
    assert {item["guid"] for item in workspace.catalog()} == {caller[6:], replacement[6:]}
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {replacement}
    assert store.query(old)["edges"][0]["provider"] == "manual"
    assert store.query("dangling-owner")["edges"][0]["target_exists"] is False
    restored_store = GraphStore(store.path, store.scope)
    restored = GraphWorkspace(restored_store)
    assert await Reconciler(restored, editor, allowed).reconcile()
    assert {item["guid"] for item in restored.catalog()} == {caller[6:], replacement[6:]}
    assert {edge["target"] for edge in restored_store.query(caller)["edges"]} == {replacement}


@pytest.mark.asyncio
async def test_replaced_script_cannot_republish_retired_facts_or_wrapper_definitions(setup):
    store, workspace, editor, engine = setup
    one = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    two = editor.add(
        2, "Assets/Resources/Two.prefab", keys=[{"scheme": "resources", "value": "Two"}]
    )
    old = editor.add(
        3,
        "Assets/Keys.cs",
        'class Keys { public const string Value = "One"; void M() { UnityEngine.Resources.Load("One"); } }',
    )
    caller = editor.add(
        4, "Assets/Caller.cs", "class C { void M() { UnityEngine.Resources.Load(Keys.Value); } }"
    )
    assert await engine.reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {one}
    del editor.assets[old[6:]]
    replacement = editor.add(
        5, "Assets/Keys.cs", 'class Keys { public const string Value = "Two"; }'
    )
    assert await engine.reconcile()
    assert not workspace.documents([old])
    assert not store.query(old)["edges"]
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {two}
    restored = GraphWorkspace(GraphStore(store.path, store.scope))
    assert await Reconciler(restored, editor, allowed).reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {two}
    assert set(restored.document_manifest()) == {replacement, caller}


@pytest.mark.asyncio
@pytest.mark.parametrize("overflow", ["document", "aggregate", "ignore"])
async def test_external_budget_failure_keeps_stale_evidence_and_recovers(setup, tmp_path, overflow):
    from services.reference_graph.collection_protocol import (
        CollectionBudgetExceeded,
        CollectionLimits,
    )

    store, workspace, editor, engine = setup
    target = editor.add(1, "Assets/One.prefab")
    root = tmp_path / "external"
    root.mkdir()
    document = root / "a.json"
    document.write_text('{"asset":"' + target[6:] + '"}', encoding="utf-8")
    workspace.set_source(
        "external:bounded", {"root": str(root), "include": ["*.json"]}, expected_version=0
    )
    assert await engine.reconcile()
    before = store.query(target, direction="incoming")["edges"]
    assert len(before) == 1
    if overflow == "document":
        document.write_bytes(b" " * 80 + document.read_bytes())
        limits = CollectionLimits(max_source_bytes=64)
        match = "max_source_bytes=64"
    elif overflow == "ignore":
        (root / ".gitignore").write_bytes(b"#" * 80)
        limits = CollectionLimits(max_source_bytes=64)
        match = "max_source_bytes=64"
    else:
        (root / "b.json").write_bytes(document.read_bytes())
        # Native metadata consumes response bytes separately; decoded input bytes
        # across both external documents must still share this attempt's counter.
        limits = CollectionLimits(max_bytes=80_000)
        document.write_bytes(b" " * 44_000 + document.read_bytes())
        (root / "b.json").write_bytes(document.read_bytes())
        match = "max_bytes=80000"
    bounded = Reconciler(workspace, editor, allowed, limits=limits)
    with pytest.raises(CollectionBudgetExceeded, match=match):
        await bounded.reconcile(force_external=True)
    remaining = store.query(target, direction="incoming")["edges"]
    assert {edge["id"] for edge in before} <= {edge["id"] for edge in remaining}
    assert all(edge["stale"] for edge in remaining)
    assert workspace.provider_status()["external:bounded"]["state"] == "failed"
    restored = GraphWorkspace(GraphStore(store.path, store.scope))
    assert await Reconciler(restored, editor, allowed).reconcile(force_external=True)
    assert all(not edge["stale"] for edge in store.query(target, direction="incoming")["edges"])


@pytest.mark.asyncio
async def test_compiler_context_change_reparses_same_bytes_without_import_or_repeated_idle_reads(
    setup,
):
    store, workspace, editor, _ = setup
    one = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    two = editor.add(
        2, "Assets/Resources/Two.prefab", keys=[{"scheme": "resources", "value": "Two"}]
    )
    caller = editor.add(
        3,
        "Assets/Caller.cs",
        """class Use { void Run() {
#if CLIENT
UnityEngine.Resources.Load("One");
#else
UnityEngine.Resources.Load("Two");
#endif
} }""",
    )
    build = {
        "state": "available",
        "assembly": "Game",
        "references": [],
        "defines": ["CLIENT"],
        "revision": "a",
    }

    async def collect(action, **params):
        if action == "compilation":
            return {
                "revision": build["revision"],
                "bindings": [
                    {"path": path, "signature": build["revision"]} for path in params["paths"]
                ],
            }
        page = await editor(action, **params)
        if action == "status":
            page["compilation_context"] = {
                key: value for key, value in build.items() if key in {"state", "revision"}
            }
        if action == "source":
            page["fingerprint"] += build["revision"]
            if params["offset"] == 0:
                page["compilation_context"] = {**build, "binding_revision": build["revision"]}
        return page

    engine = Reconciler(workspace, collect, allowed)
    assert await engine.reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {one}
    evidence = next(edge for edge in store.query(caller)["edges"] if edge["provider"] == "code")
    assert evidence["provenance"]["build"]["revision"] == "a"
    assert evidence["provenance"]["analysis_fingerprint"]
    saved = workspace.queries().snapshot(limit=1000)["snapshot"]["id"]
    build.update(defines=[], revision="b")
    assert await engine.reconcile()
    assert {edge["target"] for edge in store.query(caller)["edges"]} == {two}
    evidence = next(edge for edge in store.query(caller)["edges"] if edge["provider"] == "code")
    assert evidence["provenance"]["build"]["revision"] == "b"
    changed = workspace.queries().snapshot(limit=1000)["snapshot"]["id"]
    delta = workspace.queries().diff(saved, changed)
    previous = next(
        item["before"]
        for item in delta["changes"]
        if item["before"] and item["before"]["provider"] == "code"
    )
    assert previous["provenance"]["build"]["revision"] == "a"
    editor.calls.clear()
    assert await engine.reconcile()
    assert not any(action == "source" for action, _ in editor.calls)


@pytest.mark.asyncio
async def test_new_script_membership_only_rechecks_metadata_of_unchanged_compiler_inputs(setup):
    _, workspace, editor, _ = setup
    editor.add(1, "Assets/Existing.cs", "class Existing { }")
    revision = "first"

    async def collect(action, **params):
        if action == "compilation":
            return {
                "revision": revision,
                "bindings": [
                    {"path": path, "signature": "same-assembly"} for path in params["paths"]
                ],
            }
        page = await editor(action, **params)
        if action == "status":
            page["compilation_context"] = {"state": "available", "revision": revision}
        if action == "source":
            page["fingerprint"] += revision
            if params["offset"] == 0:
                page["compilation_context"] = {
                    "state": "available",
                    "revision": revision,
                    "binding_revision": "same-assembly",
                    "assembly": "Game",
                    "defines": [],
                    "references": [],
                }
        return page

    engine = Reconciler(workspace, collect, allowed)
    assert await engine.reconcile()
    revision = "second"
    added = editor.add(2, "Assets/Added.cs", "class Added { }")
    editor.calls.clear()
    assert await engine.reconcile()
    assert {params["guid"] for action, params in editor.calls if action == "source"} == {added[6:]}


@pytest.mark.asyncio
async def test_context_change_inside_source_continuation_cannot_publish(setup):
    _, workspace, editor, _ = setup
    owner = editor.add(
        1, "Assets/Caller.cs", 'class Use { void Run() { UnityEngine.Resources.Load("One"); } }'
    )
    build = {
        "state": "available",
        "assembly": "Game",
        "references": [],
        "defines": [],
        "revision": "a",
    }

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "status":
            page["compilation_context"] = {"state": "available", "revision": "a"}
        if action == "source":
            page["compilation_context"] = build.copy()
            page["fingerprint"] += "a" if params["offset"] == 0 else "b"
        return page

    engine = Reconciler(workspace, collect, allowed)
    with pytest.raises(RevisionConflict, match="Source changed"):
        await engine.reconcile()
    assert not workspace.documents([owner])


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
    assert {call[0] for call in editor.calls} == {"status", "changes", "ignore_rules"}
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
    assert await asyncio.wait_for(engine.reconcile(), watchdog_timeout())
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
        release.wait(watchdog_timeout())
        return "finished"

    task = asyncio.create_task(drained_thread(operation))
    await asyncio.to_thread(started.wait, watchdog_timeout())
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["cancel", "deadline", "budget"])
async def test_parser_cancellation_drains_owned_work_without_saving_facts(
    setup, monkeypatch, trigger
):
    import services.reference_graph.analysis as analysis
    from services.reference_graph.collection_protocol import CollectionBudgetExceeded

    _, workspace, editor, engine = setup
    editor.add(1, "Assets/Keys.cs", 'class Keys { const string Key="value"; }')
    entered, release, observed, exited = (threading.Event() for _ in range(4))
    tokenize, saved = analysis._tokens, []
    real_timeout, deadline = asyncio.timeout, None

    def capture_timeout(delay):
        nonlocal deadline
        deadline = real_timeout(delay)
        return deadline

    def held_tokens(source, check=lambda: None):
        calls = 0

        def checkpoint():
            nonlocal calls
            calls += 1
            if calls == 3:
                entered.set()
                assert release.wait(watchdog_timeout())
            try:
                check()
            except (RuntimeError, CollectionBudgetExceeded):
                observed.set()
                raise

        try:
            return tokenize(source, checkpoint)
        finally:
            exited.set()

    monkeypatch.setattr(analysis, "_tokens", held_tokens)
    monkeypatch.setattr(asyncio, "timeout", capture_timeout)
    monkeypatch.setattr(workspace, "save_document", lambda *args: saved.append(args))
    attempt = asyncio.create_task(engine.reconcile())
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        if trigger == "budget":
            engine._budget.started -= engine.limits.max_seconds + 1
        elif trigger == "deadline":
            deadline.reschedule(asyncio.get_running_loop().time())
        else:
            attempt.cancel()
        if trigger != "budget":
            assert await asyncio.to_thread(engine.stopped.wait, watchdog_timeout())
            # A repeated cancellation must not abandon the held physical parser.
            if trigger == "cancel":
                attempt.cancel()
                await asyncio.sleep(0)
        assert not attempt.done()
        assert not workspace.acquire("replacement")
        assert not saved
    finally:
        release.set()
        result = await asyncio.gather(attempt, return_exceptions=True)
    assert isinstance(
        result[0], asyncio.CancelledError if trigger == "cancel" else CollectionBudgetExceeded
    )
    assert observed.is_set() and exited.is_set()
    assert not saved
    assert workspace.acquire("replacement")
    workspace.release("replacement")


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
        release.wait(watchdog_timeout())
        return result

    monkeypatch.setattr(workspace, "acquire", held_acquire)
    task = asyncio.create_task(engine.reconcile())
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
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
async def test_same_byte_wrapper_repair_rechecks_new_callers_added_during_failure(setup):
    store, _, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    wrapper = editor.add(
        2,
        "Assets/Loader.cs",
        'using UnityEngine; class Loader { public static Object Get() => Resources.Load("One"); }',
    )
    assert await engine.reconcile()
    original = editor.sources[wrapper[6:]]
    editor.sources[wrapper[6:]] = b"\xffinvalid"
    editor.event("Assets/Loader.cs")
    assert not await engine.reconcile()
    caller = editor.add(3, "Assets/Caller.cs", "class Caller { void Go() { Loader.Get(); } }")
    editor.event("Assets/Caller.cs")
    assert not await engine.reconcile()
    editor.sources[wrapper[6:]] = original
    editor.event("Assets/Loader.cs")
    assert await engine.reconcile()
    assert store.query(caller)["edges"][0]["target"] == target


@pytest.mark.asyncio
async def test_native_collection_error_envelope_is_isolated_from_later_assets(setup, monkeypatch):
    import services.reference_graph.service as module

    store, workspace, editor, _ = setup
    bad = editor.add(1, "Assets/Bad.cs", "class Bad {}")
    target = editor.add(
        2, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    good = editor.add(
        3,
        "Assets/Good.cs",
        'using UnityEngine; class Good { void Go() { Resources.Load("One"); } }',
    )

    async def transport(_, instance, command, params, **options):
        if params["action"] == "source" and params["guid"] == bad[6:]:
            return {"success": False, "error": "reference_collection_unavailable"}
        action = params["action"]
        return {
            "success": True,
            "data": await editor(action, **{k: v for k, v in params.items() if k != "action"}),
        }

    monkeypatch.setattr(module, "send_with_unity_instance", transport)

    async def collect(action, **params):
        return await module.native_send("editor", {"action": action, **params})

    engine = Reconciler(workspace, collect, allowed)
    assert not await engine.reconcile()
    assert workspace.counts()["failed_assets"] == 1
    assert store.query(good)["edges"][0]["target"] == target


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
async def test_analysis_upgrade_reparses_old_facts_once_without_native_import(setup):
    store, workspace, editor, engine = setup
    editor.add(1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}])
    caller = editor.add(
        2,
        "Assets/Caller.cs",
        'using UnityEngine; class Caller { void Go() { Resources.Load("One"); } }',
    )
    assert await engine.reconcile()
    with store._connect(write=True) as connection:
        workspace._set(connection, "analysis_version", 1)
        connection.execute(
            "UPDATE graph_documents SET payload=json_remove(payload,'$.imports','$.version')"
        )
    editor.calls.clear()
    assert await engine.reconcile()
    assert any(action == "source" for action, _ in editor.calls)
    assert workspace.documents()[0]["data"]["version"] == ANALYSIS_VERSION
    assert store.query(caller)["edges"]
    editor.calls.clear()
    assert await engine.reconcile()
    assert not any(action == "source" for action, _ in editor.calls)


@pytest.mark.asyncio
async def test_analysis_upgrade_rechecks_unchanged_external_keys(setup, tmp_path):
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    root = tmp_path / "settings"
    root.mkdir()
    (root / "a.json").write_text('{"prefab":"one"}', encoding="utf-8")
    workspace.set_source(
        "external:settings", {"root": str(root), "include": ["*.json"]}, expected_version=0
    )
    assert await engine.reconcile()
    previous_key = _hash(
        (1, store.rules_state()["revision"], workspace.state("catalog_revision", 0))
    )
    with store._connect(write=True) as connection:
        # Simulate a ready v1 scan that could not resolve the lower-case key.
        connection.execute("DELETE FROM edges WHERE provider='external:settings'")
        connection.execute("UPDATE graph_external SET analysis_key=?", (previous_key,))
        workspace._set(connection, "analysis_version", 1)
        store._bump(connection)
    assert not store.query(target, direction="incoming")["edges"]
    assert await engine.reconcile()
    assert len(store.query(target, direction="incoming")["edges"]) == 1


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


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, -1, True, 1.0, "2", 2**63, 257])
async def test_inventory_rejects_invalid_continuation_before_recording(setup, offset):
    _, workspace, editor, _ = setup
    editor.add(1, "Assets/One.asset")

    async def collect(action, **params):
        if len(editor.calls) > 20:
            raise AssertionError("Collector exceeded the bounded malicious fixture")
        page = await editor(action, **params)
        if action == "inventory":
            page["next_offset"] = offset
        return page

    engine = Reconciler(workspace, collect, allowed)
    with pytest.raises(RevisionConflict):
        await asyncio.wait_for(engine.reconcile(), watchdog_timeout())
    assert workspace.counts()["assets"] == 0
    assert not workspace.progress().get("initialized")
    assert workspace.acquire("replacement")
    workspace.release("replacement")


@pytest.mark.asyncio
async def test_inventory_rejects_overlapping_guid_pages_without_finishing_census(setup):
    _, workspace, editor, _ = setup
    editor.add(1, "Assets/One.asset")
    editor.add(2, "Assets/Two.asset")
    editor.add(3, "Assets/Three.asset")

    async def collect(action, **params):
        if len(editor.calls) > 20:
            raise AssertionError("Collector exceeded the bounded malicious fixture")
        page = await editor(action, **params)
        if action == "inventory" and params["offset"]:
            page["assets"] = [editor.assets[f"{1:032x}"]]
        return page

    with pytest.raises(RevisionConflict):
        await Reconciler(workspace, collect, allowed).reconcile()
    assert workspace.counts()["assets"] == 2
    assert not workspace.progress().get("initialized")


@pytest.mark.asyncio
@pytest.mark.parametrize("next_offset", [0, -1, True, "1", 2, 2**63])
async def test_dependency_stream_rejects_nonprogress_and_inexact_offsets(setup, next_offset):
    store, workspace, editor, _ = setup
    owner = editor.add(1, "Assets/Owner.asset")

    async def collect(action, **params):
        if len(editor.calls) > 20:
            raise AssertionError("Collector exceeded the bounded malicious fixture")
        page = await editor(action, **params)
        if action == "dependencies":
            page.update(targets=[f"{2:032x}"], next_offset=next_offset)
        return page

    with pytest.raises(RevisionConflict):
        await asyncio.wait_for(
            Reconciler(workspace, collect, allowed).reconcile(), watchdog_timeout()
        )
    assert not store.query(owner)["edges"]
    assert workspace.dirty_assets()


@pytest.mark.asyncio
async def test_dependency_stream_rejects_overlap_without_publishing_partial_edges(setup):
    store, workspace, editor, _ = setup
    owner = editor.add(1, "Assets/Owner.asset")

    async def collect(action, **params):
        if len(editor.calls) > 20:
            raise AssertionError("Collector exceeded the bounded malicious fixture")
        page = await editor(action, **params)
        if action == "dependencies":
            page.update(targets=[f"{2:032x}"], next_offset=params["offset"] + 1)
        return page

    with pytest.raises(RevisionConflict):
        await asyncio.wait_for(
            Reconciler(workspace, collect, allowed).reconcile(), watchdog_timeout()
        )
    assert not store.query(owner)["edges"]
    assert workspace.dirty_assets()


@pytest.mark.asyncio
@pytest.mark.parametrize("next_offset", [0, -1, True, "37", 38, 2**63])
async def test_source_rejects_invalid_byte_continuations_before_decode(
    setup, monkeypatch, next_offset
):
    _, workspace, editor, _ = setup
    owner = editor.add(1, "Assets/Owner.cs", "class Owner {}")
    decoded = False
    original_decode = base64.b64decode

    def decode(*args, **kwargs):
        nonlocal decoded
        decoded = True
        return original_decode(*args, **kwargs)

    async def collect(action, **params):
        if len(editor.calls) > 20:
            raise AssertionError("Collector exceeded the bounded malicious fixture")
        page = await editor(action, **params)
        if action == "source":
            page["next_offset"] = next_offset
        return page

    monkeypatch.setattr(base64, "b64decode", decode)
    with pytest.raises(RevisionConflict):
        await asyncio.wait_for(
            Reconciler(workspace, collect, allowed).reconcile(), watchdog_timeout()
        )
    assert not decoded
    assert not workspace.documents([owner])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "has_more,next_after", [(True, 0), ("false", 0), (1, 0), (True, True), (True, 2**63)]
)
async def test_changes_rejects_nonprogress_and_adversarial_types(setup, has_more, next_after):
    _, workspace, editor, engine = setup
    assert await engine.reconcile()

    async def collect(action, **params):
        if len(editor.calls) > 20:
            raise AssertionError("Collector exceeded the bounded malicious fixture")
        page = await editor(action, **params)
        if action == "changes":
            page.update(has_more=has_more, next_after=next_after)
        return page

    with pytest.raises(RevisionConflict):
        await asyncio.wait_for(
            Reconciler(workspace, collect, allowed).reconcile(), watchdog_timeout()
        )
    assert workspace.progress()["phase"] == "error"


@pytest.mark.asyncio
async def test_aggregate_page_budget_preserves_census_checkpoint_and_resumes(setup):
    from services.reference_graph.collection_protocol import (
        CollectionBudgetExceeded,
        CollectionLimits,
    )

    _, workspace, editor, _ = setup
    for number in range(8):
        editor.add(number, f"Assets/{number}.asset")
    engine = Reconciler(workspace, editor, allowed, limits=CollectionLimits(max_pages=4))
    with pytest.raises(CollectionBudgetExceeded, match="max_pages=4"):
        await engine.reconcile()
    assert workspace.counts()["assets"] == 4
    assert workspace.progress()["census"]["offset"] == 4
    assert workspace.progress()["phase"] == "error"
    assert not workspace.progress().get("initialized")
    assert workspace.acquire("replacement")
    workspace.release("replacement")
    assert await Reconciler(workspace, editor, allowed).reconcile()
    assert workspace.counts()["assets"] == 8
    assert workspace.progress()["phase"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["max_items", "max_bytes"])
async def test_aggregate_inventory_budgets_reject_before_database_write(setup, limit):
    from services.reference_graph.collection_protocol import (
        CollectionBudgetExceeded,
        CollectionLimits,
    )

    _, workspace, editor, _ = setup
    for number in range(2):
        editor.add(number, f"Assets/{number}.asset")
    limits = CollectionLimits(**{limit: 1 if limit == "max_items" else 250})
    with pytest.raises(CollectionBudgetExceeded, match=limit):
        await Reconciler(workspace, editor, allowed, limits=limits).reconcile()
    assert workspace.counts()["assets"] == 0
    assert not workspace.progress().get("initialized")


@pytest.mark.asyncio
async def test_source_memory_budget_rejects_before_decoding_or_saving(setup, monkeypatch):
    from services.reference_graph.collection_protocol import (
        CollectionBudgetExceeded,
        CollectionLimits,
    )

    _, workspace, editor, _ = setup
    owner = editor.add(1, "Assets/Owner.cs", "class Owner {}")
    monkeypatch.setattr(
        base64, "b64decode", lambda *args, **kwargs: pytest.fail("Decoded over budget")
    )
    engine = Reconciler(workspace, editor, allowed, limits=CollectionLimits(max_source_bytes=10))
    with pytest.raises(CollectionBudgetExceeded, match="max_source_bytes=10"):
        await engine.reconcile()
    assert not workspace.documents([owner])
    assert workspace.dirty_assets()
    assert workspace.progress()["phase"] == "error"


@pytest.mark.asyncio
async def test_dependency_memory_budget_keeps_previous_edges_and_dirty_state(setup):
    from services.reference_graph.collection_protocol import (
        CollectionBudgetExceeded,
        CollectionLimits,
    )

    store, workspace, editor, _ = setup
    owner = editor.add(1, "Assets/Owner.asset")
    growing = False

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "dependencies":
            offset = params["offset"]
            page.update(
                targets=[f"{offset + 2 if growing else 9:032x}"],
                next_offset=offset + 1 if growing else None,
            )
        return page

    assert await Reconciler(workspace, collect, allowed).reconcile()
    assert {row["target"] for row in store.query(owner)["edges"]} == {"asset:" + f"{9:032x}"}
    growing = True
    editor.event("Assets/Owner.asset")
    engine = Reconciler(
        workspace, collect, allowed, limits=CollectionLimits(max_dependency_items=2)
    )
    with pytest.raises(CollectionBudgetExceeded, match="max_dependency_items=2"):
        await engine.reconcile()
    remaining = store.query(owner)["edges"]
    assert {row["target"] for row in remaining} == {"asset:" + f"{9:032x}"}
    assert all(row["stale"] for row in remaining)
    assert workspace.dirty_assets()
    assert sum(action == "dependencies" for action, _ in editor.calls) == 4


@pytest.mark.asyncio
async def test_many_valid_dependency_pages_keep_all_edges(setup):
    store, workspace, editor, _ = setup
    owner = editor.add(1, "Assets/Owner.asset")
    count = 2048

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "dependencies":
            offset = params["offset"]
            page.update(
                targets=[f"{offset + 2:032x}"],
                next_offset=offset + 1 if offset + 1 < count else None,
            )
        return page

    engine = Reconciler(workspace, collect, allowed)
    assert await engine.reconcile()
    with store._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM edges WHERE owner=?", (owner,)).fetchone()[0]
            == count
        )
    assert sum(action == "dependencies" for action, _ in editor.calls) == count


@pytest.mark.asyncio
async def test_many_valid_source_pages_keep_the_complete_document(setup):
    _, workspace, editor, _ = setup
    content = "// source page\n" * 2048 + "class CompleteSource {}"
    owner = editor.add(1, "Assets/Owner.cs", content)
    engine = Reconciler(workspace, editor, allowed)
    assert await engine.reconcile()
    assert len(workspace.documents([owner])) == 1
    assert sum(action == "source" for action, _ in editor.calls) > 512


@pytest.mark.asyncio
async def test_budget_deadline_cancels_slow_collection_and_records_recoverable_error(setup):
    from services.reference_graph.collection_protocol import (
        CollectionBudgetExceeded,
        CollectionLimits,
    )

    _, workspace, editor, _ = setup
    cancelled = asyncio.Event()

    async def collect(action, **params):
        if action == "inventory":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return await editor(action, **params)

    engine = Reconciler(workspace, collect, allowed, limits=CollectionLimits(max_seconds=1))
    with pytest.raises(CollectionBudgetExceeded, match="max_seconds=1"):
        await engine.reconcile()
    assert cancelled.is_set()
    assert workspace.progress()["phase"] == "error"
    assert not workspace.progress().get("initialized")
    assert workspace.acquire("replacement")
    workspace.release("replacement")


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_attempt_deadline_retains_lease_until_collector_cleanup_finishes(
    setup, repeat_cancel, monkeypatch
):
    from services.reference_graph.collection_protocol import (
        CollectionBudgetExceeded,
        CollectionLimits,
    )

    _, workspace, editor, _ = setup
    closing, release = asyncio.Event(), asyncio.Event()
    real_timeout, deadline = asyncio.timeout, None

    def capture_timeout(delay):
        nonlocal deadline
        deadline = real_timeout(delay)
        return deadline

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)

    async def collect(action, **params):
        if action == "inventory":
            # Expire the real attempt scope only once the owned operation starts;
            # slow SQLite/fixture setup must not consume this test's trigger.
            assert deadline is not None
            deadline.reschedule(asyncio.get_running_loop().time())
            try:
                await asyncio.Event().wait()
            finally:
                closing.set()
                await release.wait()
        return await editor(action, **params)

    engine = Reconciler(workspace, collect, allowed, limits=CollectionLimits())
    attempt = asyncio.create_task(engine.reconcile())
    try:
        await asyncio.wait_for(closing.wait(), watchdog_timeout())
        if repeat_cancel:
            attempt.cancel()
            await asyncio.sleep(0)
            attempt.cancel()
            await asyncio.sleep(0)
        assert not attempt.done()
        assert not workspace.acquire("replacement")
    finally:
        release.set()
        result = await asyncio.gather(attempt, return_exceptions=True)
    assert isinstance(
        result[0], asyncio.CancelledError if repeat_cancel else CollectionBudgetExceeded
    )
    assert workspace.acquire("replacement")
    workspace.release("replacement")
    assert workspace.progress()["phase"] != "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("following", [1, 2])
async def test_inventory_rejects_decreasing_and_fixed_positive_offsets(setup, following):
    _, workspace, editor, _ = setup
    for number in range(3):
        editor.add(number, f"Assets/{number}.asset")

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "inventory" and params["offset"]:
            page["next_offset"] = following
        return page

    with pytest.raises(RevisionConflict):
        await Reconciler(workspace, collect, allowed).reconcile()
    assert workspace.counts()["assets"] == 2
    assert not workspace.progress().get("initialized")


@pytest.mark.asyncio
async def test_missing_source_continuation_is_rejected_before_document_publication(setup):
    _, workspace, editor, _ = setup
    owner = editor.add(1, "Assets/Owner.cs", "class Owner {}")

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "source":
            page.pop("next_offset")
        return page

    with pytest.raises(RevisionConflict, match="explicit next_offset"):
        await Reconciler(workspace, collect, allowed).reconcile()
    assert not workspace.documents([owner])


@pytest.mark.asyncio
async def test_transport_timeout_is_not_mislabeled_as_whole_attempt_budget(setup):
    _, workspace, _, _ = setup

    async def collect(*args, **kwargs):
        raise TimeoutError("Individual collector request expired")

    with pytest.raises(TimeoutError, match="Individual collector request expired"):
        await Reconciler(workspace, collect, allowed).reconcile()
    assert "max_seconds" not in workspace.progress()["error"]


@pytest.mark.parametrize(
    "name",
    [
        "max_pages",
        "max_items",
        "max_bytes",
        "max_seconds",
        "max_source_bytes",
        "max_dependency_items",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "nan", "1.5", str(2**63)])
def test_collection_operator_limits_reject_unbounded_or_invalid_settings(monkeypatch, name, value):
    from services.reference_graph.collection_protocol import CollectionLimits, ENV_PREFIX

    monkeypatch.setenv(ENV_PREFIX + name.upper(), value)
    with pytest.raises(ValueError):
        CollectionLimits.from_environment()


def test_collection_operator_can_provision_larger_limits(monkeypatch):
    from services.reference_graph.collection_protocol import CollectionLimits, ENV_PREFIX

    monkeypatch.setenv(ENV_PREFIX + "MAX_SOURCE_BYTES", str(1024**3))
    monkeypatch.setenv(ENV_PREFIX + "MAX_SECONDS", str(7 * 24 * 3600))
    limits = CollectionLimits.from_environment()
    assert limits.max_source_bytes == 1024**3
    assert limits.max_seconds == 7 * 24 * 3600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch", [{"epoch": "replacement"}, {"revision": True}, {"revision": 10, "has_more": False}]
)
async def test_change_pages_reject_epoch_revision_and_completion_mismatch(setup, patch):
    _, workspace, editor, engine = setup
    assert await engine.reconcile()

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "changes":
            page.update(patch)
        return page

    with pytest.raises(RevisionConflict):
        await Reconciler(workspace, collect, allowed).reconcile()
    assert workspace.progress()["phase"] == "error"


def test_collection_offset_overflow_is_rejected_without_allocation():
    from services.reference_graph.collection_protocol import continuation, MAX_OFFSET

    with pytest.raises(RevisionConflict, match="overflowed"):
        continuation({"next_offset": None}, MAX_OFFSET, 1)


@pytest.mark.asyncio
async def test_folder_only_inventory_can_advance_across_many_empty_pages(setup):
    _, workspace, editor, _ = setup
    count = 513

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "inventory":
            following = params["offset"] + params["page_size"]
            page.update(next_offset=following if following < count * 256 else None)
        return page

    assert await Reconciler(workspace, collect, allowed).reconcile()
    assert workspace.counts()["assets"] == 0
    assert sum(action == "inventory" for action, _ in editor.calls) == count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch", [{"snapshot": "replacement"}, {"epoch": "replacement"}, {"revision": 42}]
)
async def test_inventory_rejects_changed_snapshot_identity_before_recording(setup, patch):
    _, workspace, editor, _ = setup
    for number in range(3):
        editor.add(number, f"Assets/{number}.asset")

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "inventory" and params["offset"]:
            page.update(patch)
        return page

    with pytest.raises(RevisionConflict, match="snapshot changed"):
        await Reconciler(workspace, collect, allowed).reconcile()
    assert workspace.counts()["assets"] == 2
    assert not workspace.progress().get("initialized")
