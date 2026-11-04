"""Dirty/retry selection preserves queue semantics without scanning healthy assets."""

from contextlib import contextmanager
import json

import pytest

from services.reference_graph.restart import _attach_donor
from services.reference_graph.store import GraphScope, GraphStore
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
    eligible = [guid for guid in ("a", "b", "c", "d", "f") if guid > after]
    expected = eligible if limit < 0 else eligible[:limit]

    # When selecting a bounded page through the production workspace API.
    actual = workspace.dirty_assets(limit, after=after)

    # Then duplicates, future backoff, exact due boundary and cursor remain compatible.
    assert actual == [{"guid": guid} for guid in expected]
    assert clock_calls == [100]


def test_orphan_retries_cannot_consume_page_before_existing_assets(workspace, monkeypatch):
    # Given more early orphan failures than the requested page size.
    populate(workspace, [("z", 0)], [(f"a-{index}", 0) for index in range(10)] + [("z", 0)])
    monkeypatch.setattr("services.reference_graph.workspace.time.time", lambda: 100)

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

    # When consuming exclusive-cursor pages until exhaustion.
    actual = []
    after = ""
    while page := workspace.dirty_assets(after=after):
        assert len(page) <= 100
        actual.extend(item["guid"] for item in page)
        after = page[-1]["guid"]

    # Then all eligible identities appear exactly once in GUID order.
    assert actual == guids


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

    # Then queue payloads survive and the retry-time range has its covering index.
    assert upgraded.dirty_assets() == reopened.dirty_assets() == [{"guid": "a"}, {"guid": "b"}]
    with workspace.store._connect() as connection:
        assert [
            row[2] for row in connection.execute("PRAGMA index_info(graph_asset_failures_retry)")
        ] == ["retry_at", "guid"]
        assert connection.execute("SELECT version FROM graph_workspace").fetchone()[0] == 10


@pytest.mark.parametrize("version", [6, 7, 8, 9, 10, 11])
def test_restart_accepts_supported_index_migration_and_rejects_future_schema(workspace, version):
    # Given an owned donor with the durable projection required for restart reuse.
    with workspace.store._connect(write=True) as connection:
        connection.execute("UPDATE graph_workspace SET version=?", (version,))

    # When validating the actual donor database against the restart projection.
    with workspace.store._connect() as connection:
        accepted = _attach_donor(connection, workspace.store.path)

    # Then additive index versions remain reusable, while unknown future versions fail closed.
    assert accepted is (version <= 10)
