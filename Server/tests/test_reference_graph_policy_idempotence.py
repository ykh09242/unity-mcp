"""Policy resubmission preserves collected graph state and CAS semantics."""

import pytest

from services.reference_graph.store import RevisionConflict
from tests.test_reference_graph_operations import setup  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["set_policy", "import_config"])
async def test_first_default_save_preserves_index_and_worker(setup, monkeypatch, action):
    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        workspace.store._budget.grow()
        assert await entry.engine.reconcile()
        assert workspace.acquire("collector")
        workspace.save_document("collector", "asset:synthetic", "yaml", "v1", {})
        fence = workspace.delivery_fence()
        starts = []
        monkeypatch.setattr(service, "start", lambda *args, **kwargs: starts.append(kwargs))
        options = {"expected_version": 0, "config": {}}
        if action == "import_config":
            options["kind"] = "policy"

        result = await service.execute(ctx, action, options)

        assert result["data"]["version"] == 1
        assert workspace.policy() == {"version": 1, "exclude": [], "respect_gitignore": True}
        assert workspace.delivery_fence()[:3] == fence[:3]
        assert workspace.renew("collector")
        assert "asset:synthetic" in workspace.document_manifest()
        assert not starts
        with pytest.raises(RevisionConflict):
            workspace.set_policy({}, expected_version=0)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_changed_policy_schedules_collection(setup, monkeypatch):
    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        workspace.store._budget.grow()
        assert await entry.engine.reconcile()
        assert workspace.acquire("collector")
        starts = []
        monkeypatch.setattr(service, "start", lambda *args, **kwargs: starts.append(kwargs))

        result = await service.execute(
            ctx, "set_policy", {"expected_version": 0, "config": {"exclude": ["Assets/Private/**"]}}
        )

        assert result["data"]["version"] == 1
        assert not workspace.renew("collector")
        assert len(starts) == 1 and starts[0]["wake"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["set_policy", "import_config"])
async def test_identical_policy_preserves_active_collection(setup, action):
    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        entry.watching = True
        workspace = entry.workspace
        workspace.store._budget.grow()
        workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0)
        assert await entry.engine.reconcile()
        assert workspace.acquire("collector")
        workspace.save_document("collector", "asset:synthetic", "yaml", "v1", {})
        revision = workspace.store.revision()
        options = {
            "expected_version": 1,
            "config": {"exclude": ["Assets/Private/**"], "respect_gitignore": True},
        }
        if action == "import_config":
            options["kind"] = "policy"

        result = await service.execute(ctx, action, options)

        assert result["data"]["version"] == 1
        assert workspace.store.revision() == revision
        assert workspace.renew("collector")
        assert "asset:synthetic" in workspace.document_manifest()
        with pytest.raises(RevisionConflict):
            workspace.set_policy(options["config"], expected_version=0)
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "submitted", "identical"),
    [
        ({"version": 4, "exclude": []}, {}, True),
        ({"version": 4, "exclude": ["a", "b"]}, {"exclude": ["b", "a"]}, False),
        ({"version": 4, "exclude": [], "respect_gitignore": False}, {}, False),
        ({"version": 4, "exclude": [], "extra": True}, {}, False),
    ],
)
async def test_policy_equality_respects_defaults_order_and_authored_fields(
    setup, stored, submitted, identical
):
    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        workspace.store._budget.grow()
        with workspace.store._connect(write=True) as db:
            workspace._set(db, "policy", stored)

        version = workspace.set_policy(submitted, expected_version=4)

        assert version == (4 if identical else 5)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_rebuild_still_resets_identical_policy(setup):
    service, ctx = setup
    try:
        entry, _ = await service.resolve(ctx, "status")
        workspace = entry.workspace
        workspace.store._budget.grow()
        workspace.set_policy({}, expected_version=0)
        assert workspace.acquire("collector")
        workspace.save_document("collector", "asset:synthetic", "yaml", "v1", {})

        workspace.rebuild()

        assert workspace.policy()["version"] == 2
        assert not workspace.renew("collector")
        assert not workspace.document_manifest()
    finally:
        await service.aclose()
