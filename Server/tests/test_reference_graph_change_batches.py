"""Journal descriptions are bounded, ordered, and safe to replay."""

import pytest

from services.reference_graph.collection_protocol import (
    CollectionBudget,
    CollectionBudgetExceeded,
    CollectionLimits,
)
from services.reference_graph.reconcile import Reconciler
from services.reference_graph.store import RevisionConflict


class Workspace:
    def __init__(self):
        self.events = []

    def admission(self):
        return lambda path: not path.startswith("Assets/Private/")

    def state(self, key, fallback):
        return fallback

    def block_admission(self):
        self.events.append(("block",))

    def record_page(self, worker, assets, progress, source):
        self.events.append(("record", tuple(asset["path"] for asset in assets)))

    def remove_path(self, worker, path, *, exact=False):
        self.events.append(("remove", path))

    def finish_changes(self, worker, progress, page):
        pass


def setup_reconciler(changes, version=1, malformed=False):
    worker = Reconciler.__new__(Reconciler)
    worker.workspace = Workspace()
    worker.worker = "test"
    worker.progress = {
        "epoch": "epoch",
        "after": 0,
        "catalog_through": 999,
        "editor": {"asset_batch_version": version},
    }
    worker._budget = CollectionBudget(CollectionLimits())
    calls = []

    async def check():
        pass

    async def collect(action, **kwargs):
        calls.append((action, kwargs))
        if action == "changes":
            return {
                "epoch": "epoch",
                "revision": len(changes),
                "requires_census": False,
                "changes": changes,
                "next_after": len(changes),
                "has_more": False,
            }
        if action == "assets":
            assert len(kwargs["paths"]) <= 32
            result = [
                {
                    "path": path,
                    "asset": {"present": False}
                    if path == "Assets/Missing"
                    else {"present": True, "path": path},
                }
                for path in kwargs["paths"]
            ]
            if malformed:
                result.pop()
            return {"assets": result}
        if action == "asset":
            return {"present": True, "path": kwargs["path"]}
        raise AssertionError(action)

    async def phase(name, **kwargs):
        worker.progress.update(kwargs)

    async def remove(path, revision, through):
        worker.workspace.events.append(("remove", path))
        return None, False

    worker.check = check
    worker.collect = collect
    worker._phase = phase
    worker._remove_changed_path = remove
    return worker, calls


def change(index, path, deleted=False):
    return {"revision": index, "path": path, "deleted": deleted}


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [32, 256])
async def test_journal_descriptions_use_bounded_pages(count):
    changes = [change(i + 1, f"Assets/{i}.png") for i in range(count)]
    worker, calls = setup_reconciler(changes)
    await worker._changes()
    assert [len(params["paths"]) for action, params in calls if action == "assets"] == [32] * (
        count // 32
    )
    assert worker.progress["after"] == count
    assert worker._budget.items == count * 2
    assert worker.workspace.events == [("record", tuple(item["path"] for item in changes))]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, None, True])
async def test_mixed_changes_preserve_application_order_and_legacy_fallback(version):
    changes = [
        change(1, "Assets/A"),
        change(2, "Assets/Old", True),
        change(3, "Assets/Private/B"),
        change(4, "Assets/Private/B", True),
        change(5, "Assets/C"),
    ]
    worker, calls = setup_reconciler(changes, version)
    await worker._changes()
    assert worker.workspace.events == [
        ("block",),
        ("record", ("Assets/A",)),
        ("remove", "Assets/Old"),
        ("record", ("Assets/Private/B",)),
        ("remove", "Assets/Private/B"),
        ("record", ("Assets/C",)),
    ]
    # Each import group can commit before the next deletion starts consuming its budget.
    assert sum(action == "assets" for action, _ in calls) == (3 if type(version) is int else 0)
    assert sum(action == "asset" for action, _ in calls) == (0 if type(version) is int else 3)


@pytest.mark.asyncio
async def test_malformed_batch_does_not_fall_back_or_advance_journal():
    worker, calls = setup_reconciler([change(1, "Assets/Private/A")], malformed=True)
    with pytest.raises(RevisionConflict):
        await worker._changes()
    assert [action for action, _ in calls] == ["changes", "assets"]
    assert worker.progress["after"] == 0
    assert worker.workspace.events == [("block",)]
    resumed, _ = setup_reconciler([change(1, "Assets/Private/A")])
    resumed.progress = worker.progress
    await resumed._changes()
    assert resumed.progress["after"] == 1


@pytest.mark.asyncio
async def test_missing_description_flushes_prior_rows_before_removing_path():
    changes = [change(1, "Assets/A"), change(2, "Assets/Missing"), change(3, "Assets/B")]
    worker, _ = setup_reconciler(changes)
    await worker._changes()
    assert worker.workspace.events == [
        ("record", ("Assets/A",)),
        ("remove", "Assets/Missing"),
        ("record", ("Assets/B",)),
    ]


@pytest.mark.asyncio
async def test_description_items_still_obey_budget_before_checkpoint():
    worker, _ = setup_reconciler([change(1, "Assets/A")])
    worker._budget = CollectionBudget(CollectionLimits(max_items=1))
    with pytest.raises(CollectionBudgetExceeded):
        await worker._changes()
    assert worker.progress["after"] == 0
    assert worker.workspace.events == []
