"""Public graph requests must not wait on a collector queued behind them."""

import asyncio
import contextvars
import json

import pytest

from services.registry import operation_invocation as invocation
from services.registry.operation_context import active_operation
from services.registry.operation_scheduler import OperationAdmission, OperationScope
from services.registry.operation_target import OperationTarget
from services.reference_graph.ignore import GitIgnoreRules
from services.reference_graph import response_budget
from services.reference_graph.reconcile import Reconciler
from services.reference_graph.service import ReferenceGraphService
from services.reference_graph.store import Edge
from services.tools.manage_reference_graph import manage_reference_graph
from tests.test_reference_graph_mutations import PATH, call as mutation_call, service_graph
from tests.test_reference_graph_service import Context, setup
from tests.timing import watchdog_timeout


def test_comments_and_reinclusions_do_not_count_as_exclusions():
    assert not GitIgnoreRules([(".gitignore", "# comment\n!Assets/Public/\n")]).has_exclusions()
    assert GitIgnoreRules([(".gitignore", "Assets/Private/\n")]).has_exclusions()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "options", "pending_query"),
    [
        ("query", {"node": "owner"}, True),
        ("query", {"node": "owner", "consistency": "checked"}, False),
        ("snapshot", {"name": "unsafe"}, False),
    ],
)
async def test_public_request_yields_to_collector_when_admission_is_pending(
    setup, monkeypatch, action, options, pending_query
):
    service, sender, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    entry.workspace.store.put_declaration(
        "old", "owner", Edge("target", "manual"), expected_version=0
    )
    entry.workspace.block_admission()
    scope = OperationScope("local", "graph-fixture", "generation")
    background_ready, allow_background, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def target(*args, **kwargs):
        return OperationTarget(scope, lambda: True, True)

    async def native(instance, params):
        if active_operation() is None and not background_ready.is_set():
            background_ready.set()
            await allow_background.wait()
            queued.set()
        async with invocation.operation_session(
            OperationAdmission(scope, "exclusive", lambda: True)
        ):
            if (
                active_operation() is not None
                and params["action"] == "status"
                and background_ready.is_set()
            ):
                allow_background.set()
                await queued.wait()
            return await sender(instance, params)

    monkeypatch.setattr(invocation, "capture_target", target)
    service.send = native
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    collector = asyncio.create_task(entry.engine.reconcile(), context=contextvars.Context())
    call = invocation.wrap_tool(
        manage_reference_graph, "manage_reference_graph", "manage_reference_graph"
    )
    try:
        await asyncio.wait_for(background_ready.wait(), watchdog_timeout())
        response = await asyncio.wait_for(call(ctx, action, options), watchdog_timeout(5))
        if pending_query:
            assert response["success"], response
            assert response["data"]["edges"] == []
            status = response["data"]["graph_status"]
            assert not status["current"] and status["admission_pending"]
            assert status["progress"] and status["hint"]
        else:
            assert not response["success"]
            assert response["error"] == "reference_graph_changed"
            assert "collection" in response["message"].lower()
        assert await asyncio.wait_for(collector, watchdog_timeout())
        finished = await asyncio.wait_for(call(ctx, "query", {"node": "owner"}), watchdog_timeout())
        assert finished["success"] and len(finished["data"]["edges"]) == 1
        assert finished["data"]["graph_status"]["current"]
        assert "hint" not in finished["data"]["graph_status"]
    finally:
        allow_background.set()
        collector.cancel()
        await asyncio.gather(collector, return_exceptions=True)
        await service.aclose()


@pytest.mark.asyncio
async def test_public_mutation_wait_yields_when_collection_is_pending(service_graph, monkeypatch):
    service, native, ctx, entry = service_graph
    receipt = (await mutation_call(service, ctx, "begin_mutation", paths=[PATH]))["receipt"]
    await mutation_call(service, ctx, "finish_mutation", id=receipt["id"], outcome="accepted")
    native.edit()
    scope = OperationScope("local", "mutation-fixture", "generation")

    async def target(*args, **kwargs):
        return OperationTarget(scope, lambda: True, True)

    monkeypatch.setattr(invocation, "capture_target", target)
    call = invocation.wrap_tool(
        manage_reference_graph, "manage_reference_graph", "manage_reference_graph"
    )
    response = await asyncio.wait_for(
        call(ctx, "await_mutation", {"id": receipt["id"], "wait_seconds": 300}),
        watchdog_timeout(5),
    )
    assert response["success"]
    assert response["data"]["pending_reason"] == "awaiting_collection"
    assert response["data"]["receipt"]["state"] == "pending"
    assert await entry.engine.reconcile()
    finished = await call(ctx, "await_mutation", {"id": receipt["id"]})
    assert finished["data"]["receipt"]["state"] == "synchronized"


@pytest.mark.asyncio
async def test_cold_cached_query_schedules_collection_and_keeps_input_validation(
    setup, monkeypatch
):
    service, sender, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    scope = OperationScope("local", "cold-fixture", "generation")
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    reconcile = entry.engine.reconcile

    async def target(*args, **kwargs):
        return OperationTarget(scope, lambda: True, True)

    async def native(instance, params):
        if active_operation() is None and not started.is_set():
            started.set()
            await release.wait()
        async with invocation.operation_session(
            OperationAdmission(scope, "exclusive", lambda: True)
        ):
            return await sender(instance, params)

    async def tracked_reconcile(**options):
        result = await reconcile(**options)
        finished.set()
        return result

    monkeypatch.setattr(invocation, "capture_target", target)
    monkeypatch.setattr(entry.engine, "reconcile", tracked_reconcile)
    service.send = native
    call = invocation.wrap_tool(
        manage_reference_graph, "manage_reference_graph", "manage_reference_graph"
    )
    try:
        for options in (
            {"node": "owner", "direction": "invalid"},
            {"node": "owner", "cursor": "invalid"},
        ):
            response = await asyncio.wait_for(call(ctx, "query", options), watchdog_timeout(5))
            assert not response["success"]
        response = await asyncio.wait_for(
            call(ctx, "query", {"node": "owner"}), watchdog_timeout(5)
        )
        assert response["success"] and response["data"]["edges"] == []
        assert response["data"]["graph_status"]["admission_pending"]
        assert response["data"]["graph_status"]["hint"]
        await asyncio.wait_for(started.wait(), watchdog_timeout())
        assert not finished.is_set()
        release.set()
        await asyncio.wait_for(finished.wait(), watchdog_timeout())
        response = await asyncio.wait_for(call(ctx, "query", {"node": "owner"}), watchdog_timeout())
        assert response["success"] and response["data"]["graph_status"]["current"]
        assert "admission_pending" not in response["data"]["graph_status"]
        assert "hint" not in response["data"]["graph_status"]
    finally:
        release.set()
        await service.aclose()


@pytest.mark.asyncio
async def test_expired_ignore_proof_returns_pending_without_foreground_reconcile(
    setup, monkeypatch
):
    service, sender, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    try:
        entry.workspace.store.put_declaration(
            "old", "owner", Edge("target", "manual"), expected_version=0
        )
        assert await entry.engine.reconcile()
        expired = True

        async def native(instance, params):
            result = await sender(instance, params)
            if params["action"] == "status":
                result["ignore_watchers"] = False
            if expired and params["action"] == "ignore_rules" and params.get("validate"):
                result["snapshot"] = "expired"
            return result

        service.send = native
        response = await asyncio.wait_for(
            manage_reference_graph(ctx, "query", {"node": "owner"}), watchdog_timeout(5)
        )
        assert response["success"] and response["data"]["edges"] == []
        assert response["data"]["graph_status"]["admission_pending"]
        assert response["data"]["graph_status"]["hint"]
        old_scope = response["data"]["scope"]
        ctx.instance = "another-editor"
        isolated = await manage_reference_graph(ctx, "query", {"node": "owner"})
        assert isolated["success"] and isolated["data"]["edges"] == []
        assert isolated["data"]["scope"] != old_scope
        ctx.instance = "one"
        expired = False
        assert await entry.engine.reconcile()
        response = await manage_reference_graph(ctx, "query", {"node": "owner"})
        assert response["success"] and len(response["data"]["edges"]) == 1
        assert "hint" not in response["data"]["graph_status"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_policy_handoff_hides_old_edges_until_exclusions_are_reconciled(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    guid = "1" * 32

    async def native(instance, params):
        result = await sender(instance, params)
        if params["action"] == "inventory":
            result["assets"] = [
                {
                    "guid": guid,
                    "path": "Assets/Private/One.asset",
                    "kind": "asset",
                    "source_format": "none",
                }
            ]
        elif params["action"] == "dependencies":
            result.update(
                present=True, fingerprint="fixture", snapshot="deps", targets=[], next_offset=None
            )
        return result

    service.send = native
    entry, _ = await service.resolve(ctx, "status")
    try:
        entry.workspace.store.put_declaration(
            "manual", "owner", Edge("asset:" + guid, "manual"), expected_version=0
        )
        assert await entry.engine.reconcile()
        before = await manage_reference_graph(ctx, "query", {"node": "owner"})
        assert len(before["data"]["edges"]) == 1
        await service.execute(
            ctx, "set_policy", {"expected_version": 0, "config": {"exclude": ["Assets/Private/**"]}}
        )
        pending = await manage_reference_graph(ctx, "query", {"node": "owner"})
        assert pending["success"] and pending["data"]["edges"] == []
        assert pending["data"]["graph_status"]["admission_pending"]
        assert pending["data"]["graph_status"]["hint"]
        assert await entry.engine.reconcile()
        after = await manage_reference_graph(ctx, "query", {"node": "owner"})
        assert after["success"] and after["data"]["edges"] == []
        assert "admission_pending" not in after["data"]["graph_status"]
        assert "hint" not in after["data"]["graph_status"]
        await service.execute(ctx, "set_policy", {"expected_version": 1, "config": {"exclude": []}})
        pending_target = await manage_reference_graph(ctx, "query", {"node": "asset:" + guid})
        assert pending_target["success"] and pending_target["data"]["edges"] == []
        assert pending_target["data"]["graph_status"]["admission_pending"]
        assert "excluded" not in pending_target["data"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_first_collection_serves_only_verified_partial_static_edges(setup, monkeypatch):
    service, sender, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    guid1, guid2, excluded = (str(number) * 32 for number in (1, 2, 3))
    entry.workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    entry.workspace.store.put_declaration(
        "manual", "asset:" + guid1, Edge("asset:" + guid2, "manual"), expected_version=0
    )
    scope = OperationScope("local", "partial-fixture", "generation")
    paused, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    reconcile = entry.engine.reconcile
    editor_revision = 0

    async def target(*args, **kwargs):
        return OperationTarget(scope, lambda: True, True)

    async def native(instance, params):
        if params["action"] == "dependencies" and params["guid"] == guid2:
            paused.set()
            await release.wait()
        async with invocation.operation_session(
            OperationAdmission(scope, "exclusive", lambda: True)
        ):
            result = await sender(instance, params)
            if params["action"] == "status":
                result["revision"] = editor_revision
            elif params["action"] == "inventory":
                result["assets"] = [
                    {
                        "guid": guid1,
                        "path": "Assets/One.asset",
                        "kind": "asset",
                        "source_format": "none",
                    },
                    {
                        "guid": guid2,
                        "path": "Assets/Two.asset",
                        "kind": "asset",
                        "source_format": "none",
                    },
                    {
                        "guid": excluded,
                        "path": "Assets/Private/Hidden.asset",
                        "kind": "asset",
                        "source_format": "none",
                    },
                ]
            elif params["action"] == "dependencies":
                result.update(
                    present=True,
                    fingerprint="fixture",
                    snapshot="deps",
                    targets=[guid2, excluded] if params["guid"] == guid1 else [],
                    next_offset=None,
                )
            return result

    async def tracked_reconcile(**options):
        result = await reconcile(**options)
        finished.set()
        return result

    monkeypatch.setattr(invocation, "capture_target", target)
    monkeypatch.setattr(entry.engine, "reconcile", tracked_reconcile)
    service.send = native
    call = invocation.wrap_tool(
        manage_reference_graph, "manage_reference_graph", "manage_reference_graph"
    )
    try:
        await call(ctx, "query", {"node": "asset:" + guid1})
        await asyncio.wait_for(paused.wait(), watchdog_timeout())
        page = await asyncio.wait_for(
            call(ctx, "query", {"node": "asset:" + guid1}), watchdog_timeout(5)
        )
        assert page["success"], page
        assert page["data"]["graph_status"]["partial_static"]
        assert page["data"]["graph_status"]["hint"]
        assert [(edge["provider"], edge["target"]) for edge in page["data"]["edges"]] == [
            ("unity", "asset:" + guid2)
        ]
        editor_revision = 1
        stale = await call(ctx, "query", {"node": "asset:" + guid1})
        assert stale["success"] and stale["data"]["edges"] == []
        assert stale["data"]["graph_status"]["admission_pending"]
        assert "partial_static" not in stale["data"]["graph_status"]
        editor_revision = 0
        release.set()
        await asyncio.wait_for(finished.wait(), watchdog_timeout())
        page = await call(ctx, "query", {"node": "asset:" + guid1})
        assert page["success"]
        assert {edge["provider"] for edge in page["data"]["edges"]} == {"unity", "manual"}
        assert all(edge["target"] != "asset:" + excluded for edge in page["data"]["edges"])
        assert "hint" not in page["data"]["graph_status"]
    finally:
        release.set()
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("move_at_delivery", [False, True])
async def test_excluded_move_fences_cached_evidence_at_both_boundaries(
    setup, monkeypatch, move_at_delivery
):
    service, sender, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    entry.workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    guid1, guid2 = "1" * 32, "2" * 32
    editor = {"revision": 0, "target_path": "Assets/Visible/Two.asset"}

    async def native(instance, params):
        result = await sender(instance, params)
        result["revision"] = editor["revision"]
        if params["action"] == "inventory":
            result["assets"] = [
                {
                    "guid": guid1,
                    "path": "Assets/One.asset",
                    "kind": "asset",
                    "source_format": "none",
                },
                {
                    "guid": guid2,
                    "path": editor["target_path"],
                    "kind": "asset",
                    "source_format": "none",
                },
            ]
        elif params["action"] == "dependencies":
            result.update(
                present=True,
                fingerprint="fixture",
                snapshot="deps",
                targets=[guid2] if params["guid"] == guid1 else [],
                next_offset=None,
            )
        return result

    def move(*args, **kwargs):
        editor.update(revision=1, target_path="Assets/Private/Two.asset")

    service.send = native
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    try:
        assert await entry.engine.reconcile()
        old = await manage_reference_graph(ctx, "query", {"node": "asset:" + guid1})
        assert len(old["data"]["edges"]) == 1
        if move_at_delivery:
            monkeypatch.setattr(service, "start", move)
        else:
            move()
            # Legacy over-budget rows must not be read while admission is pending.
            with entry.workspace.store._connect(write=True) as db:
                db.execute(
                    "UPDATE edges SET payload=? WHERE owner=?",
                    (json.dumps({"evidence": "x" * 10000}), "asset:" + guid1),
                )
            monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", 8192)
        result = await manage_reference_graph(ctx, "query", {"node": "asset:" + guid1})
        if move_at_delivery:
            assert not result["success"] and result["error"] == "reference_graph_changed"
        else:
            assert result["success"] and result["data"]["edges"] == []
            assert result["data"]["graph_status"]["catalog_pending"]
            assert not result["data"]["graph_status"]["current"]
            assert result["data"]["graph_status"]["hint"]
            for action, options in (
                ("impact", {"node": "asset:" + guid1}),
                ("snapshot", {"name": "stale"}),
            ):
                refused = await manage_reference_graph(ctx, action, options)
                assert not refused["success"] and refused["error"] == "reference_graph_changed"
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_unrestricted_cached_graph_keeps_stale_edges_with_freshness_hint(setup, monkeypatch):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    entry, _ = await service.resolve(ctx, "status")
    entry.workspace.store.put_declaration(
        "manual", "owner", Edge("target", "manual"), expected_version=0
    )
    try:
        assert await entry.engine.reconcile()

        async def native(instance, params):
            result = await sender(instance, params)
            if params["action"] == "status":
                result["revision"] = 1
            return result

        service.send = native
        response = await manage_reference_graph(ctx, "query", {"node": "owner"})
        assert response["success"] and len(response["data"]["edges"]) == 1
        assert not response["data"]["graph_status"]["current"]
        assert "catalog_pending" not in response["data"]["graph_status"]
        assert response["data"]["graph_status"]["hint"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_first_snapshot_after_restart_wakes_stale_catalog(setup, monkeypatch):
    service, sender, ctx = setup
    editor_revision = 0

    async def native(instance, params):
        result = await sender(instance, params)
        result["revision"] = editor_revision
        if params["action"] == "changes":
            after = params["after"]
            result.update(
                requires_census=after < editor_revision,
                changes=[],
                next_after=editor_revision,
                has_more=False,
            )
        return result

    service.send = native
    monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
    entry, _ = await service.resolve(ctx, "status")
    entry.workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    assert await entry.engine.reconcile()
    root = service.root
    await service.aclose()

    editor_revision = 1
    finished = asyncio.Event()
    reconcile = Reconciler.reconcile

    async def tracked_reconcile(self, **options):
        result = await reconcile(self, **options)
        if result:
            finished.set()
        return result

    monkeypatch.setattr(Reconciler, "reconcile", tracked_reconcile)
    reopened = ReferenceGraphService(root, send=native)
    new_ctx = Context(reopened)
    try:
        pending = await manage_reference_graph(new_ctx, "snapshot", {"name": "first"})
        assert not pending["success"] and pending["error"] == "reference_graph_changed"
        await asyncio.wait_for(finished.wait(), watchdog_timeout())
        snapshot = await manage_reference_graph(new_ctx, "snapshot", {"name": "retried"})
        assert snapshot["success"] and snapshot["data"]["snapshot"]["state"] == "ready"
    finally:
        await reopened.aclose()
