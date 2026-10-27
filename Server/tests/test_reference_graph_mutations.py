"""Mutation outcomes are distinct from authoritative imported graph evidence."""

from tests.timing import watchdog_timeout

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio

from services.reference_graph.mutations import MutationReceipts
from services.reference_graph.store import Edge, GraphScope, GraphStore, RevisionConflict
from services.reference_graph.workspace import GraphWorkspace
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.mutation_tracking import tracked_mutation, target_paths
from core.config import config


GUID = "a" * 32
PATH = "Assets/Receipt.asset"


@pytest.fixture
def graph(tmp_path):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("user", "editor", "project")))
    workspace.acquire("worker")
    workspace.record_page(
        "worker",
        [{"guid": GUID, "path": PATH, "kind": "asset", "source_format": "none"}],
        {},
        "seen",
    )
    return workspace, MutationReceipts(workspace)


def probe(stamp="original", present=True):
    return {
        "path": PATH,
        "guid": GUID if present else "",
        "present": present,
        "folder": False,
        "stamp": stamp if present else None,
        "dependency": "dependency" if present else None,
    }


def begin(receipts):
    return receipts.begin([probe()], {"epoch": "epoch", "revision": 3})["id"]


def test_receipt_survives_restart_and_separates_unknown_outcome(graph):
    workspace, receipts = graph
    identity = begin(receipts)
    receipts.finish(identity, "unknown", "tool_response")
    restored = MutationReceipts(GraphWorkspace(workspace.store))
    assert restored.read(identity)["outcome"] == "unknown"
    assert restored.read(identity)["state"] == "pending"
    assert restored.read(identity)["mutation_confirmed"] is False


def test_changed_file_needs_import_event_even_when_graph_versions_match(graph):
    workspace, receipts = graph
    identity = begin(receipts)
    receipts.finish(identity, "accepted", "tool_response")
    workspace.clean_asset("worker", GUID, version=probe("changed"))
    assert (
        receipts.check_targets(identity, [probe("changed")], replacement_epoch=False)
        == "awaiting_import"
    )
    receipts.events(identity, [{"path": PATH, "revision": 4, "file_stamp": "changed"}], "epoch", 4)
    assert receipts.check_targets(identity, [probe("changed")], replacement_epoch=False) is None


def test_noop_does_not_require_import_or_refresh(graph):
    workspace, receipts = graph
    identity = begin(receipts)
    receipts.finish(identity, "accepted", "tool_response")
    workspace.clean_asset("worker", GUID, version=probe())
    assert receipts.check_targets(identity, [probe()], replacement_epoch=False) is None


def test_recollection_skips_other_current_targets_in_the_same_page(graph):
    workspace, receipts = graph
    identity = begin(receipts)
    current = {**probe(), "guid": "b" * 32, "path": "Assets/Current.asset"}
    workspace.record_page(
        "worker", [{**current, "kind": "asset", "source_format": "none"}], {}, "seen"
    )
    workspace.clean_asset("worker", GUID, version=probe())
    workspace.clean_asset("worker", current["guid"], version=current)
    assert receipts.request_collection(identity, [probe("changed"), current])
    assert [row["guid"] for row in workspace.dirty_assets()] == [GUID]


def test_late_collection_stays_pending_and_delete_requires_journal(graph):
    workspace, receipts = graph
    identity = begin(receipts)
    receipts.finish(identity, "accepted", "tool_response")
    receipts.events(identity, [{"path": PATH, "revision": 4, "file_stamp": "changed"}], "epoch", 4)
    assert (
        receipts.check_targets(identity, [probe("changed")], replacement_epoch=False)
        == "awaiting_collection"
    )
    assert (
        receipts.check_targets(identity, [probe(present=False)], replacement_epoch=False)
        == "awaiting_collection"
    )
    workspace.remove_path("worker", PATH)
    assert receipts.check_targets(identity, [probe(present=False)], replacement_epoch=False) is None


def test_receipts_are_scope_owned_and_finish_is_immutable(graph, tmp_path):
    _, receipts = graph
    identity = begin(receipts)
    other = MutationReceipts(
        GraphWorkspace(GraphStore.open(tmp_path, GraphScope("other", "editor", "project")))
    )
    with pytest.raises(ValueError):
        other.read(identity)
    receipts.finish(identity, "accepted", "tool_response")
    assert receipts.finish(identity, "accepted", "tool_response")["outcome"] == "accepted"
    with pytest.raises(RevisionConflict):
        receipts.finish(identity, "failed", "tool_response")


def test_incident_delta_is_paged_and_keeps_authored_evidence(graph):
    workspace, receipts = graph
    store = workspace.store
    store.put_declaration(
        "manual", "asset:" + GUID, Edge("target-a", "declared"), expected_version=0
    )
    identity = begin(receipts)
    receipts.finish(identity, "accepted", "tool_response")
    store.put_declaration(
        "manual", "asset:" + GUID, Edge("target-b", "declared"), expected_version=1
    )
    receipts.complete(identity, {"epoch": "epoch", "revision": 3}, store.status()["revision"])
    page = receipts.diff(identity, limit=1)
    assert page["changes"][0]["change"] == "modified"
    assert page["historical"]
    assert store.declarations()[0]["version"] == 2
    assert receipts.release(identity)
    assert store.declarations()[0]["version"] == 2


class Native:
    def __init__(self):
        self.revision = 0
        self.stamp = "original"
        self.present = True
        self.failed = False
        self.events = []

    def edit(self, *, imported=True, present=True):
        self.stamp = "changed"
        self.present = present
        if imported:
            self.revision += 1
            self.events.append(
                {
                    "path": PATH,
                    "revision": self.revision,
                    "deleted": not present,
                    "file_stamp": self.stamp if present else None,
                }
            )

    def asset(self):
        return {
            "guid": GUID,
            "path": PATH,
            "kind": "asset",
            "source_format": "none",
            "present": self.present,
        }

    async def __call__(self, instance, params):
        response = {
            "instance_id": instance,
            "project_id": "project",
            "epoch": "epoch",
            "revision": self.revision,
            "compilation_failed": self.failed,
            "ignore_state": "available",
            "ignore_revision": 0,
        }
        action = params["action"]
        if action == "ignore_rules":
            response.update(snapshot="ignore", files=[], next_offset=None)
        elif action == "inventory":
            response.update(
                snapshot="snapshot", assets=[self.asset()] if self.present else [], next_offset=None
            )
        elif action == "changes":
            response.update(
                requires_census=False,
                changes=[row for row in self.events if row["revision"] > params["after"]],
                next_after=self.revision,
                has_more=False,
            )
        elif action == "asset":
            response.update(self.asset())
        elif action == "dependencies":
            response.update(
                self.asset(),
                fingerprint="dependency",
                file_stamp=self.stamp,
                targets=[],
                snapshot="dependencies",
                next_offset=None,
            )
        elif action == "probe":
            response["targets"] = [
                {**probe(self.stamp, self.present), "path": path} for path in params["paths"]
            ]
        return response


class Context:
    def __init__(self, service):
        self.instance = "editor"
        self.fastmcp = SimpleNamespace(_reference_graph_service=service)

    async def get_state(self, name):
        return self.instance if name == "unity_instance" else None


@pytest_asyncio.fixture
async def service_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    native = Native()
    service = ReferenceGraphService(tmp_path, send=native)
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    ctx = Context(service)
    entry, _ = await service.resolve(ctx, "status")
    assert await entry.engine.reconcile()
    yield service, native, ctx, entry
    await service.aclose()


async def call(service, ctx, action, **options):
    return (await service.execute(ctx, action, options))["data"]


@pytest.mark.asyncio
async def test_delayed_import_failed_compilation_and_noop(service_graph):
    service, native, ctx, entry = service_graph
    receipt = (await call(service, ctx, "begin_mutation", paths=[PATH]))["receipt"]
    await call(service, ctx, "finish_mutation", id=receipt["id"], outcome="accepted")
    native.edit(imported=False)
    result = await call(service, ctx, "await_mutation", id=receipt["id"])
    assert result["pending_reason"] == "awaiting_import"
    native.edit()
    native.failed = True
    assert (await call(service, ctx, "await_mutation", id=receipt["id"]))[
        "pending_reason"
    ] == "editor_not_ready"
    native.failed = False
    assert await entry.engine.reconcile()
    result = await call(service, ctx, "await_mutation", id=receipt["id"])
    assert result["receipt"]["state"] == "synchronized"
    assert result["receipt"]["mutation_confirmed"] is False  # caller assertion
    noop = (await call(service, ctx, "begin_mutation", paths=[PATH]))["receipt"]
    await call(service, ctx, "finish_mutation", id=noop["id"], outcome="accepted")
    assert (await call(service, ctx, "await_mutation", id=noop["id"]))["receipt"][
        "state"
    ] == "synchronized"


@pytest.mark.asyncio
async def test_cancelled_wait_preserves_receipt_and_scope_isolation(service_graph):
    service, native, ctx, entry = service_graph
    identity = (await call(service, ctx, "begin_mutation", paths=[PATH]))["receipt"]["id"]
    await call(service, ctx, "finish_mutation", id=identity, outcome="unknown")
    native.edit(imported=False)
    task = asyncio.create_task(call(service, ctx, "await_mutation", id=identity, wait_seconds=3600))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert entry.workspace.mutations().read(identity)["state"] == "pending"
    ctx.instance = "other-editor"
    with pytest.raises(ValueError, match="scope"):
        await call(service, ctx, "await_mutation", id=identity)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected",
    [
        ({"success": True}, "accepted"),
        ({"success": False, "message": "connection closed"}, "unknown"),
        ({"success": False, "message": "invalid property"}, "failed"),
    ],
)
async def test_write_boundary_records_once_and_preserves_native_outcome(
    service_graph, response, expected
):
    service, native, ctx, entry = service_graph
    calls = []

    async def invoke():
        calls.append("write")
        native.edit()
        return response

    result = await tracked_mutation(
        ctx, ctx.instance, "manage_asset", {"action": "modify", "path": PATH}, invoke
    )
    assert calls == ["write"]
    assert result["success"] == response["success"]
    receipt = result["data"]["reference_graph"]
    assert receipt["outcome"] == expected
    assert await entry.engine.reconcile()
    observed = await call(service, ctx, "await_mutation", id=receipt["id"])
    assert observed["receipt"]["state"] == "synchronized"
    assert observed["receipt"]["mutation_confirmed"] == (expected == "accepted")


@pytest.mark.asyncio
async def test_inactive_graph_and_preview_make_no_extra_requests(service_graph):
    service, native, ctx, entry = service_graph
    entry.watching = False
    native.__call__ = lambda *_: pytest.fail("unexpected native query")

    async def invoke():
        return {"success": True}

    assert await tracked_mutation(
        ctx, ctx.instance, "manage_asset", {"action": "modify", "path": PATH}, invoke
    ) == {"success": True}
    assert (
        target_paths(
            "manage_script",
            {
                "action": "apply_text_edits",
                "path": "Assets",
                "name": "X",
                "options": {"preview": True},
            },
        )
        is None
    )


@pytest.mark.asyncio
async def test_preparation_reprobes_a_moving_import_checkpoint_without_replaying_write(
    service_graph, monkeypatch
):
    # Given: an import lands after resolution but before the target probe.
    _, native, ctx, entry = service_graph
    collect = entry.collect
    probes = []
    writes = []

    async def moving(action, **options):
        if action == "probe":
            probes.append(action)
            if len(probes) == 1:
                native.edit()
        return await collect(action, **options)

    monkeypatch.setattr(entry, "collect", moving)

    async def invoke():
        writes.append("write")
        native.edit()
        return {"success": True}

    # When: the automatic boundary prepares and dispatches one mutation.
    result = await tracked_mutation(
        ctx, ctx.instance, "manage_asset", {"action": "modify", "path": PATH}, invoke
    )

    # Then: a fresh baseline is captured before the sole write.
    assert len(probes) == 2
    assert writes == ["write"]
    assert result["data"]["reference_graph"]["state"] == "pending"
    receipt = entry.workspace.mutations().read(result["data"]["reference_graph"]["id"])
    assert receipt["baseline"]["revision"] == 1


@pytest.mark.asyncio
async def test_receipt_metadata_survives_unrelated_graph_progress(service_graph, monkeypatch):
    # Given: a background worker publishes between source-list reads.
    service, _, ctx, entry = service_graph
    store = entry.workspace.store
    sources = store.sources

    def progressing_sources():
        with store._connect(write=True) as connection:
            store._bump(connection)
        return sources()

    monkeypatch.setattr(store, "sources", progressing_sources)

    # When: a receipt captures its own durable before snapshot.
    result = await call(service, ctx, "begin_mutation", paths=[PATH])

    # Then: unrelated publication cannot lose the newly prepared receipt ID.
    receipt = result["receipt"]
    assert receipt["state"] == "open"
    assert receipt["targets"] == 1
    assert entry.workspace.mutations().read(receipt["id"])["id"] == receipt["id"]


@pytest.mark.asyncio
async def test_continuously_moving_preparation_stays_unavailable_and_dispatches_once(
    service_graph, monkeypatch
):
    # Given: every probe observes another import revision.
    _, native, ctx, entry = service_graph
    collect = entry.collect
    probes = []
    writes = []

    async def moving(action, **options):
        if action == "probe":
            probes.append(action)
            native.edit()
        return await collect(action, **options)

    monkeypatch.setattr(entry, "collect", moving)

    async def invoke():
        writes.append("write")
        return {"success": True}

    # When: preparation cannot reach a stable checkpoint within its read budget.
    result = await tracked_mutation(
        ctx, ctx.instance, "manage_asset", {"action": "modify", "path": PATH}, invoke
    )

    # Then: the native result is preserved and tracking failure stays explicit.
    assert len(probes) == 3
    assert writes == ["write"]
    assert result["success"] is True
    assert result["data"]["reference_graph"] == {
        "state": "unavailable",
        "reason": "RevisionConflict",
    }


@pytest.mark.asyncio
async def test_receipt_metadata_still_refuses_a_changed_external_source_grant(
    service_graph, monkeypatch, tmp_path
):
    # Given: a valid source participates in the before snapshot.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", json.dumps([str(tmp_path)]))
    service, _, ctx, entry = service_graph
    store = entry.workspace.store
    store.set_source(
        "external:fixture", {"root": str(tmp_path), "include": ["*.json"]}, expected_version=0
    )
    collect = entry.collect

    async def revoked(action, **options):
        result = await collect(action, **options)
        if action == "probe":
            store.set_source("external:fixture", {}, expected_version=1, enabled=False)
        return result

    monkeypatch.setattr(entry, "collect", revoked)

    # When: the source grant changes during preparation.
    # Then: the new metadata fence still fails closed.
    with pytest.raises(RevisionConflict, match="source grants"):
        await call(service, ctx, "begin_mutation", paths=[PATH])


def test_append_target_pages_has_no_total_target_cap(graph):
    _, receipts = graph
    identity = begin(receipts)
    for page in range(3):
        targets = [{**probe(), "path": f"Assets/Page{page}/{index}.asset"} for index in range(1000)]
        receipts.add(identity, targets, {"epoch": "epoch", "revision": 3})
    assert receipts.read(identity)["targets"] == 3001
    assert receipts.release(identity)


def test_previous_import_of_same_path_cannot_certify_later_unimported_bytes(graph):
    workspace, receipts = graph
    identity = begin(receipts)
    receipts.finish(identity, "accepted", "tool_response")
    receipts.events(identity, [{"path": PATH, "file_stamp": "imported", "revision": 4}], "epoch", 4)
    workspace.clean_asset("worker", GUID, version=probe("unimported"))
    assert (
        receipts.check_targets(identity, [probe("unimported")], replacement_epoch=False)
        == "awaiting_import"
    )


def test_validation_cursor_survives_restart_but_resets_on_changed_revision(graph):
    workspace, receipts = graph
    identity = begin(receipts)
    native = {"epoch": "epoch", "revision": 3}
    revision = workspace.store.status()["revision"]
    receipts.validation(identity, native, revision, PATH)
    restored = MutationReceipts(GraphWorkspace(workspace.store))
    assert restored.validation(identity, native, revision) == PATH
    assert restored.validation(identity, {**native, "revision": 4}, revision) == ""


def test_legacy_upgrade_backfills_only_receipt_targets(graph):
    workspace, _ = graph
    other = "b" * 32
    workspace.record_page(
        "worker",
        [{"guid": other, "path": "Assets/Other.asset", "kind": "asset", "source_format": "none"}],
        {},
        "seen",
    )
    for guid in (GUID, other):
        workspace.clean_asset("worker", guid)
    with workspace.store._connect(write=True) as connection:
        connection.execute("DROP TABLE graph_collected_versions")
        connection.execute("UPDATE graph_workspace SET version=5")
    restored = GraphWorkspace(workspace.store)
    assert restored.dirty_assets() == []
    begin(MutationReceipts(restored))
    assert [asset["guid"] for asset in restored.dirty_assets()] == [GUID]


def test_native_receipt_and_compiler_metadata_use_read_permission():
    from services.operation_permissions import Permission, required_permissions

    for action in ("probe", "compilation", "begin_mutation", "await_mutation"):
        assert required_permissions("manage_reference_graph", {"action": action}, native=True) == {
            Permission.READ
        }


@pytest.mark.asyncio
async def test_guid_targets_are_native_verified_and_lost_response_stays_unknown(service_graph):
    from services.reference_graph.mutation_tracking import mark_uncertain_dispatch

    service, _, ctx, _ = service_graph
    result = await call(service, ctx, "begin_mutation", guids=[GUID])
    assert result["receipt"]["targets"] == 1

    async def recovered():
        mark_uncertain_dispatch()
        return {"success": True, "message": "State observed after reconnect"}

    response = await tracked_mutation(
        ctx, ctx.instance, "manage_asset", {"action": "modify", "path": PATH}, recovered
    )
    assert response["success"]
    assert response["data"]["reference_graph"]["outcome"] == "unknown"


@pytest.mark.asyncio
async def test_actual_reload_recovery_boundary_does_not_confirm_dispatch(
    service_graph, monkeypatch
):
    from services.tools import refresh_unity

    _, _, ctx, _ = service_graph
    calls = []

    async def send(*_, **__):
        calls.append("write")
        return {"success": False, "error": "connection closed"}

    async def verified():
        return {"success": True, "data": {"read_back": True}}

    monkeypatch.setattr(refresh_unity.unity_transport, "send_with_unity_instance", send)
    response = await refresh_unity.send_mutation(
        ctx,
        ctx.instance,
        "manage_asset",
        {"action": "modify", "path": PATH},
        verify_after_disconnect=verified,
    )
    assert calls == ["write"] and response["success"]
    assert response["data"]["read_back"]
    assert response["data"]["reference_graph"]["outcome"] == "unknown"


@pytest.mark.asyncio
async def test_release_is_not_blocked_by_another_requests_long_wait(service_graph):
    service, native, ctx, _ = service_graph
    identity = (await call(service, ctx, "begin_mutation", paths=[PATH]))["receipt"]["id"]
    await call(service, ctx, "finish_mutation", id=identity, outcome="unknown")
    native.edit(imported=False)
    task = asyncio.create_task(call(service, ctx, "await_mutation", id=identity, wait_seconds=3600))
    try:
        await asyncio.sleep(0.03)
        released = await asyncio.wait_for(
            call(service, ctx, "release_mutation", id=identity), timeout=watchdog_timeout()
        )
        assert released["released"]
        with pytest.raises(ValueError, match="released"):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_sqlite_prepare_failure_preserves_write(service_graph, monkeypatch):
    service, _, ctx, _ = service_graph
    writes = []

    async def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    async def invoke():
        writes.append(1)
        return {"success": True, "data": {"native": "kept"}}

    monkeypatch.setattr(service, "execute", unavailable)
    response = await tracked_mutation(
        ctx, ctx.instance, "manage_asset", {"action": "modify", "path": PATH}, invoke
    )
    assert writes == [1]
    assert response["success"] is True
    assert response["data"]["reference_graph"]["state"] == "unavailable"


@pytest.mark.asyncio
async def test_sqlite_finish_failure_preserves_native_response(service_graph, monkeypatch):
    _, _, ctx, entry = service_graph
    writes = []

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    async def invoke():
        writes.append(1)
        monkeypatch.setattr(entry.workspace.mutations(), "finish", unavailable)
        return {"success": True, "data": {"native": "kept"}}

    try:
        response = await tracked_mutation(
            ctx, ctx.instance, "manage_asset", {"action": "modify", "path": PATH}, invoke
        )
    finally:
        assert writes == [1]
    assert response["success"] is True
    assert response["data"]["native"] == "kept"
    assert response["data"]["reference_graph"]["state"] == "unconfirmed"


class DuplicateNative(Native):
    copied_guid = "b" * 32
    copied_path = "Assets/Receipt 1.asset"

    def __init__(self):
        super().__init__()
        self.copied = False

    def copy(self):
        self.copied = True
        self.revision += 1
        self.events.append(
            {
                "path": self.copied_path,
                "revision": self.revision,
                "deleted": False,
                "file_stamp": "copied",
            }
        )

    async def __call__(self, instance, params):
        if params["action"] == "copy_destination":
            return {"instance_id": instance, "project_id": "project", "path": self.copied_path}
        result = await super().__call__(instance, params)
        copied = {
            "guid": self.copied_guid,
            "path": self.copied_path,
            "kind": "asset",
            "source_format": "none",
            "present": True,
        }
        if params["action"] == "inventory" and self.copied:
            result["assets"].append(copied)
        elif params["action"] == "asset" and params["path"] == self.copied_path:
            result.update(copied)
        elif params["action"] == "dependencies":
            result["targets"] = ["c" * 32]
            if params["guid"] == self.copied_guid:
                result.update(copied, file_stamp="copied")
        elif params["action"] == "probe":
            result["targets"] = [
                {
                    **probe("copied", present=self.copied),
                    "path": path,
                    "guid": self.copied_guid if self.copied else "",
                }
                if path == self.copied_path
                else {**probe(), "path": path}
                for path in params["paths"]
            ]
        return result


@pytest.mark.asyncio
async def test_duplicate_without_destination_includes_created_asset(tmp_path, monkeypatch):
    from services.tools import manage_asset

    monkeypatch.setattr(config, "http_remote_hosted", False)
    native = DuplicateNative()
    service = ReferenceGraphService(tmp_path, send=native)
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    ctx = Context(service)
    writes = []

    async def send(*args, **kwargs):
        assert kwargs["retry_on_reload"] is False
        writes.append(1)
        native.copy()
        return {"success": True, "data": {"path": native.copied_path, "guid": native.copied_guid}}

    monkeypatch.setattr(manage_asset, "send_with_unity_instance", send)
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()
        response = await manage_asset._send_asset(
            ctx, ctx.instance, {"action": "duplicate", "path": PATH}
        )
        assert writes == [1]
        receipt_id = response["data"]["reference_graph"]["id"]
        assert await entry.engine.reconcile()
        result = await call(service, ctx, "await_mutation", id=receipt_id)
        assert result["receipt"]["state"] == "synchronized"
        delta = await call(service, ctx, "mutation_diff", id=receipt_id)
        assert any(
            change["after"] and change["after"]["owner"] == "asset:" + native.copied_guid
            for change in delta["changes"]
        ), delta
    finally:
        await service.aclose()


def test_create_folder_is_a_tracked_asset_mutation():
    assert target_paths(
        "manage_asset", {"action": "create_folder", "path": "Assets/NewFolder"}
    ) == ["Assets/NewFolder"]


@pytest.mark.asyncio
async def test_actual_structured_edit_action_creates_receipt(service_graph, monkeypatch):
    from services.tools import refresh_unity

    _, _, ctx, _ = service_graph
    writes = []

    async def send(*args, **kwargs):
        writes.append(1)
        return {"success": True}

    monkeypatch.setattr(refresh_unity.unity_transport, "send_with_unity_instance", send)
    response = await refresh_unity.send_mutation(
        ctx,
        ctx.instance,
        "manage_script",
        {"action": "edit", "path": "Assets", "name": "Receipt", "edits": []},
    )
    assert writes == [1]
    assert response.get("data", {}).get("reference_graph"), response


@pytest.mark.asyncio
async def test_changeset_lookup_failure_reports_missing_tracking(service_graph, monkeypatch):
    from services.tools import script_change_set

    _, _, ctx, _ = service_graph
    calls = []

    async def send(sender, instance, command, params, **kwargs):
        calls.append(params["action"])
        if params["action"] == "get_change_set":
            return {"success": False, "message": "Temporary read unavailable"}
        return {"success": True, "data": {"phase": "applied"}}

    monkeypatch.setattr(script_change_set, "send_with_unity_instance", send)
    response = await script_change_set.script_change_set(ctx, "apply", change_set_id="a" * 32)
    assert calls == ["get_change_set", "apply_change_set"]
    assert response["success"] is True
    assert response["data"].get("reference_graph", {}).get("state") == "unavailable", response


@pytest.mark.parametrize(
    "path",
    [
        "Packages/com.example/C:/Windows/win.ini",
        "Packages/com.example/C:Windows/win.ini",
        "Packages/com.example/file.asset:stream",
        "Assets/file.asset:stream",
        "Packages/com.example/../outside",
        "Packages/com.example//outside",
        "Packages/com.example/\\\\host\\share",
        "C:/Assets/file.asset",
        "//host/share/Assets/file.asset",
        "asset:" + GUID,
    ],
)
def test_mutation_paths_reject_filesystem_roots_and_alternate_streams(path):
    with pytest.raises(ValueError, match="relative asset paths"):
        MutationReceipts.paths([path])


def test_mutation_paths_preserve_project_relative_assets_and_registered_package_references():
    MutationReceipts.paths(["Assets/Receipt.asset", "Packages/com.example/Runtime/Receipt.asset"])


@pytest.mark.asyncio
async def test_mutation_probe_rejects_escape_before_native_collection():
    from services.reference_graph.mutation_service import probe as collect_probe

    class Entry:
        async def collect(self, *args, **kwargs):
            pytest.fail("Invalid path reached the native collector")

    with pytest.raises(ValueError, match="relative asset paths"):
        await collect_probe(Entry(), ["Packages/com.example/C:/Windows/win.ini"])
