"""Dirty/retry selection preserves queue semantics without scanning healthy assets."""

from contextlib import contextmanager
import asyncio
import json

import pytest

from services.reference_graph.restart import _attach_donor
from services.reference_graph.reconcile import Reconciler
from services.reference_graph.collection_protocol import (
    CollectionBudget,
    CollectionBudgetExceeded,
    CollectionLimits,
)
from services.reference_graph.store import GraphScope, GraphStore, RevisionConflict
from services.reference_graph.workspace import GraphWorkspace


@pytest.fixture
def workspace(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("dirty-queue", "editor", "project"))
    return GraphWorkspace(store)


def populate(workspace, rows, failures=()):
    """Install synthetic durable queue rows in the real workspace schema."""
    with workspace.store._connect(write=True) as connection:
        connection.executemany(
            "INSERT INTO graph_assets VALUES (?,?,?,?,?)",
            [
                (guid, "Assets/Fixture.prefab", json.dumps({"guid": guid}), "seen", dirty)
                for guid, dirty in rows
            ],
        )
        connection.executemany("INSERT INTO graph_asset_failures VALUES (?,1,?)", failures)


def queue_due(workspace, through=100):
    assert workspace.acquire("queue-test")
    after = None
    try:
        while page := workspace.queue_due_assets("queue-test", through, after=after):
            assert len(page) <= 256
            after = page[-1]
    finally:
        workspace.release("queue-test")


def test_retry_promotion_is_bounded_and_preserves_failure_backoff(workspace, monkeypatch):
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)
    guids = [f"{index:032x}" for index in range(300)]
    populate(workspace, [(guid, 0) for guid in guids], [(guid, 100) for guid in guids])
    assert workspace.acquire("worker")
    first = workspace.queue_due_assets("worker", 100)
    assert len(first) == 256
    assert [asset["guid"] for asset in workspace.dirty_assets(-1)] == guids[:256]
    # Cleaning and rescheduling between pages must not be undone by continuation.
    workspace.clean_asset("worker", guids[0])
    workspace.clean_asset("worker", guids[260])
    workspace.fail_asset("worker", {"guid": guids[1]}, OSError("synthetic failure"))
    workspace.fail_asset("worker", {"guid": guids[261]}, OSError("synthetic failure"))
    second = workspace.queue_due_assets("worker", 100, after=first[-1])
    assert len(second) == 42
    expected = [guid for guid in guids if guid not in {guids[0], guids[1], guids[260], guids[261]}]
    assert [asset["guid"] for asset in workspace.dirty_assets(-1)] == expected
    with workspace.store._connect() as connection:
        assert tuple(
            connection.execute(
                "SELECT attempts,retry_at FROM graph_asset_failures WHERE guid=?", (guids[1],)
            ).fetchone()
        ) == (2, 110)
    # The old worker cannot promote remaining rows after its durable lease expires.
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 221)
    with pytest.raises(RevisionConflict, match="ownership changed"):
        workspace.queue_due_assets("worker", 221)
    assert [asset["guid"] for asset in workspace.dirty_assets(-1)] == expected
    workspace.release("worker")
    queue_due(workspace, 221)
    assert [asset["guid"] for asset in workspace.dirty_assets(-1)] == [
        guid for guid in guids if guid not in {guids[0], guids[260]}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("during_drain", [False, True])
async def test_cancelled_retry_promotion_resumes_under_a_new_worker(
    workspace, monkeypatch, during_drain
):
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)
    guids = [f"{index:032x}" for index in range(300)]
    populate(workspace, [(guid, 0) for guid in guids], [(guid, 100) for guid in guids])
    checks = 0

    async def cancel_after_first_page():
        nonlocal checks
        checks += 1
        if checks == 2 and not during_drain:
            raise asyncio.CancelledError

    async def unused_collector(*args, **kwargs):
        raise AssertionError("Queue promotion must not call the native collector")

    engine = Reconciler(workspace, unused_collector, cancel_after_first_page)
    engine.progress = {"editor": {}, "completed_at": 1}
    actual = []

    async def interrupted_collect(asset, reused):
        if len(actual) == 10:
            raise asyncio.CancelledError
        actual.append(asset["guid"])
        workspace.clean_asset(engine.worker, asset["guid"])

    monkeypatch.setattr(engine, "_collect_dirty_asset", interrupted_collect)
    assert workspace.acquire(engine.worker)
    try:
        with pytest.raises(asyncio.CancelledError):
            await engine._assets(None)
        assert len(workspace.dirty_assets(-1)) == (290 if during_drain else 256)
        with workspace.store._connect() as connection:
            assert connection.execute("SELECT count(*) FROM graph_asset_failures").fetchone()[
                0
            ] == (290 if during_drain else 300)
    finally:
        workspace.release(engine.worker)

    async def allowed():
        pass

    replacement = Reconciler(
        workspace,
        unused_collector,
        allowed,
        limits=CollectionLimits(max_items=1) if during_drain else None,
    )
    replacement.progress = {"editor": {}, "completed_at": 1}

    async def collect(asset, reused):
        actual.append(asset["guid"])
        workspace.clean_asset(replacement.worker, asset["guid"])

    monkeypatch.setattr(replacement, "_collect_dirty_asset", collect)
    assert workspace.acquire(replacement.worker)
    try:
        await replacement._assets(None)
        assert actual == guids
        assert not workspace.dirty_assets()
        with workspace.store._connect() as connection:
            assert not connection.execute("SELECT 1 FROM graph_asset_failures").fetchone()
    finally:
        workspace.release(replacement.worker)


@pytest.mark.asyncio
async def test_retry_promotion_budget_resumes_without_repeating_the_backlog(workspace, monkeypatch):
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)
    guids = [f"{index:032x}" for index in range(600)]
    populate(workspace, [(guid, 0) for guid in guids], [(guid, 100) for guid in guids])

    async def allowed():
        pass

    async def unused_collector(*args, **kwargs):
        raise AssertionError("Queue promotion must not call the native collector")

    engine = Reconciler(
        workspace, unused_collector, allowed, limits=CollectionLimits(max_items=256)
    )
    engine.progress = {"editor": {}, "completed_at": 1}
    actual = []

    async def collect(asset, reused):
        actual.append(asset["guid"])
        workspace.clean_asset(engine.worker, asset["guid"])

    monkeypatch.setattr(engine, "_collect_dirty_asset", collect)
    for _ in range(3):
        engine._budget = CollectionBudget(engine.limits)
        assert workspace.acquire(engine.worker)
        try:
            await engine._assets(None)
            break
        except CollectionBudgetExceeded:
            pass
        finally:
            workspace.release(engine.worker)
    assert actual == guids
    assert workspace.state("retry_scan") is None


@pytest.mark.parametrize("limit", [0, 1, 2, 3, 100, -1, -7])
@pytest.mark.parametrize("after", ["", "b", "d", "z"])
def test_dirty_retry_page_matches_eligibility_order_and_cursor(
    workspace, monkeypatch, limit, after
):
    # Given dirty, due, duplicate, future-only, and orphan retry identities.
    populate(
        workspace,
        [("a", 0), ("b", 1), ("c", 0), ("d", 1), ("e", 0), ("f", 1), ("g", 0)],
        [("0-orphan", 99), ("a", 99), ("b", 100), ("c", 100), ("d", 101), ("e", 101)],
    )
    clock_calls = []

    def now():
        clock_calls.append(100)
        return 100

    monkeypatch.setattr("services.reference_graph.workspace.time.time", now)
    queue_due(workspace)
    eligible = [guid for guid in ("a", "b", "c", "d", "f") if guid > after]
    expected = eligible if limit < 0 else eligible[:limit]

    # When selecting a bounded page through the production workspace API.
    actual = workspace.dirty_assets(limit, after=after)

    # Then duplicates, future backoff, exact due boundary and cursor remain compatible.
    assert actual == [{"guid": guid} for guid in expected]
    assert clock_calls and set(clock_calls) == {100}


def test_orphan_retries_cannot_consume_page_before_existing_assets(workspace, monkeypatch):
    # Given more early orphan failures than the requested page size.
    populate(workspace, [("z", 0)], [(f"a-{index}", 0) for index in range(10)] + [("z", 0)])
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)
    queue_due(workspace)

    # When selecting one retry.
    actual = workspace.dirty_assets(1)

    # Then the existing asset is still selected.
    assert actual == [{"guid": "z"}]


def test_mixed_queue_pagination_has_no_duplicates_or_skipped_work(workspace, monkeypatch):
    # Given more eligible work than a default page, with overlapping streams.
    guids = [f"{index:032x}" for index in range(250)]
    populate(
        workspace,
        [(guid, index % 2) for index, guid in enumerate(guids)],
        [(guid, 100) for guid in guids],
    )
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)
    queue_due(workspace)

    # When consuming exclusive-cursor pages until exhaustion.
    actual = []
    after = ""
    while page := workspace.dirty_assets(after=after):
        assert len(page) <= 100
        actual.extend(item["guid"] for item in page)
        after = page[-1]["guid"]

    # Then all eligible identities appear exactly once in GUID order.
    assert actual == guids


@pytest.mark.asyncio
async def test_due_retry_collection_work_scales_linearly_with_backlog(workspace, monkeypatch):
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)
    original_connect = workspace.store._connect
    steps = 0

    def tick():
        nonlocal steps
        steps += 1
        return 0

    @contextmanager
    def counted_connect(*args, **kwargs):
        with original_connect(*args, **kwargs) as connection:
            connection.set_progress_handler(tick, 1)
            try:
                yield connection
            finally:
                connection.set_progress_handler(None, 0)

    async def allowed():
        pass

    async def unused_collector(*args, **kwargs):
        raise AssertionError("Queue selection must not call the native collector")

    engine = Reconciler(workspace, unused_collector, allowed)
    engine.progress = {"editor": {}, "completed_at": 1}
    assert workspace.acquire(engine.worker)
    actual = []

    async def collect(asset, reused):
        actual.append(asset["guid"])

    monkeypatch.setattr(engine, "_collect_dirty_asset", collect)
    dirty_assets = workspace.dirty_assets
    page_steps = []

    def measured_page(*args, **kwargs):
        before = steps
        result = dirty_assets(*args, **kwargs)
        page_steps.append(steps - before)
        return result

    monkeypatch.setattr(workspace, "dirty_assets", measured_page)
    counts, maximum_pages = [], []
    try:
        for start, stop in ((0, 1000), (1000, 10000)):
            guids = [f"{index:032x}" for index in range(start, stop)]
            populate(
                workspace,
                [(guid, index % 7 == 0) for index, guid in enumerate(guids, start)],
                [
                    (guid, 101 if index % 3 == 0 else 99 + index % 2)
                    for index, guid in enumerate(guids, start)
                ],
            )
            actual.clear()
            page_steps.clear()
            steps = 0
            with monkeypatch.context() as patch:
                patch.setattr(workspace.store, "_connect", counted_connect)
                await engine._assets(None)
            counts.append(steps)
            maximum_pages.append(max(page_steps))
            assert actual == [
                f"{index:032x}" for index in range(stop) if index % 3 != 0 or index % 7 == 0
            ]
    finally:
        workspace.release(engine.worker)
    print(f"mixed due retry collection VM steps: {counts}; maximum page steps: {maximum_pages}")
    assert counts[1] <= counts[0] * 15, counts
    assert maximum_pages[1] <= maximum_pages[0] * 2 + 100, maximum_pages


@pytest.mark.parametrize("future_failures", [False, True])
def test_idle_dirty_queue_work_does_not_scale_with_unrelated_rows(
    workspace, monkeypatch, future_failures
):
    # Given healthy assets or exclusively future retries in a real workspace.
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)
    original_connect = workspace.store._connect
    steps = 0

    def tick():
        nonlocal steps
        steps += 1
        return 0

    @contextmanager
    def counted_connect(*args, **kwargs):
        with original_connect(*args, **kwargs) as connection:
            connection.set_progress_handler(tick, 1)
            try:
                yield connection
            finally:
                connection.set_progress_handler(None, 0)

    counts = []
    for start, stop in ((0, 1000), (1000, 10000)):
        rows = [(f"{index:032x}", 0) for index in range(start, stop)]
        failures = [(guid, 101) for guid, _ in rows] if future_failures else []
        populate(workspace, rows, failures)
        steps = 0
        with monkeypatch.context() as patch:
            patch.setattr(workspace.store, "_connect", counted_connect)
            # When performing the actual idle workspace queue query.
            queue_due(workspace)
            assert workspace.dirty_assets() == []
        counts.append(steps)

    # Then a tenfold unrelated population does not cause proportional VM work.
    print(f"dirty queue VM steps future_failures={future_failures}: {counts}")
    assert counts[1] <= counts[0] * 2 + 100, counts


def test_workspace_upgrade_adds_retry_index_without_changing_work(workspace, monkeypatch):
    # Given an existing version-nine queue, as persisted before this optimization.
    populate(workspace, [("a", 0), ("b", 1)], [("a", 100), ("b", 101)])
    with workspace.store._connect(write=True) as connection:
        connection.execute("DROP INDEX IF EXISTS graph_asset_failures_retry")
        connection.execute("UPDATE graph_workspace SET version=9")
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)

    # When reopening and then reopening again to exercise migration idempotency.
    upgraded = GraphWorkspace(workspace.store)
    reopened = GraphWorkspace(workspace.store)
    queue_due(upgraded)

    # Then queue payloads survive and the retry-time range has its covering index.
    assert upgraded.dirty_assets() == reopened.dirty_assets() == [{"guid": "a"}, {"guid": "b"}]
    with workspace.store._connect() as connection:
        assert [
            row[2] for row in connection.execute("PRAGMA index_info(graph_asset_failures_retry)")
        ] == ["retry_at", "guid"]
        assert connection.execute("SELECT version FROM graph_workspace").fetchone()[0] == 12


@pytest.mark.parametrize("version", [6, 7, 8, 9, 10, 11, 12, 13])
def test_restart_accepts_supported_index_migration_and_rejects_future_schema(workspace, version):
    # Given an owned donor with the durable projection required for restart reuse.
    with workspace.store._connect(write=True) as connection:
        connection.execute("UPDATE graph_workspace SET version=?", (version,))

    # When validating the actual donor database against the restart projection.
    with workspace.store._connect() as connection:
        accepted = _attach_donor(connection, workspace.store.path)

    # Then additive index versions remain reusable, while unknown future versions fail closed.
    assert accepted is (version <= 12)
