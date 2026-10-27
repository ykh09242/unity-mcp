"""Integration regressions for previously captured evidence and final delivery races."""

import pytest

from services.reference_graph.analysis import Analysis
from services.reference_graph.runtime import RuntimeObservations
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.store import Edge, RevisionConflict
from tests.test_reference_graph_reconcile import setup


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scheme,upgrade", [("resources", False), ("addressables", False), ("resources", True)]
)
async def test_historical_key_only_runtime_and_handle_follow_current_policy_and_release(
    setup, scheme, upgrade
):
    # Given an admitted target and a captured acquisition with only its logical key.
    store, workspace, editor, engine = setup
    editor.add(1, "Assets/Resources/Private.prefab", keys=[{"scheme": scheme, "value": "Private"}])
    assert await engine.reconcile()
    runtime = RuntimeObservations(workspace)
    capture = runtime.begin()["id"]
    event = {
        "sequence": 1,
        "kind": "acquire",
        "scheme": scheme,
        "key": "Private",
        "handle": "h",
        "owner_path": "Assets/Caller.cs",
        "target_path": "",
        "target_guid": "",
    }
    runtime.ingest(capture, 0, {"events": [event], "next_offset": 1})
    owner = "runtime:" + capture + ":1"
    edge_id = store.query(owner)["edges"][0]["id"]
    assert runtime.handles(capture)["handles"][0]["balance"] == 1
    if upgrade:
        from services.reference_graph.workspace import GraphWorkspace
        from services.reference_graph.reconcile import Reconciler
        from tests.test_reference_graph_reconcile import allowed

        with store._connect(write=True) as db:
            db.execute("DROP TABLE graph_runtime_bindings")
            db.execute("UPDATE graph_runtime_schema SET version=1")
        workspace = GraphWorkspace(store)
        engine = Reconciler(workspace, editor, allowed)
        runtime = RuntimeObservations(workspace)
    # When the target becomes ignored, then every current representation rejects the old key.
    editor.ignore_files[".gitignore"] = b"Assets/Resources/Private.prefab\n"
    editor.ignore_revision += 1
    assert await engine.reconcile()
    assert not store.query(owner)["edges"]
    assert not workspace.queries().explain(edge_id)["edges"]
    assert not workspace.queries().snapshot()["edges"]
    with pytest.raises(RevisionConflict, match="excluded"):
        runtime.events(capture)
    with pytest.raises(RevisionConflict, match="excluded"):
        runtime.handles(capture)
    # When the real release happens while hidden, then raw history and pairing remain intact.
    runtime.ingest(
        capture,
        1,
        {"events": [{"sequence": 2, "kind": "release", "handle": "h"}], "next_offset": 2},
    )
    with pytest.raises(RevisionConflict, match="excluded"):
        runtime.events(capture, after=1)
    with store._connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM graph_runtime_events WHERE session=?", (capture,)
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute(
                "SELECT released FROM graph_runtime_handles WHERE session=? AND handle='h'",
                (capture,),
            ).fetchone()[0]
            == 1
        )
    # When the exclusion is removed, then eligible history and the actual release return.
    editor.ignore_files.clear()
    editor.ignore_revision += 1
    assert await engine.reconcile()
    assert runtime.handles(capture)["handles"][0]["balance"] == 0
    assert store.query(owner)["edges"]
    assert store.query("runtime:" + capture + ":2")["edges"]


@pytest.mark.asyncio
async def test_policy_reset_preserves_external_identity_for_authored_endpoint_admission(
    setup, tmp_path
):
    # Given an external document with generated and authored references.
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/One.prefab", keys=[{"scheme": "resources", "value": "One"}]
    )
    root = tmp_path / "external"
    root.mkdir()
    (root / "private.json").write_text('{"key":"One"}')
    workspace.set_source(
        "external:settings",
        {
            "root": str(root),
            "include": ["*.json"],
            "mappings": [{"field": "/key", "scheme": "resources"}],
        },
        expected_version=0,
    )
    assert await engine.reconcile()
    owner = store.query(target, direction="incoming")["edges"][0]["owner"]
    store.put_declaration("kept", owner, Edge(target, "manual"), expected_version=0)
    # When a policy reset clears materialization before scanning, then identity is still excluded.
    workspace.set_policy({"exclude": ["private.json"]}, expected_version=0)
    assert workspace.external_nodes([owner])[0]["path"] == "private.json"
    assert workspace.external_inputs("external:settings") == {}
    assert await engine.reconcile()
    assert not store.query(owner)["edges"]
    assert store.query(owner)["excluded"]
    assert store.declarations()[0]["id"] == "kept"
    # When the exclusion is removed, then authored and generated references recover.
    workspace.set_policy({"exclude": []}, expected_version=1)
    assert await engine.reconcile()
    assert len(store.query(owner)["edges"]) == 2
    service = ReferenceGraphService(tmp_path / "service")
    try:
        with pytest.raises(RevisionConflict, match="admission is unavailable"):
            service._validate_manual_endpoints(workspace, ["external:settings:missing"])
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["query", "runtime_events", "runtime_handles"])
@pytest.mark.parametrize("change", ["native_rules", "policy"])
async def test_final_native_await_cannot_deliver_a_pre_policy_query(
    tmp_path, monkeypatch, action, change
):
    from core.config import config
    from tests.test_reference_graph_service import Context, Sender

    # Given a computed query and a native await that allows another reconciliation to finish.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    sender = Sender()
    service = ReferenceGraphService(tmp_path, send=sender)
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    ctx = Context(service)
    entry, _ = await service.resolve(ctx, "status")
    assert await entry.engine.reconcile()
    target, caller = "asset:" + "1" * 32, "asset:" + "2" * 32
    workspace = entry.workspace
    assert workspace.acquire("fixture")
    rows = [
        {
            "guid": target[6:],
            "path": "Assets/Private.prefab",
            "kind": "asset",
            "source_format": "none",
        },
        {"guid": caller[6:], "path": "Assets/Caller.cs", "kind": "script", "source_format": "none"},
    ]
    workspace.record_page("fixture", rows, workspace.progress(), "scan")
    generation = workspace.store.observe(caller, "unity", "initial", path="Assets/Caller.cs")
    workspace.publish(
        "fixture", caller, "unity", generation, Analysis([Edge(target, "dependency")])
    )
    workspace.release("fixture")
    options = {"node": caller}
    if action.startswith("runtime_"):
        runtime = RuntimeObservations(workspace)
        capture = runtime.begin()["id"]
        runtime.ingest(
            capture,
            0,
            {
                "events": [
                    {
                        "sequence": 1,
                        "kind": "acquire",
                        "scheme": "resources",
                        "key": "Private",
                        "handle": "h",
                        "owner_path": "Assets/Caller.cs",
                        "target_path": "Assets/Private.prefab",
                        "target_guid": target[6:],
                    }
                ],
                "next_offset": 1,
            },
        )
        options = {"id": capture}
    statuses = 0

    async def changed(instance, values):
        nonlocal statuses
        response = await sender(instance, values)
        if values["action"] == "status":
            statuses += 1
            if statuses == 2:
                if change == "policy":
                    workspace.set_policy({"exclude": ["Assets/Private.prefab"]}, expected_version=0)
                assert workspace.acquire("transition")
                if change == "native_rules":
                    workspace.set_ignore_rules(
                        "transition",
                        [(".gitignore", "Assets/Private.prefab\n")],
                        {"epoch": "epoch", "ignore_revision": 1, "snapshot": "replacement"},
                    )
                workspace.record_page("transition", rows, {}, "scan")
                workspace.finish_admission("transition")
                workspace.release("transition")
                if change == "native_rules":
                    response["ignore_revision"] = 1
        return response

    service.send = changed
    try:
        # When the final await returns a fresh policy, then the old page must fail without replay.
        with pytest.raises(RevisionConflict):
            await service.execute(ctx, action, options)
        assert not workspace.store.query(caller)["edges"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_delivery_fence_accepts_own_mutation_and_runtime_ingestion_once(
    tmp_path, monkeypatch
):
    from core.config import config
    from tests.test_reference_graph_service import Context, Sender

    # Given valid native runtime input and ordinary authored mutations.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    sender = Sender()
    reads = 0

    async def native(instance, values):
        nonlocal reads
        result = await sender(instance, values)
        if values["action"] == "runtime_start":
            result.update(events=[], next_offset=0, state="running")
        elif values["action"] == "runtime_read":
            reads += 1
            result.update(
                events=[
                    {
                        "sequence": 1,
                        "kind": "load",
                        "scheme": "resources",
                        "key": "unknown",
                        "owner_path": "Assets/Caller.cs",
                    }
                ],
                next_offset=1,
                state="running",
            )
        return result

    service = ReferenceGraphService(tmp_path, send=native)
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    ctx = Context(service)
    try:
        # When each operation advances its own revision, then its result is accepted without replay.
        capture = (await service.execute(ctx, "runtime_start", {}))["data"]["capture"]["id"]
        polled = await service.execute(ctx, "runtime_poll", {"id": capture})
        assert polled["data"]["capture"]["events"] == 1 and reads == 1
        declared = await service.execute(
            ctx,
            "declare",
            {
                "id": "one",
                "owner": "owner",
                "edge": {"target": "target", "kind": "manual"},
                "expected_version": 0,
            },
        )
        assert declared["data"]["version"] == 1
        policy = await service.execute(
            ctx, "set_policy", {"config": {"exclude": []}, "expected_version": 0}
        )
        assert policy["data"]["version"] == 1
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_historical_key_only_runtime_keeps_an_allowed_alias_candidate(setup):
    # Given a key shared by an ignored and an admitted asset.
    store, workspace, editor, engine = setup
    for number, name in ((1, "Private"), (2, "Public")):
        editor.add(
            number,
            f"Assets/Resources/{name}.prefab",
            keys=[{"scheme": "resources", "value": "Shared"}],
        )
    assert await engine.reconcile()
    runtime = RuntimeObservations(workspace)
    capture = runtime.begin()["id"]
    runtime.ingest(
        capture,
        0,
        {
            "events": [
                {
                    "sequence": 1,
                    "kind": "acquire",
                    "scheme": "resources",
                    "key": "Shared",
                    "handle": "h",
                }
            ],
            "next_offset": 1,
        },
    )
    # When only one candidate is excluded, then unresolved runtime evidence is still eligible.
    editor.ignore_files[".gitignore"] = b"Assets/Resources/Private.prefab\n"
    editor.ignore_revision += 1
    assert await engine.reconcile()
    assert store.query("runtime:" + capture + ":1")["edges"]
    assert runtime.handles(capture)["handles"][0]["balance"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 128])
async def test_runtime_binding_pages_and_refresh_are_batched(setup, monkeypatch, count):
    from contextlib import contextmanager

    # Given differently sized captures with the same target and policy.
    store, workspace, editor, engine = setup
    target = editor.add(
        1, "Assets/Resources/Private.prefab", keys=[{"scheme": "resources", "value": "Private"}]
    )
    assert await engine.reconcile()
    runtime = RuntimeObservations(workspace)
    capture = runtime.begin()["id"]
    runtime.ingest(
        capture,
        0,
        {
            "events": [
                {
                    "sequence": index + 1,
                    "kind": "acquire",
                    "scheme": "resources",
                    "key": "Private",
                    "handle": f"h{index}",
                }
                for index in range(count)
            ],
            "next_offset": count,
        },
    )
    connect = store._connect
    counts, queries = [], []

    @contextmanager
    def counted(**options):
        counts.append(options.get("write", False))
        with connect(**options) as connection:
            connection.set_trace_callback(queries.append)
            yield connection

    monkeypatch.setattr(store, "_connect", counted)
    # When reading either page size, then metadata uses one binding lookup and three connections.
    assert len(runtime.handles(capture, limit=1000)["handles"]) == count
    assert len(counts) == 3
    assert sum("FROM graph_runtime_bindings b" in query for query in queries) == 1
    monkeypatch.setattr(store, "_connect", connect)
    workspace.set_policy({"exclude": ["Assets/Resources/Private.prefab"]}, expected_version=0)
    assert workspace.acquire("refresh")
    workspace.record_page("refresh", [editor.assets[target[6:]]], {}, "scan")
    counts.clear()
    queries.clear()
    monkeypatch.setattr(store, "_connect", counted)
    try:
        # When publishing the changed admission, then history uses one transaction and one scan each.
        workspace.finish_admission("refresh")
        assert counts == [True]
        assert sum("SELECT * FROM graph_runtime_bindings" in query for query in queries) == 1
        assert sum("SELECT e.owner,e.provider,e.payload" in query for query in queries) == 1
    finally:
        workspace.release("refresh")


@pytest.mark.asyncio
async def test_ignored_import_move_and_delete_update_only_private_admission_metadata(setup):
    # Given an unchanged exclusion rule, an admitted asset and an initially unknown runtime key.
    store, workspace, editor, engine = setup
    editor.ignore_files[".gitignore"] = b"Assets/Resources/Private.prefab\n"
    target = editor.add(
        1, "Assets/Resources/Public.prefab", keys=[{"scheme": "resources", "value": "Public"}]
    )
    assert await engine.reconcile()
    runtime = RuntimeObservations(workspace)
    capture = runtime.begin()["id"]
    runtime.ingest(
        capture,
        0,
        {
            "events": [{"sequence": 1, "kind": "load", "scheme": "resources", "key": "Private"}],
            "next_offset": 1,
        },
    )
    owner = "runtime:" + capture + ":1"
    store.put_declaration("kept", "manual-owner", Edge(target, "manual"), expected_version=0)
    assert store.query(owner)["edges"]
    editor.calls.clear()
    # When the same GUID moves into an ignored path, then metadata masks runtime/manual references.
    editor.assets[target[6:]]["path"] = "Assets/Resources/Private.prefab"
    editor.assets[target[6:]]["keys"] = [{"scheme": "resources", "value": "Private"}]
    editor.event("Assets/Resources/Private.prefab")
    assert await engine.reconcile()
    assert not store.query(owner)["edges"]
    assert not store.query("manual-owner")["edges"]
    assert not any(action in {"inventory", "source", "dependencies"} for action, _ in editor.calls)
    with store._connect() as db:
        assert (
            db.execute("SELECT path FROM graph_asset_paths WHERE guid=?", (target[6:],)).fetchone()[
                0
            ]
            == "Assets/Resources/Private.prefab"
        )
        assert not db.execute("SELECT 1 FROM nodes WHERE id=?", (target,)).fetchone()
    # When it moves out, then collection and eligible declarations recover without a census.
    editor.assets[target[6:]]["path"] = "Assets/Resources/Public.prefab"
    editor.assets[target[6:]]["keys"] = [{"scheme": "resources", "value": "Public"}]
    editor.event("Assets/Resources/Public.prefab")
    assert await engine.reconcile()
    assert store.query(owner)["edges"] and store.query("manual-owner")["edges"]
    # When a new ignored asset is imported and deleted, then its key is learned and retired incrementally.
    hidden = editor.add(
        2, "Assets/Resources/Private.prefab", keys=[{"scheme": "resources", "value": "Private"}]
    )
    assert await engine.reconcile()
    assert not store.query(owner)["edges"]
    del editor.assets[hidden[6:]]
    editor.event("Assets/Resources/Private.prefab", deleted=True)
    assert await engine.reconcile()
    assert store.query(owner)["edges"]
    assert not any(action == "inventory" for action, _ in editor.calls)
    with store._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM graph_asset_paths WHERE guid=?", (hidden[6:],)
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM graph_blocked_keys WHERE guid=?", (hidden[6:],)
        ).fetchone()


@pytest.mark.asyncio
async def test_folder_only_move_retains_private_descendant_admission_without_census(setup):
    # Given known descendants and a folder move whose journal omits each child.
    store, workspace, editor, engine = setup
    editor.ignore_files[".gitignore"] = b"Assets/Hidden/\n"
    targets = [
        editor.add(
            number,
            f"Assets/Allowed/Resources/{number}.prefab",
            keys=[{"scheme": "resources", "value": str(number)}],
        )
        for number in (1, 2)
    ]
    assert await engine.reconcile()
    store.put_declaration("kept", "owner", Edge(targets[0], "manual"), expected_version=0)
    editor.calls.clear()
    for asset in editor.assets.values():
        asset["path"] = asset["path"].replace("Assets/Allowed/", "Assets/Hidden/")
    editor.event("Assets/Allowed", deleted=True)
    editor.event("Assets/Hidden")
    # When applying only those folder events, then known GUID metadata relocates before old-prefix retirement.
    assert await engine.reconcile()
    assert workspace.counts()["assets"] == 0
    assert not store.query("owner")["edges"]
    assert all(store.query(target)["excluded"] for target in targets)
    assert not any(action in {"inventory", "source", "dependencies"} for action, _ in editor.calls)
    assert sum(action == "asset" and "guid" in values for action, values in editor.calls) == 2


@pytest.mark.asyncio
async def test_census_removal_invalidates_private_only_catalog_keys(setup):
    # Given an excluded asset with no active graph_assets row and a cached key catalog.
    _, workspace, editor, engine = setup
    editor.ignore_files[".gitignore"] = b"Assets/Private.prefab\n"
    editor.add(1, "Assets/Private.prefab", keys=[{"scheme": "resources", "value": "Private"}])
    assert await engine.reconcile()
    assert workspace.key_admission().resolve_detail("resources", "Private")[1] == "excluded"
    assert workspace.acquire("census")
    try:
        # When a fresh census no longer sees it, then the private key cache is invalidated too.
        workspace.finish_census("census", "empty", {})
        workspace.finish_admission("census")
    finally:
        workspace.release("census")
    assert workspace.key_admission().resolve_detail("resources", "Private")[1] == "key_not_found"


@pytest.mark.asyncio
async def test_unknown_ignored_folder_import_coalesces_one_metadata_census(setup):
    # Given a warm graph and an unknown ignored folder with only folder-level journal events.
    store, workspace, editor, engine = setup
    editor.ignore_files[".gitignore"] = b"Assets/Hidden/\n"
    assert await engine.reconcile()
    hidden = "1".zfill(32)
    editor.assets[hidden] = {
        "guid": hidden,
        "path": "Assets/Hidden/Resources/Private.prefab",
        "kind": "asset",
        "source_format": "none",
        "keys": [{"scheme": "resources", "value": "Private"}],
    }
    editor.event("Assets/Hidden")
    editor.event("Assets/Hidden/Resources")
    editor.snapshot = None
    editor.calls.clear()
    # When journal metadata cannot enumerate unknown children, then one existing census discovers identities.
    assert await engine.reconcile()
    assert store.query("asset:" + hidden)["excluded"]
    assert workspace.counts()["assets"] == 0
    assert (
        sum(action == "inventory" and values["offset"] == 0 for action, values in editor.calls) == 1
    )
    assert not any(action in {"source", "dependencies"} for action, _ in editor.calls)


@pytest.mark.asyncio
async def test_large_ignored_import_page_batches_private_metadata_once(setup, monkeypatch):
    # Given a warm graph and one native page containing many excluded file imports.
    _, workspace, editor, engine = setup
    editor.ignore_files[".gitignore"] = b"Assets/Hidden/\n"
    assert await engine.reconcile()
    for number in range(128):
        editor.add(number + 1, f"Assets/Hidden/{number}.asset")
    native = engine._collector

    async def collect(action, **values):
        result = await native(action, **values)
        if action == "changes":
            result.update(
                changes=editor.events[values["after"] :],
                next_after=len(editor.events),
                has_more=False,
            )
        return result

    engine._collector = collect
    record = workspace.record_page
    batches = []

    def recorded(worker, assets, *args, **kwargs):
        batches.append(len(assets))
        return record(worker, assets, *args, **kwargs)

    monkeypatch.setattr(workspace, "record_page", recorded)
    editor.calls.clear()
    # When collecting the page, then only the required metadata RPC per file and one DB batch run.
    assert await engine.reconcile()
    assert batches == [128]
    assert sum(action == "asset" for action, _ in editor.calls) == 128
    assert not any(action in {"inventory", "source", "dependencies"} for action, _ in editor.calls)
