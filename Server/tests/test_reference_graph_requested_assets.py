"""Public outgoing requests may reorder only the scoped collector's existing dirty jobs."""

import asyncio

import pytest

from services.reference_graph.reconcile import Reconciler
from services.reference_graph.store import RevisionConflict
from services.registry import operation_invocation as invocation
from services.registry.operation_context import active_operation
from services.registry.operation_scheduler import OperationAdmission, OperationScope
from services.registry.operation_target import OperationTarget
from services.tools.manage_reference_graph import manage_reference_graph
from tests.test_reference_graph_service import REQUEST_MARKER, setup
from tests.test_reference_graph_reconcile import setup as reconcile_setup
from tests.test_reference_graph_workspace import row, workspace
from tests.timing import watchdog_timeout


@pytest.mark.asyncio
@pytest.mark.parametrize("guarded", [True, False], ids=["current-editor", "legacy"])
async def test_outgoing_queries_prioritize_early_and_late_dirty_materials(
    setup, monkeypatch, guarded
):
    service, sender, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    entry.workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
    guids = {n: f"{n:032x}" for n in range(1, 9)}
    nodes = {n: "asset:" + guid for n, guid in guids.items()}
    assets = [
        {
            "guid": guids[n],
            "path": "Assets/Private/Hidden.mat" if n == 7 else f"Assets/Material{n}.mat",
            "kind": "asset",
            "source_format": "unity_yaml",
        }
        for n in range(1, 8)
    ]
    scope = OperationScope("local", "requested-assets-fixture", "generation")
    inventory, first, second, finished = (asyncio.Event() for _ in range(4))
    finish_census, finish_first, finish_second = (asyncio.Event() for _ in range(3))
    requests = []
    source_guards = []

    async def target(*args, **kwargs):
        return OperationTarget(scope, lambda: True, True)

    async def native(instance, params):
        action = params["action"]
        if action == "inventory":
            inventory.set()
            await finish_census.wait()
        if action == "dependencies":
            assert REQUEST_MARKER.get() is None, "foreground query called the native scanner"
            requests.append((params["guid"], params.get("asset_guard")))
            if params["guid"] == guids[1]:
                first.set()
                await finish_first.wait()
            if params["guid"] == guids[2]:
                second.set()
                await finish_second.wait()
        async with invocation.operation_session(
            OperationAdmission(scope, "exclusive", lambda: True)
        ):
            result = await sender(instance, params)
            if action == "status" and guarded:
                result["asset_guard_version"] = 1
            elif action == "inventory":
                result["assets"] = assets
            elif action == "dependencies":
                result.update(
                    present=True,
                    fingerprint="fixture",
                    snapshot="deps",
                    targets=[guids[3]],
                    next_offset=None,
                )
            elif action == "source":
                source_guards.append((params["guid"], params.get("asset_guard")))
                result.update(source_format="binary", coverage="binary")
            if "asset_guard" in params:
                assert (
                    params["asset_guard"]["path"] == f"Assets/Material{int(params['guid'], 16)}.mat"
                )
                assert params["asset_guard"]["ignore_digest"]
                result["asset_guard_current"] = True
            return result

    reconcile = entry.engine.reconcile

    async def tracked_reconcile(**options):
        result = await reconcile(**options)
        if result:
            finished.set()
        return result

    monkeypatch.setattr(invocation, "capture_target", target)
    monkeypatch.setattr(entry.engine, "reconcile", tracked_reconcile)
    dirty = entry.workspace.dirty_assets
    monkeypatch.setattr(entry.workspace, "dirty_assets", lambda **kwargs: dirty(limit=3, **kwargs))
    service.send = native
    call = invocation.wrap_tool(
        manage_reference_graph, "manage_reference_graph", "manage_reference_graph"
    )

    async def query(number, direction="outgoing"):
        token = REQUEST_MARKER.set("foreground")
        try:
            response = await asyncio.wait_for(
                call(ctx, "query", {"node": nodes[number], "direction": direction}),
                watchdog_timeout(5),
            )
        finally:
            REQUEST_MARKER.reset(token)
        assert response["success"], response
        return response["data"]

    try:
        service.start(entry)
        await asyncio.wait_for(inventory.wait(), watchdog_timeout())
        for number in (7, 8, 6, 6):
            pending = await query(number)
            assert pending["edges"] == [] and pending["graph_status"]["hint"]
        assert not (await query(4, "incoming"))["edges"]
        assert requests == []
        finish_census.set()
        await asyncio.wait_for(first.wait(), watchdog_timeout())
        early = await query(6)
        assert early["graph_status"]["hint"]
        assert [e["target"] for e in early["edges"]] == ([nodes[3]] if guarded else [])
        for number in (5, 5, 4):
            assert not (await query(number))["edges"]
        finish_first.set()
        await asyncio.wait_for(second.wait(), watchdog_timeout())
        late = await query(5)
        assert late["graph_status"]["hint"]
        assert [e["target"] for e in late["edges"]] == ([nodes[3]] if guarded else [])
        incoming = await query(3, "incoming")
        assert incoming["graph_status"]["hint"] and not incoming["graph_status"]["current"]
        entry.wake.clear()
        finish_second.set()
        await asyncio.wait_for(finished.wait(), watchdog_timeout())
        expected = [6, 1, 5, 2, 4, 3] if guarded else [1, 2, 3, 4, 5, 6]
        assert [g for g, _ in requests] == [guids[n] for n in expected]
        assert [g for g, guard in requests if guard] == (
            [guids[n] for n in (6, 5, 4)] if guarded else []
        )
        assert source_guards == requests
        complete = await query(3, "incoming")
        assert "hint" not in complete["graph_status"]
        assert {edge["owner"] for edge in complete["edges"]} == {nodes[n] for n in range(1, 7)}
    finally:
        finish_census.set()
        finish_first.set()
        finish_second.set()
        await service.aclose()


def test_requested_selector_preserves_failure_backoff_admission_and_worker_lease(workspace):
    assets = [row(f"{n:032x}", f"Assets/{n}.mat") for n in range(1, 5)]
    guids = tuple(asset["guid"] for asset in assets)
    workspace.record_page("worker", assets, {}, "scan")
    workspace.clean_asset("worker", guids[0])
    workspace.fail_asset("worker", assets[1], OSError("fixture"))
    workspace.finish_admission("worker")
    # Redirty during backoff (e.g. a replayed catalog checkpoint).
    workspace.record_page("worker", [assets[1]], {}, "scan")
    assert workspace.requested_asset("worker", guids, None)["guid"] == guids[2]
    workspace.block_admission()
    assert workspace.requested_asset("worker", guids, None) is None
    workspace.set_policy(
        {"respect_gitignore": False, "exclude": ["Assets/3.mat"]}, expected_version=0
    )
    with pytest.raises(RevisionConflict, match="ownership"):
        workspace.requested_asset("worker", guids, None)
    assert workspace.acquire("new-worker")
    workspace.record_page("new-worker", assets, {}, "scan")
    workspace.finish_admission("new-worker")
    assert workspace.requested_asset("new-worker", guids[2:], None)["guid"] == guids[3]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [{}, {"asset_guard_current": False}])
async def test_unproven_promotions_fall_back_to_regular_jobs(reconcile_setup, reply):
    store, _, editor, engine = reconcile_setup
    nodes = [editor.add(n, f"Assets/{n}.mat") for n in (1, 2, 3)]
    order = []

    async def native(action, **params):
        if action == "dependencies":
            order.append((params["guid"], "asset_guard" in params))
            if "asset_guard" in params:
                return {**reply, "present": True, "targets": ["bad-unverified-edge"]}
        result = await editor(action, **params)
        if action == "status":
            result["asset_guard_version"] = 1
        return result

    engine._collector = native
    engine.request_asset(nodes[2])
    assert await engine.reconcile()
    assert order == [(nodes[2][6:], True), *((node[6:], False) for node in nodes)]
    assert not store.query(nodes[2])["edges"]
    assert store.status()["queue_idle"]


@pytest.mark.asyncio
async def test_cancelled_priority_read_resumes_from_inventory_without_stale_hints(reconcile_setup):
    _, workspace, editor, engine = reconcile_setup
    nodes = [editor.add(n, f"Assets/{n}.mat") for n in (1, 2, 3)]
    started = asyncio.Event()

    async def native(action, **params):
        if "asset_guard" in params:
            started.set()
            await asyncio.Event().wait()
        result = await editor(action, **params)
        if action == "status":
            result["asset_guard_version"] = 1
        return result

    engine._collector = native
    engine.request_asset(nodes[2])
    pending = asyncio.create_task(engine.reconcile())
    try:
        await asyncio.wait_for(started.wait(), watchdog_timeout())
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    assert pending.cancelled()
    assert workspace.progress()["initialized"] and len(workspace.dirty_assets()) == 3
    editor.calls.clear()
    resumed = Reconciler(workspace, native, engine.check)
    assert await resumed.reconcile()
    assert not any(action == "inventory" for action, _ in editor.calls)
    assert [params["guid"] for action, params in editor.calls if action == "dependencies"] == [
        node[6:] for node in nodes
    ]


@pytest.mark.asyncio
async def test_priority_hints_are_bounded_and_scoped(setup):
    service, _, ctx = setup
    try:
        first, _ = await service.resolve(ctx, "status")
        first.engine.request_asset("external:" + "0" * 32)
        first.engine.request_asset("asset:" + "x" * 4000)
        for n in range(Reconciler.MAX_REQUESTED_ASSETS + 20):
            first.engine.request_asset(f"asset:{n:032x}")
            first.engine.request_asset(f"asset:{n:032x}")
        assert list(first.engine._requested_guids) == [
            f"{n:032x}" for n in range(Reconciler.MAX_REQUESTED_ASSETS)
        ]
        ctx.instance = "another-editor"
        other, _ = await service.resolve(ctx, "status")
        assert not other.engine._requested_guids
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_action", ["dependencies", "source"])
async def test_queued_promoted_page_rechecks_policy_after_editor_admission(
    setup, monkeypatch, blocked_action
):
    service, sender, ctx = setup
    entry, _ = await service.resolve(ctx, "status")
    scope = OperationScope("local", "queued-policy-fixture", "generation")
    ready, allow_page, queued, finished = (asyncio.Event() for _ in range(4))
    first, requested = "1" * 32, "2" * 32
    assets = [row(first, "Assets/One.mat"), row(requested, "Assets/Two.mat")]
    assets[1]["source_format"] = "unity_yaml"
    native_reads = []

    async def target(*args, **kwargs):
        if allow_page.is_set() and active_operation() is None and REQUEST_MARKER.get() is None:
            queued.set()
        return OperationTarget(scope, lambda: True, True)

    async def native(instance, params):
        if "asset_guard" in params and params["action"] == blocked_action:
            queued.set()
        async with invocation.operation_session(
            OperationAdmission(scope, "exclusive", lambda: True)
        ):
            action = params["action"]
            if action == "status" and REQUEST_MARKER.get() == "set_policy":
                allow_page.set()
                await queued.wait()
            result = await sender(instance, params)
            if action == "status":
                result["asset_guard_version"] = 1
            if action == "inventory":
                result["assets"] = assets
            if action in ("source", "dependencies"):
                native_reads.append((params["guid"], action))
                result.update(
                    present=True,
                    fingerprint="f",
                    snapshot="deps",
                    targets=[],
                    next_offset=None,
                    source_format="binary",
                    coverage="binary",
                    asset_guard_current=True,
                )
            return result

    collect = entry.engine.collect
    reconcile = entry.engine.reconcile

    async def pause_page(action, **params):
        if "asset_guard" in params and action == blocked_action:
            ready.set()
            await allow_page.wait()
        return await collect(action, **params)

    async def tracked(**kwargs):
        try:
            return await reconcile(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(entry.engine, "collect", pause_page)
    monkeypatch.setattr(entry.engine, "reconcile", tracked)
    monkeypatch.setattr(invocation, "capture_target", target)
    service.send = native
    call = invocation.wrap_tool(
        manage_reference_graph, "manage_reference_graph", "manage_reference_graph"
    )
    try:
        result = await call(ctx, "query", {"node": "asset:" + requested})
        assert result["success"], result
        await asyncio.wait_for(ready.wait(), watchdog_timeout())
        token = REQUEST_MARKER.set("set_policy")
        try:
            changed = await asyncio.wait_for(
                call(
                    ctx,
                    "set_policy",
                    {"expected_version": 0, "config": {"exclude": ["Assets/Two.mat"]}},
                ),
                watchdog_timeout(),
            )
            assert changed["success"], changed
        finally:
            REQUEST_MARKER.reset(token)
        await asyncio.wait_for(finished.wait(), watchdog_timeout())
        reads_of_requested = [action for guid, action in native_reads if guid == requested]
        assert reads_of_requested == (["dependencies"] if blocked_action == "source" else [])
    finally:
        allow_page.set()
        queued.set()
        await service.aclose()


def test_effective_policy_contends_with_active_page_across_workspace_handles(workspace):
    from services.reference_graph.workspace import GraphWorkspace

    other = GraphWorkspace(workspace.store)
    with workspace.requested_page_guard():
        assert other.set_policy({"respect_gitignore": True}, expected_version=0) == 1
        with pytest.raises(RevisionConflict, match="policy is in use"):
            other.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=1)
        with pytest.raises(RevisionConflict, match="policy is in use"):
            other.rebuild()
        assert not other.policy()["exclude"]
    assert other.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=1) == 2
