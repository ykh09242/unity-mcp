"""Real scoped SQLite with fake transport and opaque credential lifetimes."""

import asyncio
from contextvars import ContextVar
from contextlib import contextmanager
import json
import threading
from types import SimpleNamespace

import pytest

from core.config import config
from services.credential_lease import (
    CredentialLease,
    CredentialLeaseError,
    bind_credential_lease,
    current_credential_lease,
)
from services.operation_permissions import (
    LEGACY_GRANTS,
    OperationGrants,
    OperationPermissionError,
    Permission,
    required_permissions,
)
from services.reference_graph.analysis import Analysis
from services.reference_graph.external import ExternalSource
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.store import Edge, GraphScope, RevisionConflict, _json
from services.tools.manage_reference_graph import manage_reference_graph

REQUEST_MARKER = ContextVar("graph_test_request_marker", default=None)


async def external_fixture(service, sender, ctx, root, monkeypatch, files=1):
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    guid = "1" * 32

    async def send(instance, params):
        result = await sender(instance, params)
        if params["action"] == "inventory":
            result["assets"] = [
                {
                    "guid": guid,
                    "path": "Assets/Resources/One.prefab",
                    "kind": "asset",
                    "source_format": "none",
                    "keys": [{"scheme": "resources", "value": "One"}],
                }
            ]
        elif params["action"] == "dependencies":
            result.update(
                present=True, fingerprint="fixture", snapshot="deps", targets=[], next_offset=None
            )
        return result

    service.send = send
    root.mkdir()
    for index in range(files):
        (root / f"{index}.json").write_text('{"prefab":"One"}', encoding="utf-8")
    entry, _ = await service.resolve(ctx, "status")
    entry.workspace.set_source(
        "external:fixture",
        {
            "root": str(root),
            "include": ["*.json"],
            "mappings": [{"field": "/prefab", "scheme": "resources"}],
        },
        expected_version=0,
    )
    assert await entry.engine.reconcile()
    return entry, "asset:" + guid


@pytest.mark.asyncio
async def test_empty_external_result_requires_explicit_checked_snapshot(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    root = tmp_path / "settings"
    entry, target = await external_fixture(service, sender, ctx, root, monkeypatch, files=0)
    path = root / "new.json"
    path.write_text('{"prefab":"One"}', encoding="utf-8")
    response = (await service.execute(ctx, "query", {"node": target, "direction": "incoming"}))[
        "data"
    ]
    assert response["edges"] == []
    assert not response["graph_status"]["current"]
    assert response["graph_status"]["native_current"]
    assert response["graph_status"]["providers"]["external:fixture"]["last_verified_at"]
    checked = (
        await service.execute(
            ctx,
            "query",
            {
                "node": target,
                "direction": "incoming",
                "consistency": "checked",
            },
        )
    )["data"]
    assert len(checked["edges"]) == 1
    assert checked["graph_status"]["current"]
    assert checked["graph_status"]["providers"]["external:fixture"]["current"]
    assert checked["graph_status"]["consistency"] == "checked_snapshot"
    path.write_text('{"prefab":"Two"}', encoding="utf-8")
    assert not (await service.execute(ctx, "status", {}))["data"]["graph_status"]["current"]
    await service.aclose()


@pytest.mark.asyncio
async def test_external_validation_batches_metadata_without_skipping_each_file(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    entry, target = await external_fixture(
        service, sender, ctx, tmp_path / "settings", monkeypatch, files=16
    )
    store = entry.workspace.store
    result = store.query(target, direction="incoming")
    counts = {"reads": 0, "stamps": 0, "grants": 0}
    connect, stamp, grant = store._connect, ExternalSource.stamp, service._grant

    @contextmanager
    def counted(**kwargs):
        counts["reads"] += 1
        with connect(**kwargs) as connection:
            yield connection

    def counted_stamp(*args):
        counts["stamps"] += 1
        return stamp(*args)

    def counted_grant(*args):
        counts["grants"] += 1
        return grant(*args)

    monkeypatch.setattr(store, "_connect", counted)
    monkeypatch.setattr(ExternalSource, "stamp", counted_stamp)
    monkeypatch.setattr(service, "_grant", counted_grant)
    assert not service._validate_external(entry.workspace, result)
    assert counts == {"reads": 2, "stamps": 16, "grants": 1}
    await service.aclose()


@pytest.mark.asyncio
async def test_checked_query_refuses_partial_scan_and_empty_source_revocation(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    root = tmp_path / "settings"
    entry, target = await external_fixture(service, sender, ctx, root, monkeypatch, files=0)
    options = {"node": target, "direction": "incoming", "consistency": "checked"}
    path = root / "bad.json"
    path.write_text('{"prefab":', encoding="utf-8")
    with pytest.raises(RevisionConflict, match="not synchronized"):
        await service.execute(ctx, "query", options)
    assert entry.workspace.provider_status()["external:fixture"]["state"] == "failed"
    path.unlink()

    def revoked(_):
        raise PermissionError("Operator revoked the external source")

    monkeypatch.setattr(entry.engine, "authorize_source", revoked)
    with pytest.raises(RevisionConflict, match="not synchronized"):
        await service.execute(ctx, "query", options)
    assert entry.workspace.provider_status()["external:fixture"]["state"] == "failed"
    await service.aclose()


@pytest.mark.asyncio
async def test_checked_empty_result_rechecks_every_provider_before_delivery(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    await external_fixture(service, sender, ctx, tmp_path / "settings", monkeypatch, files=0)
    validate = service._validate_external
    revoked = False

    def operator(*_):
        if revoked:
            raise PermissionError("revoked before checked delivery")

    def revoke_after_validation(*args):
        nonlocal revoked
        result = validate(*args)
        revoked = True
        return result

    monkeypatch.setattr(ReferenceGraphService, "_operator_grant", staticmethod(operator))
    monkeypatch.setattr(service, "_validate_external", revoke_after_validation)
    try:
        with pytest.raises(PermissionError, match="revoked before checked delivery"):
            await service.execute(
                ctx, "query", {"node": "missing", "direction": "incoming", "consistency": "checked"}
            )
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_batch_validation_checks_operator_revocation_after_last_file(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    entry, target = await external_fixture(
        service, sender, ctx, tmp_path / "settings", monkeypatch, files=3
    )
    stamp = ExternalSource.stamp
    revoked = False
    count = 0

    def operator(*_):
        if revoked:
            raise PermissionError("revoked during file validation")

    def revoke_after_stamp(*args):
        nonlocal revoked, count
        value = stamp(*args)
        count += 1
        revoked = count == 3
        return value

    monkeypatch.setattr(ReferenceGraphService, "_operator_grant", staticmethod(operator))
    monkeypatch.setattr(ExternalSource, "stamp", revoke_after_stamp)
    with pytest.raises(PermissionError, match="revoked during"):
        service._validate_external(
            entry.workspace, entry.workspace.store.query(target, direction="incoming")
        )
    await service.aclose()


class Context:
    def __init__(self, service, instance="one"):
        self.instance = instance
        self.fastmcp = SimpleNamespace(_reference_graph_service=service)

    async def get_state(self, name):
        return self.instance if name == "unity_instance" else None


class Authority:
    def __init__(self):
        self.revoked = False
        self.owners = 0

    def check_lease_now(self, _):
        if self.revoked:
            raise CredentialLeaseError()

    async def check_lease(self, lease):
        self.check_lease_now(lease)

    def acquire_lease_owner(self, lease):
        self.check_lease_now(lease)
        self.owners += 1
        return object()

    def release_lease_owner(self, *_):
        self.owners -= 1

    def lease_retired(self, _):
        return self.revoked


class Sender:
    def __init__(self):
        self.calls = []
        self.identity = "original"
        self.complete = asyncio.Event()

    async def __call__(self, instance, params):
        self.calls.append((params.copy(), REQUEST_MARKER.get(), current_credential_lease()))
        result = {
            "instance_id": self.identity + instance,
            "project_id": "project",
            "epoch": "epoch",
            "revision": 0,
        }
        if params["action"] == "inventory":
            result.update(snapshot="snapshot", assets=[], next_offset=None)
        elif params["action"] == "changes":
            result.update(requires_census=False, changes=[], next_after=0, has_more=False)
            self.complete.set()
        return result


@pytest.mark.asyncio
async def test_watcher_survives_malformed_csv_and_recovers_on_next_wake(setup, tmp_path):
    service, _, ctx = setup
    root = tmp_path / "settings"
    root.mkdir()
    path = root / "a.csv"
    path.write_text('prefab\n"unterminated', encoding="utf-8")
    entry, _ = await service.resolve(ctx, "status")
    entry.workspace.set_source(
        "external:settings", {"root": str(root), "include": ["*.csv"]}, expected_version=0
    )
    completed = asyncio.Queue()
    reconcile = entry.engine.reconcile

    async def watched(**options):
        result = await reconcile(**options)
        completed.put_nowait(result)
        return result

    entry.engine.reconcile = watched
    service.start(entry)
    try:
        assert not await asyncio.wait_for(completed.get(), 5)
        task = entry.task
        assert task is not None and not task.done()
        assert entry.workspace.store.status()["errors"] == 1
        path.write_text("prefab\nOne", encoding="utf-8")
        entry.wake.set()
        assert await asyncio.wait_for(completed.get(), 5)
        assert entry.task is task and not task.done()
        assert entry.workspace.store.status()["queue_idle"]
    finally:
        await service.aclose()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    sender = Sender()
    service = ReferenceGraphService(tmp_path / "graphs", send=sender)
    return service, sender, Context(service)


@pytest.mark.asyncio
async def test_requires_selected_instance_and_rejects_supplied_identity_before_io(setup):
    service, sender, ctx = setup
    ctx.instance = None
    with pytest.raises(ValueError, match="Select"):
        await service.execute(ctx, "status", {})
    ctx.instance = "one"
    result = await manage_reference_graph(ctx, "query", {"node": "asset:a", "user_id": "another"})
    assert not result["success"]
    assert sender.calls == []


@pytest.mark.asyncio
async def test_same_project_different_instances_and_authenticated_users_are_isolated(
    setup, monkeypatch
):
    service, _, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    monkeypatch.setattr(config, "http_remote_hosted", True)
    authority = Authority()
    alice = CredentialLease(authority, object(), "alice", LEGACY_GRANTS)
    bob = CredentialLease(authority, object(), "bob", LEGACY_GRANTS)
    with bind_credential_lease(alice):
        a = await service.execute(
            ctx,
            "declare",
            {
                "id": "manual",
                "owner": "source",
                "edge": {"target": "target", "kind": "declared"},
                "expected_version": 0,
            },
        )
        ctx.instance = "two"
        b = await service.execute(ctx, "declarations", {})
    ctx.instance = "one"
    with bind_credential_lease(bob):
        c = await service.execute(ctx, "declarations", {})
    assert len({result["data"]["scope"] for result in (a, b, c)}) == 3
    assert b["data"]["declarations"] == c["data"]["declarations"] == []
    with pytest.raises(CredentialLeaseError):
        await service.execute(ctx, "status", {})
    await service.aclose()


@pytest.mark.asyncio
async def test_detached_worker_drops_request_context_pins_lease_and_drains_on_shutdown(
    setup, monkeypatch
):
    service, sender, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    authority = Authority()
    lease = CredentialLease(authority, object(), "alice", LEGACY_GRANTS)
    token = REQUEST_MARKER.set("private request")
    try:
        with bind_credential_lease(lease):
            await service.execute(ctx, "sync", {})
    finally:
        REQUEST_MARKER.reset(token)
    await asyncio.wait_for(sender.complete.wait(), 5)
    assert authority.owners == 1
    background = [call for call in sender.calls if call[0]["action"] == "changes"]
    assert background[0][1] is None
    assert background[0][2] is lease
    await service.aclose()
    assert authority.owners == 0
    assert service.entries == {}


@pytest.mark.asyncio
async def test_native_response_cannot_switch_graph_identity(setup):
    service, sender, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    sender.identity = "replacement"
    with pytest.raises(RevisionConflict, match="changed"):
        await entry.collect("changes", epoch="epoch", after=0)
    await service.aclose()


@pytest.mark.asyncio
async def test_read_permission_cannot_mutate_authored_configuration(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    lease = CredentialLease(
        Authority(),
        object(),
        "read-only",
        OperationGrants(frozenset({Permission.READ}), scoped=True),
    )
    with bind_credential_lease(lease), pytest.raises(OperationPermissionError):
        await service.execute(ctx, "set_policy", {"config": {"exclude": []}, "expected_version": 0})
    assert not sender.calls
    assert required_permissions("manage_reference_graph", {"action": "set_source"}) == {
        Permission.READ,
        Permission.EDIT,
    }


def test_remote_external_roots_require_operator_grant_before_any_adapter_probe(
    tmp_path, monkeypatch
):
    scope = GraphScope("remote:alice", "editor", "project")
    root = tmp_path / "settings"
    root.mkdir()
    monkeypatch.delenv("UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", raising=False)
    with pytest.raises(PermissionError, match="operator"):
        ReferenceGraphService._grant(scope, {"root": str(root / "missing"), "include": ["*.json"]})
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", json.dumps({"bob": [str(root)]}))
    with pytest.raises(PermissionError):
        ReferenceGraphService._grant(scope, {"root": str(root), "include": ["*.json"]})
    monkeypatch.setenv(
        "UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", json.dumps({"alice": [str(root)]})
    )
    ReferenceGraphService._grant(scope, {"root": str(root), "include": ["*.json"]})
    with pytest.raises(PermissionError):
        ReferenceGraphService._grant(scope, {"root": str(root.parent), "include": ["*.json"]})


@pytest.mark.asyncio
async def test_cached_external_results_recheck_file_access_and_operator_revocation(
    setup, tmp_path, monkeypatch
):
    service, _, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    monkeypatch.setattr(config, "http_remote_hosted", True)
    lease = CredentialLease(Authority(), object(), "alice", LEGACY_GRANTS)
    root = tmp_path / "settings"
    root.mkdir()
    path = root / "a.json"
    path.write_text('{"key":"asset"}')
    grant = {"root": str(root), "include": ["*.json"]}
    monkeypatch.setenv(
        "UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", json.dumps({"alice": [str(root)]})
    )
    with bind_credential_lease(lease):
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        workspace.store.set_source("external:settings", grant, expected_version=0)
        workspace.acquire("test")
        generation = workspace.store.observe(
            "external:owner",
            "external:settings",
            "hash",
            path="a.json",
            kind="external",
            source_version=1,
        )
        workspace.publish(
            "test",
            "external:owner",
            "external:settings",
            generation,
            Analysis([Edge("target", "external")]),
        )
        workspace.record_external(
            "test",
            "external:settings",
            "external:owner",
            "a.json",
            "hash",
            _json(ExternalSource(grant).stamp("a.json")),
            "key",
            "scan",
        )
        workspace.release("test")
        workspace.acquire("test")
        workspace.set_progress(
            "test", {"initialized": True, "epoch": "epoch", "after": 0, "phase": "idle"}
        )
        workspace.release("test")
        response = await service.execute(ctx, "query", {"node": "target", "direction": "incoming"})
        assert len(response["data"]["edges"]) == 1
        assert response["data"]["graph_status"]["native_current"]
        assert not response["data"]["graph_status"]["current"]
        validate = service._validate_external
        validations = 0

        def change_before_final_validation(*args):
            nonlocal validations
            validations += 1
            if validations == 2:
                path.write_text('{"key":"changed"}')
            return validate(*args)

        monkeypatch.setattr(service, "_validate_external", change_before_final_validation)
        response = await service.execute(ctx, "query", {"node": "target", "direction": "incoming"})
        assert response["data"]["edges"][0]["stale"]
        assert not response["data"]["graph_status"]["current"]
        assert not response["data"]["graph_status"]["queue_idle"]
        monkeypatch.setattr(service, "_validate_external", validate)
        monkeypatch.delenv("UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS")
        with pytest.raises(PermissionError):
            await service.execute(ctx, "query", {"node": "target", "direction": "incoming"})
        monkeypatch.setenv(
            "UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", json.dumps({"alice": [str(root)]})
        )
        path.unlink()
        with pytest.raises(FileNotFoundError):
            await service.execute(ctx, "query", {"node": "target", "direction": "incoming"})
    await service.aclose()


@pytest.mark.asyncio
async def test_simultaneous_resolve_installs_one_scope_and_new_editor_gets_new_scope(setup):
    service, sender, ctx = setup
    entries = await asyncio.gather(*(service.resolve(ctx, "status") for _ in range(8)))
    assert len({id(entry) for entry, _ in entries}) == 1
    sender.identity = "restarted"
    restarted, _ = await service.resolve(ctx, "status")
    assert restarted.workspace.store.scope != entries[0][0].workspace.store.scope
    await service.aclose()


@pytest.mark.asyncio
async def test_changed_source_grant_cannot_return_prior_root_diagnostics(
    setup, tmp_path, monkeypatch
):
    service, _, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
        (root / "a.json").write_text('{"resource":"missing-value"}')
    source = {
        "root": str(roots[0]),
        "include": ["*.json"],
        "mappings": [{"field": "/resource", "scheme": "resources"}],
    }
    await service.execute(
        ctx, "set_source", {"id": "settings", "config": source, "expected_version": 0}
    )
    entry, _ = await service.resolve(ctx, "status")
    assert await entry.engine.reconcile()
    owner = next(iter(entry.workspace.external_inputs("external:settings").values()))["owner"]
    assert (await service.execute(ctx, "diagnostics", {"node": owner}))["data"]["issues"]
    source["root"] = str(roots[1])
    await service.execute(
        ctx, "set_source", {"id": "settings", "config": source, "expected_version": 1}
    )
    result = (await service.execute(ctx, "diagnostics", {"node": owner}))["data"]
    assert result["issues"] == result["coverage"] == []
    assert not entry.workspace.external_inputs("external:settings")
    assert await entry.engine.reconcile()
    assert entry.workspace.store.status()["queue_idle"]
    await service.aclose()


@pytest.mark.asyncio
async def test_replaced_credential_request_cannot_resurrect_an_untracked_watcher(
    setup, monkeypatch
):
    service, _, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    authority = Authority()
    first = CredentialLease(authority, object(), "alice", LEGACY_GRANTS)
    second = CredentialLease(authority, object(), "alice", LEGACY_GRANTS)
    entered, release = threading.Event(), threading.Event()
    with bind_credential_lease(first):
        entry, _ = await service.resolve(ctx, "declarations")
        original = entry.workspace.store.declarations

        def held_read(**kwargs):
            entered.set()
            release.wait(5)
            return original(**kwargs)

        monkeypatch.setattr(entry.workspace.store, "declarations", held_read)
        pending = asyncio.create_task(service.execute(ctx, "declarations", {}))
    tasks = []
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        with bind_credential_lease(second):
            await service.execute(ctx, "status", {})
        replacement = next(iter(service.entries.values()))
        tasks.append(replacement.task)
        release.set()
        with pytest.raises(RevisionConflict):
            await pending
        assert service.entries[entry.workspace.store.scope.key] is replacement
        assert entry.task is None
        await service.aclose()
        assert all(task.done() for task in tasks)
        assert authority.owners == 0
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        tasks.extend(item.task for item in service.entries.values() if item.task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.aclose()
