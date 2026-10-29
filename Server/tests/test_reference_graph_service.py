"""Real scoped SQLite with fake transport and opaque credential lifetimes."""

from tests.timing import await_signal, watchdog_timeout

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


@pytest.mark.asyncio
@pytest.mark.parametrize("consistency", ["cached", "checked"])
@pytest.mark.parametrize(
    "change",
    [
        {},
        {"revision": 1},
        {"epoch": "next-epoch"},
        {"compiling": True},
        {"importing": True},
        {"compilation_failed": True},
        {"compilation_context": {"revision": "changed"}},
        {"addressables_event_error": "observer unavailable"},
    ],
)
async def test_delivery_native_status_downgrades_freshness(setup, monkeypatch, consistency, change):
    service, sender, ctx = setup
    native_change = {}

    async def send(instance, params):
        result = await sender(instance, params)
        if params["action"] == "status":
            result.update(native_change)
        return result

    service.send = send
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    try:
        entry, _ = await service.resolve(ctx, "status")
        entry.workspace.set_policy({"respect_gitignore": False}, expected_version=0)
        assert await entry.engine.reconcile()
        # Scheduling occurs after initial freshness calculation and before the
        # delivery status read. Only the native transport changes its evidence.
        monkeypatch.setattr(service, "start", lambda *_, **__: native_change.update(change))
        response = await service.execute(
            ctx, "query", {"node": "asset:fixture", "consistency": consistency}
        )
        status = response["data"]["graph_status"]
        assert status["native_current"] is (not change)
        assert status["current"] is (not change)
        assert status["consistency"] == (
            "checked_snapshot" if consistency == "checked" else "cached"
        )
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_initially_stale_native_status_stays_stale_at_delivery(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    try:
        entry, _ = await service.resolve(ctx, "status")
        assert await entry.engine.reconcile()

        async def send(instance, params):
            result = await sender(instance, params)
            if params["action"] == "status":
                result["revision"] = 1
            return result

        service.send = send
        response = await service.execute(ctx, "query", {"node": "asset:fixture"})
        assert not response["data"]["graph_status"]["native_current"]
        assert not response["data"]["graph_status"]["current"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_build_context_change_cannot_claim_native_current_before_reconciliation(
    setup, monkeypatch
):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    build = {"state": "available", "revision": "first"}

    async def send(instance, params):
        result = await sender(instance, params)
        if params["action"] == "status":
            result["compilation_context"] = build.copy()
        return result

    service.send = send
    entry, _ = await service.resolve(ctx, "status")
    assert await entry.engine.reconcile()
    assert (await service.execute(ctx, "status", {}))["data"]["graph_status"]["native_current"]
    build["revision"] = "second"
    assert not (await service.execute(ctx, "status", {}))["data"]["graph_status"]["native_current"]
    assert await entry.engine.reconcile()
    assert (await service.execute(ctx, "status", {}))["data"]["graph_status"]["native_current"]
    await service.aclose()


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
async def test_cached_head_change_wakes_incremental_scan_without_reloading_unchanged_sources(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    root = tmp_path / "settings"
    entry, target = await external_fixture(service, sender, ctx, root, monkeypatch, files=3)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("1" * 40)
    source = next(
        item for item in entry.workspace.store.sources() if item["provider"] == "external:fixture"
    )
    entry.workspace.set_source(
        source["provider"],
        {**source["config"], "git_revision": True},
        expected_version=source["version"],
    )
    assert await entry.engine.reconcile()
    scheduled = {}
    monkeypatch.setattr(service, "start", lambda _, **options: scheduled.update(options))
    (root / ".git" / "HEAD").write_text("2" * 40)
    response = await service.execute(ctx, "query", {"node": target, "direction": "incoming"})
    assert response["data"]["graph_status"]["external_inputs_changed"]
    assert scheduled["wake"] and not scheduled["force"]
    monkeypatch.setattr(
        ExternalSource, "read", lambda *_: pytest.fail("HEAD-only query caused content reread")
    )
    assert await entry.engine.reconcile(force_external=scheduled["force"])
    assert (
        entry.workspace.store.query(target, direction="incoming")["edges"][0]["provenance"][
            "repository"
        ]["commit"]
        == "2" * 40
    )
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
            "ignore_state": "available",
            "ignore_revision": 0,
        }
        if params["action"] == "ignore_rules":
            result.update(snapshot="ignore", files=[], next_offset=None)
        elif params["action"] == "inventory":
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
        assert not await asyncio.wait_for(completed.get(), watchdog_timeout())
        task = entry.task
        assert task is not None and not task.done()
        assert entry.workspace.store.status()["errors"] == 1
        path.write_text("prefab\nOne", encoding="utf-8")
        entry.wake.set()
        assert await asyncio.wait_for(completed.get(), watchdog_timeout())
        assert entry.task is task and not task.done()
        assert entry.workspace.store.status()["queue_idle"]
    finally:
        await service.aclose()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    # The server operator explicitly approves this test's synthetic data tree.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", json.dumps([str(tmp_path)]))
    sender = Sender()
    service = ReferenceGraphService(tmp_path / "graphs", send=sender)
    return service, sender, Context(service)


@pytest.mark.asyncio
async def test_exploration_actions_through_tool_keep_scope_and_authored_evidence(
    setup, monkeypatch
):
    service, _, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    await service.execute(
        ctx,
        "declare",
        {
            "id": "manual",
            "owner": "source",
            "edge": {"target": "target", "kind": "declared"},
            "expected_version": 0,
        },
    )

    async def call(action, **options):
        response = await manage_reference_graph(ctx, action, options)
        assert response["success"], response
        return response["data"]

    impact = await call("impact", node="target")
    edge = impact["edges"][0]
    route = await call("path", node="target", target="source", limit=1)
    assert route["path_found"] and route["edges"][0]["id"] == edge["id"]
    explained = await call("explain", id=edge["id"])
    assert explained["edges"][0]["provider"] == "manual"
    before = (await call("snapshot", name="before"))["snapshot"]["id"]
    await service.execute(
        ctx,
        "declare",
        {
            "id": "manual",
            "owner": "source",
            "edge": {"target": "updated", "kind": "declared"},
            "expected_version": 1,
        },
    )
    after = (await call("snapshot", name="after"))["snapshot"]["id"]
    delta = await call("diff", before=before, after=after)
    assert delta["historical"] and not delta["graph_status"]["current"]
    assert delta["changes"][0]["change"] == "modified"
    assert (await call("release_query", id=impact["query_id"]))["released"]
    assert (await call("delete_snapshot", id=before))["deleted"]
    ctx.instance = "another"
    assert not (await manage_reference_graph(ctx, "diff", {"before": before, "after": after}))[
        "success"
    ]
    for action in (
        "impact",
        "path",
        "explain",
        "snapshot",
        "diff",
        "release_query",
        "delete_snapshot",
    ):
        assert required_permissions("manage_reference_graph", {"action": action}) == {
            Permission.READ
        }
    await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_snapshot_diff_rechecks_operator_grants_even_without_changed_edges(
    setup, tmp_path, monkeypatch, empty
):
    service, sender, ctx = setup
    entry, target = await external_fixture(
        service, sender, ctx, tmp_path / "settings", monkeypatch, files=0 if empty else 1
    )
    before = (await service.execute(ctx, "snapshot", {}))["data"]["snapshot"]["id"]
    after = (await service.execute(ctx, "snapshot", {}))["data"]["snapshot"]["id"]
    calls = 0

    def revoked(*_):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise PermissionError("operator revoked")

    monkeypatch.setattr(service, "_operator_grant", revoked)
    with pytest.raises(PermissionError, match="revoked"):
        await service.execute(ctx, "diff", {"before": before, "after": after})
    await service.aclose()


@pytest.mark.asyncio
async def test_historical_paths_are_reauthorized_without_requiring_old_content(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    root = tmp_path / "settings"
    entry, _ = await external_fixture(service, sender, ctx, root, monkeypatch)
    before = (await service.execute(ctx, "snapshot", {}))["data"]["snapshot"]["id"]
    path = root / "0.json"
    path.write_text('{"prefab":"Elsewhere"}', encoding="utf-8")
    assert await entry.engine.reconcile()
    after = (await service.execute(ctx, "snapshot", {}))["data"]["snapshot"]["id"]
    delta = (await service.execute(ctx, "diff", {"before": before, "after": after}))["data"]
    assert delta["historical"] and delta["changes"]
    monkeypatch.setattr(
        ExternalSource, "stamp", lambda *_: (_ for _ in ()).throw(PermissionError("file revoked"))
    )
    with pytest.raises(PermissionError, match="file revoked"):
        await service.execute(ctx, "diff", {"before": before, "after": after})
    await service.aclose()


@pytest.mark.asyncio
async def test_unchanged_comparison_candidates_and_empty_explain_reauthorize(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    await external_fixture(service, sender, ctx, tmp_path / "settings", monkeypatch)
    before = (await service.execute(ctx, "snapshot", {}))["data"]["snapshot"]["id"]
    after = (await service.execute(ctx, "snapshot", {}))["data"]["snapshot"]["id"]
    data = (await service.execute(ctx, "diff", {"before": before, "after": after}))["data"]
    assert not data["changes"] and "_history_access" not in data
    monkeypatch.setattr(
        ExternalSource, "stamp", lambda *_: (_ for _ in ()).throw(PermissionError("file revoked"))
    )
    with pytest.raises(PermissionError, match="file revoked"):
        await service.execute(ctx, "diff", {"before": before, "after": after})
    monkeypatch.setattr(
        service,
        "_operator_grant",
        lambda *_: (_ for _ in ()).throw(PermissionError("operator revoked")),
    )
    with pytest.raises(PermissionError, match="operator revoked"):
        await service.execute(ctx, "explain", {"id": "missing-edge"})
    await service.aclose()


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
    await await_signal(sender.complete, "sender.complete")
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


def test_local_external_roots_require_server_approval_before_adapter_probe(tmp_path, monkeypatch):
    # Given a local client, no server-approved roots and a caller-chosen host path.
    scope = GraphScope("local:owner", "editor", "project")
    monkeypatch.delenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", raising=False)
    monkeypatch.setattr(
        ExternalSource, "__init__", lambda *_: pytest.fail("Unapproved root reached adapter")
    )
    # When the client attempts to register that source, then deny before any probe.
    with pytest.raises(PermissionError, match="authorization"):
        ReferenceGraphService._grant(scope, {"root": str(tmp_path), "include": ["*.json"]})


def test_local_external_roots_accept_only_approved_descendants(tmp_path, monkeypatch):
    # Given an operator-approved local root and a separate remote principal grant.
    root = tmp_path / "approved"
    child = root / "data"
    child.mkdir(parents=True)
    scope = GraphScope("local:owner", "editor", "project")
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv(
        "UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", json.dumps({"alice": [str(tmp_path)]})
    )
    # When local access is checked, then approval covers descendants but no ancestor.
    assert ReferenceGraphService._grant(scope, {"root": str(child), "include": ["*.json"]})
    with pytest.raises(PermissionError):
        ReferenceGraphService._grant(scope, {"root": str(tmp_path), "include": ["*.json"]})
    # Local approval must not authorize any remote principal.
    with pytest.raises(PermissionError):
        ReferenceGraphService._grant(
            GraphScope("remote:bob", "editor", "project"),
            {"root": str(child), "include": ["*.json"]},
        )


@pytest.mark.parametrize("value", ['{"root":[]}', '"*"', '["relative"]', "null", "invalid"])
def test_invalid_local_root_approval_fails_closed(tmp_path, monkeypatch, value):
    # Given malformed or unusable server approval, no tool input may broaden it.
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", value)
    # When a local caller requests a root, then fail closed.
    with pytest.raises((PermissionError, ValueError)):
        ReferenceGraphService._grant(
            GraphScope("local:owner", "editor", "project"),
            {"root": str(tmp_path), "include": ["*.json"]},
        )


@pytest.mark.asyncio
async def test_local_tool_cannot_persist_source_without_server_approval(
    setup, tmp_path, monkeypatch
):
    # Given an authenticated local tool caller and no operator-approved roots.
    service, _, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    monkeypatch.delenv("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS")
    # When the caller selects a host root, then no source configuration is saved.
    with pytest.raises(PermissionError):
        await service.execute(
            ctx,
            "set_source",
            {
                "id": "host",
                "expected_version": 0,
                "config": {"root": str(tmp_path), "include": ["*.json"]},
            },
        )
    sources = (await service.execute(ctx, "sources", {}))["data"]["sources"]
    assert not any(source["provider"].startswith("external:") for source in sources)
    await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["editor", "user"])
async def test_process_root_approval_does_not_share_sources_between_scopes(
    setup, tmp_path, monkeypatch, replacement
):
    # Given one scope with a configured source under this server's approved root.
    service, sender, ctx = setup
    await external_fixture(service, sender, ctx, tmp_path / "settings", monkeypatch)
    # When the server resolves a different Editor and local principal, then its
    # source configuration and generated evidence remain private to the old scope.
    if replacement == "editor":
        sender.identity = "replacement"
    else:
        monkeypatch.setattr(
            "services.reference_graph.service.local_user", lambda: "local:another-user"
        )
    response = await service.execute(ctx, "sources", {})
    assert not any(
        source["provider"].startswith("external:") for source in response["data"]["sources"]
    )
    await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
async def test_cached_external_results_recheck_file_access_and_operator_revocation(
    setup, tmp_path, monkeypatch, remote
):
    service, _, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    monkeypatch.setattr(config, "http_remote_hosted", remote)
    lease = CredentialLease(Authority(), object(), "alice", LEGACY_GRANTS) if remote else None
    root = tmp_path / "settings"
    root.mkdir()
    path = root / "a.json"
    path.write_text('{"key":"asset"}')
    grant = {"root": str(root), "include": ["*.json"]}
    setting = (
        "UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS"
        if remote
        else "UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS"
    )
    approval = json.dumps({"alice": [str(root)]} if remote else [str(root)])
    monkeypatch.setenv(setting, approval)
    with bind_credential_lease(lease):
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        assert await entry.engine.reconcile()
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
        monkeypatch.delenv(setting)
        with pytest.raises(PermissionError):
            await service.execute(ctx, "query", {"node": "target", "direction": "incoming"})
        monkeypatch.setenv(setting, approval)
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
async def test_two_valid_credentials_share_worker_without_invalidating_active_request(
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
            release.wait(watchdog_timeout())
            return original(**kwargs)

        monkeypatch.setattr(entry.workspace.store, "declarations", held_read)
        pending = asyncio.create_task(service.execute(ctx, "declarations", {}))
    tasks = []
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        with bind_credential_lease(second):
            await service.execute(ctx, "status", {})
        replacement = next(iter(service.entries.values()))
        tasks.append(replacement.task)
        release.set()
        assert (await pending)["success"]
        assert replacement is entry
        assert service.entries[entry.workspace.store.scope.key] is entry
        await service.aclose()
        assert all(task.done() for task in tasks)
        assert authority.owners == 0
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_idle_watch_releases_owner_and_disconnected_scope_parks_then_resumes(
    setup, monkeypatch
):
    service, sender, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(service, "POLL_SECONDS", 0.001)
    authority = Authority()
    lease = CredentialLease(authority, object(), "alice", LEGACY_GRANTS)
    disconnected = False
    failures = 0

    async def send(instance, params):
        nonlocal failures
        if disconnected:
            failures += 1
            raise ConnectionError("Disconnected Editor")
        return await sender(instance, params)

    service.send = send
    try:
        with bind_credential_lease(lease):
            await service.execute(ctx, "sync", {})
        entry = next(iter(service.entries.values()))

        async def completed():
            while entry.workspace.progress().get("phase") != "idle":
                await asyncio.sleep(0.001)

        await asyncio.wait_for(completed(), watchdog_timeout())

        # A completed reconciliation must not retain a credential owner while polling.
        async def idle():
            while authority.owners:
                await asyncio.sleep(0)

        await asyncio.wait_for(idle(), watchdog_timeout())
        disconnected = True
        entry.wake.set()
        task = entry.task
        await asyncio.wait_for(asyncio.shield(task), watchdog_timeout())
        await asyncio.sleep(0)
        assert failures == service.PARK_FAILURES
        assert authority.owners == 0
        assert not service.entries
        disconnected = False
        with bind_credential_lease(lease):
            resumed, _ = await service.resolve(ctx, "status")
        assert resumed.workspace.store.path == entry.workspace.store.path
        assert resumed.engine.worker != entry.engine.worker
        assert resumed.workspace.progress()["initialized"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_retirement_drain_does_not_block_other_users(setup, monkeypatch):
    from services.reference_graph.reconcile import drained_thread

    service, sender, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    alice = CredentialLease(Authority(), object(), "alice", LEGACY_GRANTS)
    bob = CredentialLease(Authority(), object(), "bob", LEGACY_GRANTS)
    entered, release = threading.Event(), threading.Event()
    with bind_credential_lease(alice):
        old, _ = await service.resolve(ctx, "status")

    async def held(**_):
        await drained_thread(lambda: (entered.set(), release.wait(watchdog_timeout())))

    monkeypatch.setattr(old.engine, "reconcile", held)
    service.start(old)
    replacement = None
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        sender.identity = "restarted"
        with bind_credential_lease(alice):
            replacement = asyncio.create_task(service.resolve(ctx, "status"))
        await asyncio.sleep(0.02)
        with bind_credential_lease(bob):
            other, _ = await asyncio.wait_for(service.resolve(ctx, "status"), watchdog_timeout())
        assert other.workspace.store.scope.user_id == "remote:bob"
        assert not replacement.done()
    finally:
        release.set()
        if replacement:
            await replacement
        await service.aclose()


@pytest.mark.asyncio
async def test_request_cannot_borrow_other_valid_credential_after_revocation(setup, monkeypatch):
    service, _, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    first_authority, second_authority = Authority(), Authority()
    first = CredentialLease(first_authority, object(), "alice", LEGACY_GRANTS)
    second = CredentialLease(second_authority, object(), "alice", LEGACY_GRANTS)
    entered, release = threading.Event(), threading.Event()
    with bind_credential_lease(first):
        entry, _ = await service.resolve(ctx, "declarations")
        original = entry.workspace.store.declarations

        def held(**kwargs):
            entered.set()
            release.wait(watchdog_timeout())
            return original(**kwargs)

        monkeypatch.setattr(entry.workspace.store, "declarations", held)
        request = asyncio.create_task(service.execute(ctx, "declarations", {}))
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        with bind_credential_lease(second):
            same, _ = await service.resolve(ctx, "status")
        assert same is entry
        first_authority.revoked = True
        release.set()
        with pytest.raises(CredentialLeaseError):
            await request
        with bind_credential_lease(second):
            assert (await service.execute(ctx, "status", {}))["success"]
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_revoked_attempt_drains_before_live_credential_takes_over(setup, monkeypatch):
    service, _, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(service, "POLL_SECONDS", 0.001)
    first_authority, second_authority = Authority(), Authority()
    first = CredentialLease(first_authority, object(), "alice", LEGACY_GRANTS)
    second = CredentialLease(second_authority, object(), "alice", LEGACY_GRANTS)
    active, draining, release, resumed = (asyncio.Event() for _ in range(4))
    attempts = []
    with bind_credential_lease(first):
        entry, _ = await service.resolve(ctx, "status")
    worker = entry.engine.worker

    async def reconcile(**_):
        lease = current_credential_lease()
        attempts.append(asyncio.current_task())
        if lease is first:
            active.set()
            try:
                await asyncio.Event().wait()
            finally:
                draining.set()
                await release.wait()
        else:
            assert lease is second
            assert first_authority.owners == 0
            assert second_authority.owners == 1
            resumed.set()
        return True

    monkeypatch.setattr(entry.engine, "reconcile", reconcile)
    service.start(entry)
    try:
        await await_signal(active, "active")
        with bind_credential_lease(second):
            same, _ = await service.resolve(ctx, "status")
        assert same is entry and entry.engine.worker == worker
        first_authority.revoked = True
        attempts[0].cancel()
        await await_signal(draining, "draining")
        assert first_authority.owners == 1
        assert not resumed.is_set()
        release.set()
        await await_signal(resumed, "resumed")
        assert entry.engine.worker == worker
    finally:
        release.set()
        await service.aclose()
        assert first_authority.owners == second_authority.owners == 0


@pytest.mark.asyncio
async def test_idle_expiry_waits_for_active_large_attempt_and_preserves_durable_state(
    setup, monkeypatch
):
    service, _, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    started, release = asyncio.Event(), asyncio.Event()
    clock = service._now()
    monkeypatch.setattr(service, "_now", lambda: clock)

    async def held(**_):
        started.set()
        await release.wait()
        return True

    monkeypatch.setattr(entry.engine, "reconcile", held)
    service.start(entry)
    task = entry.task
    try:
        await await_signal(started, "started")
        clock += 100 * service.IDLE_SECONDS
        entry.wake.set()
        await asyncio.sleep(0)
        assert not task.done()
        assert service.entries[entry.workspace.store.scope.key] is entry
        release.set()
        await asyncio.wait_for(task, watchdog_timeout())
        await asyncio.sleep(0)
        assert not service.entries
        assert entry.workspace.store.path.is_file()
    finally:
        release.set()
        await service.aclose()
        assert not service._coordination


@pytest.mark.asyncio
async def test_database_open_for_one_scope_does_not_block_unrelated_scope(setup, monkeypatch):
    from services.reference_graph.store import GraphStore

    service, _, ctx = setup
    opened, release = threading.Event(), threading.Event()
    original = GraphStore.open

    def open_store(root, scope):
        if scope.instance_id.endswith("one"):
            opened.set()
            release.wait(watchdog_timeout())
        return original(root, scope)

    monkeypatch.setattr(GraphStore, "open", open_store)
    pending = asyncio.create_task(service.resolve(ctx, "status"))
    try:
        assert await asyncio.to_thread(opened.wait, watchdog_timeout())
        other, _ = await asyncio.wait_for(
            service.resolve(Context(service, "two"), "status"), watchdog_timeout()
        )
        assert other.instance == "two"
        assert not pending.done()
    finally:
        release.set()
        await pending
        await service.aclose()
        assert not service._coordination


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_database_open_without_installing_entry(setup, monkeypatch):
    from services.reference_graph.store import GraphStore

    service, _, ctx = setup
    entered, release = threading.Event(), threading.Event()
    original = GraphStore.open

    def held(root, scope):
        entered.set()
        release.wait(watchdog_timeout())
        return original(root, scope)

    monkeypatch.setattr(GraphStore, "open", held)
    opening = asyncio.create_task(service.resolve(ctx, "status"))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        closing = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await opening
        await asyncio.wait_for(closing, watchdog_timeout())
        assert not service.entries and not service.tasks and not service._coordination
    finally:
        release.set()
        await asyncio.gather(opening, *([closing] if closing else []), return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("foreground", [False, True])
async def test_shutdown_cancels_active_native_collection_once_and_releases_database(
    setup, foreground
):
    service, sender, ctx = setup
    entered = asyncio.Event()
    attempt = []

    async def send(instance, params):
        if params["action"] == "changes":
            attempt.append(asyncio.current_task())
            entered.set()
            await asyncio.Event().wait()
        return await sender(instance, params)

    service.send = send
    entry, _ = await service.resolve(ctx, "status")
    if foreground:
        pending = asyncio.create_task(
            service.execute(ctx, "query", {"node": "missing", "consistency": "checked"})
        )
    else:
        service.start(entry)
        pending = entry.task
    try:
        await await_signal(entered, "entered")
        await asyncio.wait_for(service.aclose(), watchdog_timeout())
        assert attempt[0].cancelling() == 1
        assert pending.done()
        assert not service.tasks and not service.entries
        assert entry.workspace.acquire("after-shutdown")
        entry.workspace.release("after-shutdown")
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_shutdown_cancels_status_lookup_before_entry_exists(setup):
    service, sender, ctx = setup
    entered = asyncio.Event()

    async def held(instance, params):
        entered.set()
        await asyncio.Event().wait()
        return await sender(instance, params)

    service.send = held
    request = asyncio.create_task(service.execute(ctx, "status", {}))
    try:
        await await_signal(entered, "entered")
        await asyncio.wait_for(service.aclose(), watchdog_timeout())
        assert request.cancelled()
        assert not service._resolving and not service._resolve_tasks and not service._routes
        assert not service.entries and not service.tasks
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown_first", [False, True])
async def test_actual_revocation_and_shutdown_drain_heartbeat_in_both_orders(
    setup, monkeypatch, shutdown_first
):
    from services.api_key_service import ApiKeyService, ValidationResult

    service, sender, ctx = setup
    monkeypatch.setattr(config, "http_remote_hosted", True)
    authority = ApiKeyService("https://synthetic.invalid/validate")

    async def validate(_):
        return ValidationResult(valid=True, user_id="alice")

    monkeypatch.setattr(authority, "_validate_external", validate)
    lease = (await authority.validate("synthetic-lifetime-key")).lease
    entered, release = threading.Event(), threading.Event()
    collecting = asyncio.Event()

    async def send(instance, params):
        if params["action"] == "changes":
            collecting.set()
            await asyncio.Event().wait()
        return await sender(instance, params)

    service.send = send
    with bind_credential_lease(lease):
        entry, _ = await service.resolve(ctx, "status")
    renew = entry.workspace.renew

    def held_renew(worker):
        entered.set()
        assert release.wait(watchdog_timeout()), "Synthetic heartbeat was not released"
        return renew(worker)

    monkeypatch.setattr(entry.workspace, "renew", held_renew)
    sleep = asyncio.sleep

    async def fast_sleep(delay, result=None):
        return await sleep(0.001 if delay == 15 else delay, result)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    service.start(entry)
    closing = None
    try:
        await await_signal(collecting, "collecting")
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        attempt = next(iter(authority._lease_records[lease.generation].owners.values()))
        if shutdown_first:
            closing = asyncio.create_task(service.aclose())
        else:
            await authority.invalidate_cache("synthetic-lifetime-key")

        async def cancelling():
            while not attempt.cancelling():
                await sleep(0)
            await sleep(0.01)

        await asyncio.wait_for(cancelling(), watchdog_timeout())
        if shutdown_first:
            await authority.invalidate_cache("synthetic-lifetime-key")
        else:
            closing = asyncio.create_task(service.aclose())
        await sleep(0.01)
        assert authority._lease_owners == 1
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, watchdog_timeout())
        assert authority._lease_owners == 0
        assert not service.tasks and not service.entries
        assert entry.workspace.acquire("after-overlap")
        entry.workspace.release("after-overlap")
    finally:
        release.set()
        if closing:
            await closing
        await service.aclose()
        await authority.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("delay_status", [False, True])
async def test_superseded_editor_cannot_install_after_newer_resolution(
    setup, monkeypatch, delay_status
):
    from services.reference_graph.store import GraphStore

    service, sender, ctx = setup
    entered, release = threading.Event(), threading.Event()
    original = GraphStore.open

    def held(root, scope):
        if not delay_status and scope.instance_id.startswith("original"):
            entered.set()
            release.wait(watchdog_timeout())
        return original(root, scope)

    async def delayed(instance, params):
        response = await sender(instance, params)
        if delay_status and response["instance_id"].startswith("original"):
            entered.set()
            from services.reference_graph.reconcile import drained_thread

            await drained_thread(release.wait, 5)
        return response

    monkeypatch.setattr(GraphStore, "open", held)
    service.send = delayed
    old = asyncio.create_task(service.resolve(ctx, "status"))
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        sender.identity = "new"
        current, _ = await asyncio.wait_for(service.resolve(ctx, "status"), watchdog_timeout())
        release.set()
        with pytest.raises(RevisionConflict, match="replacement Editor"):
            await old
        assert list(service.entries.values()) == [current]
        assert not service._routes and not service._coordination
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_shutdown_observation_keeps_blocked_writer_owned(setup, monkeypatch):
    service, _, ctx = setup
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_SHUTDOWN_TIMEOUT_SECONDS", "0.02")
    entry, _ = await service.resolve(ctx, "status")
    entered, release = threading.Event(), threading.Event()
    original = entry.workspace.set_progress

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(watchdog_timeout()), "Owned writer must be released by the fixture"
        return original(*args, **kwargs)

    monkeypatch.setattr(entry.workspace, "set_progress", held)
    service.start(entry)
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        closing = asyncio.create_task(service.aclose())
        done, _ = await asyncio.wait({closing}, timeout=watchdog_timeout())
        assert closing in done, "A blocked writer must not hold the shutdown observation"
        assert closing.result() is False
        assert service.shutdown_status["state"] == "draining"
        owner = service._close_task
        assert service.tasks and not owner.done()
        assert not entry.workspace.acquire("replacement"), (
            "Observation expiry cannot release a live writer lease"
        )
        assert await service.aclose() is False
        assert service._close_task is owner
        release.set()
        await asyncio.wait_for(asyncio.shield(owner), watchdog_timeout())
        assert await service.aclose() is True
        assert service.shutdown_status["state"] == "drained"
        assert not service.tasks and not service.entries
        assert entry.workspace.acquire("replacement")
        entry.workspace.release("replacement")
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_cancelled_shutdown_observers_share_database_open_drain(setup, monkeypatch):
    from services.reference_graph.store import GraphStore

    service, _, ctx = setup
    entered, release = threading.Event(), threading.Event()
    original = GraphStore.open

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(watchdog_timeout())
        return original(*args, **kwargs)

    monkeypatch.setattr(GraphStore, "open", held)
    opening = asyncio.create_task(service.resolve(ctx, "status"))
    observers = []
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        for _ in range(2):
            observer = asyncio.create_task(service.aclose())
            observers.append(observer)
            await asyncio.sleep(0)
            observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)
        assert service.closed
        owner = service._close_task
        assert not owner.done() and not owner.cancelling()
        assert service.shutdown_status["pending_resolutions"] == 1
        release.set()
        await asyncio.wait_for(asyncio.shield(owner), watchdog_timeout())
        assert await service.aclose() is True
        assert service._close_task is owner
        assert not service.entries and not service._coordination
    finally:
        release.set()
        await asyncio.gather(opening, *observers, return_exceptions=True)
        await service.aclose()


def test_runner_shutdown_keeps_physical_writer_lease_until_thread_finishes(setup, monkeypatch):
    service, _, ctx = setup
    monkeypatch.setenv("UNITY_MCP_REFERENCE_GRAPH_SHUTDOWN_TIMEOUT_SECONDS", "0.01")
    entered, release = threading.Event(), threading.Event()
    main_returned, lease_released = threading.Event(), threading.Event()
    captured, errors, premature = {}, [], []

    async def run():
        entry, _ = await service.resolve(ctx, "status")
        captured["entry"] = entry
        write, release_lease = entry.workspace.set_progress, entry.workspace.release

        def held_write(*args, **kwargs):
            entered.set()
            assert release.wait(watchdog_timeout()), "The owned fixture must release its writer"
            return write(*args, **kwargs)

        def tracked_release(owner):
            premature.append(not release.is_set())
            release_lease(owner)
            lease_released.set()

        entry.workspace.set_progress = held_write
        entry.workspace.release = tracked_release
        service.start(entry)
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        assert await service.aclose() is False
        main_returned.set()
        # Return normally: asyncio.run now cancels every pending Task, including
        # any to_thread wrapper. No fixture drain runs before this real teardown.

    def runner():
        try:
            asyncio.run(run())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    held = None
    try:
        assert main_returned.wait(watchdog_timeout())
        # Observe the real runner's cancellation pass while the physical writer
        # remains held; this adjustable watchdog is not a production deadline.
        lease_released.wait(watchdog_timeout(0.2))
        entry = captured["entry"]
        held = not entry.workspace.acquire("replacement")
        if not held:
            entry.workspace.release("replacement")
    finally:
        release.set()
        thread.join(watchdog_timeout())
    assert not thread.is_alive() and not errors, errors
    assert held, "Runner teardown must not admit another writer while the old thread lives"
    assert premature and not any(premature), "Lease release must follow physical completion"
    assert captured["entry"].workspace.acquire("after-runner")
    captured["entry"].workspace.release("after-runner")


@pytest.mark.parametrize("failed", [False, True])
def test_completed_shutdown_can_be_observed_after_its_event_loop_closed(setup, monkeypatch, failed):
    service, _, _ = setup

    async def close():
        if failed:
            raise RuntimeError("retained close failure")

    monkeypatch.setattr(service, "_close_owned", close)
    for _ in range(2):
        if failed:
            with pytest.raises(RuntimeError, match="retained close failure"):
                asyncio.run(service.aclose())
        else:
            assert asyncio.run(service.aclose()) is True
        assert service._close_task.done()
        assert service._close_task.get_loop().is_closed()
