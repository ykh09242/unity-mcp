"""Disk-backed exploration keeps exact revision and scope boundaries."""

import json

import pytest

from services.reference_graph.queries import GraphQueries
from services.reference_graph.store import (
    Edge,
    GraphScope,
    GraphStore,
    Observation,
    RevisionConflict,
)


@pytest.fixture
def graph(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    return store, GraphQueries(store)


def publish(store, owner, targets):
    generation = store.observe(
        owner, "code", str(store.status()["revision"]), path=f"Assets/{owner}.cs"
    )
    store.publish(
        owner,
        "code",
        generation,
        [
            Edge(target, "resources_load", target, 3, "possible", "call:Resources.Load")
            for target in targets
        ],
    )


def snapshot(queries, name="test", limit=2):
    cursor = None
    while True:
        result = queries.snapshot(name=name, limit=limit, cursor=cursor)
        cursor = result["next_cursor"]
        if not cursor:
            return result["snapshot"]["id"]


def test_impact_preserves_cycles_multiple_routes_and_replayable_pages(graph):
    store, queries = graph
    publish(store, "a", ["b", "c"])
    publish(store, "b", ["c"])
    publish(store, "c", ["a"])
    first = queries.impact("a", direction="outgoing", limit=1)
    cursor = first["next_cursor"]
    second = queries.impact("a", direction="outgoing", limit=1, cursor=cursor)
    assert queries.impact("a", direction="outgoing", limit=1, cursor=cursor) == second
    rows = first["edges"] + second["edges"]
    cursor = second["next_cursor"]
    while cursor:
        page = queries.impact("a", direction="outgoing", limit=1, cursor=cursor)
        rows.extend(page["edges"])
        cursor = page["next_cursor"]
    assert {(row["owner"], row["target"]) for row in rows} == {
        ("a", "b"),
        ("a", "c"),
        ("b", "c"),
        ("c", "a"),
    }
    assert len(rows) == 4
    assert queries.release(first["query_id"])


def test_large_fanout_uses_continuations_and_no_total_result_cap(graph):
    store, queries = graph
    publish(store, "root", [f"child{i}" for i in range(1200)])
    cursor, total, pages = None, 0, 0
    while True:
        result = queries.impact("root", direction="outgoing", limit=73, cursor=cursor)
        assert len(result["edges"]) <= 73
        total += len(result["edges"])
        pages += 1
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert total == 1200 and pages > 16


def test_cursor_is_bound_to_query_revision_and_graph_scope(graph, tmp_path):
    store, queries = graph
    publish(store, "root", ["a", "b"])
    cursor = queries.impact("root", direction="outgoing", limit=1)["next_cursor"]
    other = GraphQueries(GraphStore.open(tmp_path, GraphScope("other", "editor", "project")))
    with pytest.raises(ValueError):
        other.impact("root", direction="outgoing", cursor=cursor)
    with pytest.raises(ValueError):
        queries.impact("different", direction="outgoing", cursor=cursor)
    publish(store, "x", ["y"])
    with pytest.raises(RevisionConflict):
        queries.impact("root", direction="outgoing", limit=1, cursor=cursor)


def test_explain_preserves_evidence_location_and_authored_separation(graph):
    store, queries = graph
    publish(store, "owner", ["target"])
    store.put_declaration("manual", "owner", Edge("target", "declared"), expected_version=0)
    rows = store.query("owner")["edges"]
    for row in rows:
        result = queries.explain(row["id"])["edges"][0]
        assert result["provider"] == row["provider"]
        assert result["source_path"] == "Assets/owner.cs"
        assert result["confidence"] == row["confidence"]
    assert queries.explain("missing")["edges"] == []


def test_snapshot_diff_is_paged_stable_and_keeps_manual_ids(graph):
    store, queries = graph
    publish(store, "owner", ["one"])
    store.put_declaration("manual", "owner", Edge("before", "declared"), expected_version=0)
    before = snapshot(queries, "before")
    publish(store, "owner", ["two"])
    store.put_declaration("manual", "owner", Edge("after", "declared"), expected_version=1)
    after = snapshot(queries, "after")
    rows, cursor = [], None
    while True:
        page = queries.diff(before, after, limit=1, cursor=cursor)
        rows.extend(page["changes"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(row["change"] for row in rows) == ["added", "modified", "removed"]
    modified = next(row for row in rows if row["change"] == "modified")
    assert modified["before"]["provider"] == modified["after"]["provider"] == "manual"
    assert queries.delete_snapshot(before)
    with pytest.raises(ValueError):
        queries.diff(before, after)


def test_snapshot_continuation_survives_restart_but_not_source_changes(graph):
    store, queries = graph
    publish(store, "owner", ["one", "two", "three"])
    first = queries.snapshot(name="test", limit=1)
    queries = GraphQueries(GraphStore(store.path, store.scope))
    assert (
        queries.snapshot(name="test", limit=1, cursor=first["next_cursor"])["snapshot"]["id"]
        == first["snapshot"]["id"]
    )
    publish(store, "other", ["next"])
    with pytest.raises(RevisionConflict):
        queries.snapshot(name="test", limit=1, cursor=first["next_cursor"])


def test_historical_source_grant_versions_cannot_be_reused_after_reconfiguration(graph):
    store, queries = graph
    store.set_source(
        "external:fixture", {"root": "/synthetic", "include": ["*.json"]}, expected_version=0
    )
    generation = store.observe_batch(
        [Observation("file", "external:fixture", "one", path="data.json", source_version=1)]
    )[0]
    store.publish("file", "external:fixture", generation, [Edge("target", "external")])
    before = snapshot(queries)
    store.set_source("external:fixture", {}, expected_version=1, enabled=False)
    after = snapshot(queries)
    with pytest.raises(RevisionConflict):
        queries.diff(before, after)


def test_unchanged_diff_pages_bound_comparison_work_and_release_finished_traversal(graph):
    store, queries = graph
    publish(store, "root", [f"child{i}" for i in range(1200)])
    before = snapshot(queries, limit=1000)
    after = snapshot(queries, limit=1000)
    first = queries.diff(before, after, limit=10)
    assert first["changes"] == []
    assert first["compared"] == 10 and first["next_cursor"]
    following = queries.diff(before, after, limit=10, cursor=first["next_cursor"])
    assert following["compared"] == 10 and following["next_cursor"] != first["next_cursor"]
    result = queries.impact("absent")
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM graph_query_nodes WHERE job=?", (result["query_id"],)
            ).fetchone()[0]
            == 0
        )


def test_shortest_path_is_paged_and_distinguishes_unreachable_from_pending(graph):
    store, queries = graph
    publish(store, "a", ["long", "short"])
    publish(store, "long", ["longer"])
    publish(store, "longer", ["destination"])
    publish(store, "short", ["destination"])
    cursor, route = None, []
    while True:
        page = queries.path("a", "destination", direction="outgoing", limit=1, cursor=cursor)
        route.extend(page["edges"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert page["path_found"] is True
    assert [(edge["owner"], edge["target"]) for edge in reversed(route)] == [
        ("a", "short"),
        ("short", "destination"),
    ]
    assert queries.path("a", "a")["path_found"] is True
    assert queries.path("missing", "a")["path_found"] is False
    incoming = queries.path("destination", "a", limit=100)
    assert incoming["path_found"] and len(incoming["edges"]) == 2


@pytest.mark.parametrize("with_provenance", [False, True])
def test_stale_source_move_preserves_published_path_or_reports_unknown(graph, with_provenance):
    store, queries = graph
    old = {"build": {"revision": "old"}} if with_provenance else None
    generation = store.observe_batch(
        [Observation("owner", "code", "old", path="Assets/Old.cs", provenance=old)]
    )[0]
    store.publish("owner", "code", generation, [Edge("target", "load", line=42)])
    edge = store.query("owner")["edges"][0]
    store.observe_batch(
        [
            Observation(
                "owner",
                "code",
                "new",
                path="Assets/New.cs",
                provenance={"build": {"revision": "new"}},
            )
        ]
    )
    explained = queries.explain(edge["id"])["edges"][0]
    assert explained["stale"] and explained["line"] == 42
    assert explained["source_path"] == ("Assets/Old.cs" if with_provenance else "")
    assert explained["current_source_path"] == "Assets/New.cs"
    assert explained["source_path_origin"] == ("published" if with_provenance else "unavailable")


def test_query_schema_upgrade_preserves_existing_resumable_job(graph):
    store, queries = graph
    publish(store, "a", ["b", "c", "d"])
    page = queries.impact("a", direction="outgoing", limit=1)
    with store._connect(write=True) as db:
        db.execute("ALTER TABLE graph_queries DROP COLUMN abandoned")
        db.execute("UPDATE graph_queries_schema SET version=1")
    upgraded = GraphQueries(store)
    assert upgraded.impact("a", direction="outgoing", limit=1, cursor=page["next_cursor"])
    with store._connect() as db:
        assert db.execute("SELECT version FROM graph_queries_schema").fetchone()[0] == 2
        assert db.execute("SELECT abandoned FROM graph_queries").fetchone()[0] == 0


@pytest.mark.parametrize("direction", ["incoming", "outgoing"])
@pytest.mark.parametrize(
    ("column", "corrupt", "error"),
    [
        ("payload", "{", json.JSONDecodeError),
        ("payload", "[]", TypeError),
        ("provenance", "{", json.JSONDecodeError),
        ("provenance", "[]", AttributeError),
    ],
)
def test_path_rejects_corrupt_examined_evidence_outside_selected_route(
    graph, direction, column, corrupt, error
):
    # Given: the first examined relationship is outside the final direct route.
    store, queries = graph
    publish(store, "root", ["branch", "destination"])
    with store._connect(write=True) as db:
        rows = db.execute("SELECT id,target FROM edges ORDER BY id").fetchall()
        if direction == "incoming":
            db.execute("UPDATE edges SET owner=target,target='root'")
        if column == "payload":
            db.execute("UPDATE edges SET payload=? WHERE id=?", (corrupt, rows[0]["id"]))
        else:
            db.execute(
                "INSERT OR REPLACE INTO evidence_context VALUES (?,'code',1,'{}',?)",
                (rows[0]["target"] if direction == "incoming" else "root", corrupt),
            )
        target = rows[1]["target"]

    # When/Then: discovery must reject corruption, even though that edge is not returned.
    with pytest.raises(error):
        queries.path("root", target, direction=direction, limit=100)
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_queries").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM graph_query_nodes").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM graph_query_queue").fetchone()[0] == 0


def test_failed_path_continuation_preserves_cursor_and_scratch(graph):
    # Given: the next page encounters corrupt evidence after one committed page.
    store, queries = graph
    publish(store, "root", ["a", "b", "c"])
    with store._connect(write=True) as db:
        rows = db.execute("SELECT id FROM edges ORDER BY id").fetchall()
        db.execute("UPDATE edges SET payload='{' WHERE id=?", (rows[1]["id"],))
    first = queries.path("root", "missing", direction="outgoing", limit=1)
    with store._connect() as db:
        before = {
            table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
            for table in ("graph_queries", "graph_query_nodes", "graph_query_queue")
        }

    # When: a resumed query fails while decoding its second relationship.
    with pytest.raises(json.JSONDecodeError):
        queries.path("root", "missing", direction="outgoing", limit=1, cursor=first["next_cursor"])

    # Then: its committed cursor and traversal state remain unchanged.
    with store._connect() as db:
        after = {
            table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in before
        }
    assert after == before
