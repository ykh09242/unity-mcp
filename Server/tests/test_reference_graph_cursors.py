"""Bounded continuation tokens preserve already persisted declaration identities."""

import base64
import json

import pytest

from services.reference_graph.queries import GraphQueries
from services.reference_graph.store import (
    Edge,
    GraphScope,
    GraphStore,
    RevisionConflict,
    _hash,
    _json,
)
from services.tools.manage_reference_graph import manage_reference_graph
from tests.test_reference_graph_queries import snapshot
from tests.test_reference_graph_service import setup as setup


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["a" * 700, "한글🎮" * 700], ids=["ascii", "unicode"])
async def test_public_long_declarations_have_bounded_query_cursors(setup, monkeypatch, identity):
    service, _, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    owner, target = "asset:" + "1" * 32, "asset:" + "2" * 32
    identities = [identity + suffix for suffix in ("2", "0", "1")]

    async def call(action, **options):
        response = await manage_reference_graph(ctx, action, options)
        assert response["success"], response
        return response["data"]

    try:
        for value in identities:
            await call(
                "declare",
                id=value,
                owner=owner,
                expected_version=0,
                edge={"target": target, "kind": "declared"},
            )
        listed = await call("declarations")
        assert {item["id"] for item in listed["declarations"]} == set(identities)
        for node, direction in ((owner, "outgoing"), (target, "incoming")):
            cursor, seen = None, []
            for _ in identities:
                page = await call("query", node=node, direction=direction, limit=1, cursor=cursor)
                seen.extend(edge["id"] for edge in page["edges"])
                cursor = page["next_cursor"]
                if cursor is not None:
                    assert len(cursor) <= 1024
            assert cursor is None
            assert seen == ["declared:" + value for value in sorted(identities)]
    finally:
        await service.aclose()


@pytest.mark.parametrize("identity", ["a" * 1500, "한글🎮" * 1500], ids=["ascii", "unicode"])
def test_persisted_long_declaration_query_cursors_keep_scope_filters_and_revision(
    tmp_path, identity
):
    scope = GraphScope("user", "editor", "project")
    store = GraphStore.open(tmp_path, scope)
    for suffix in ("2", "0", "1"):
        store.put_declaration(
            identity + suffix, "owner", Edge("target", "declared"), expected_version=0
        )
    store = GraphStore(store.path, scope)
    first = store.query("owner", limit=1)
    cursor = first["next_cursor"]
    assert len(cursor) <= 1024
    second = store.query("owner", limit=1, cursor=cursor)
    assert second["edges"][0]["id"] == "declared:" + identity + "1"
    assert store.query("owner", limit=1, cursor=cursor) == second
    other = GraphStore.open(tmp_path, GraphScope("other", "editor", "project"))
    for graph, node, direction in (
        (other, "owner", "outgoing"),
        (store, "other", "outgoing"),
        (store, "owner", "incoming"),
    ):
        with pytest.raises(ValueError, match="cursor"):
            graph.query(node, direction=direction, cursor=cursor)
    store.put_declaration("extra", "owner", Edge("target", "declared"), expected_version=0)
    with pytest.raises(RevisionConflict):
        store.query("owner", cursor=cursor)


@pytest.mark.parametrize("identity", ["a" * 1500, "한글🎮" * 1500], ids=["ascii", "unicode"])
def test_long_snapshot_diff_cursors_keep_order_replay_and_comparison_scope(tmp_path, identity):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    queries = GraphQueries(store)
    for suffix in ("3", "1", "0"):
        store.put_declaration(
            identity + suffix, "owner", Edge("before", "declared"), expected_version=0
        )
    before = snapshot(queries, "before")
    store.remove_declaration(identity + "0", expected_version=1)
    store.put_declaration(identity + "1", "owner", Edge("after", "declared"), expected_version=1)
    store.put_declaration(identity + "2", "owner", Edge("after", "declared"), expected_version=0)
    after = snapshot(queries, "after")
    queries = GraphQueries(GraphStore(store.path, store.scope))
    first = queries.diff(before, after, limit=1)
    cursor = first["next_cursor"]
    assert len(cursor) <= 2048
    other = GraphQueries(GraphStore.open(tmp_path, GraphScope("other", "editor", "project")))
    with pytest.raises(ValueError):
        other.diff(before, after, cursor=cursor)
    with pytest.raises(ValueError):
        queries.diff(after, before, cursor=cursor)
    changes, compared = list(first["changes"]), first["compared"]
    # The immutable historical comparison remains valid after live graph writes.
    store.put_declaration(
        "extra", "owner", Edge("outside-snapshot", "declared"), expected_version=0
    )
    for _ in range(3):
        page = queries.diff(before, after, limit=1, cursor=cursor)
        assert queries.diff(before, after, limit=1, cursor=cursor) == page
        changes.extend(page["changes"])
        compared += page["compared"]
        cursor = page["next_cursor"]
        if cursor is not None:
            assert len(cursor) <= 2048
    assert cursor is None
    assert compared == 4
    assert [(item["id"], item["change"]) for item in changes] == [
        ("declared:" + identity + "0", "removed"),
        ("declared:" + identity + "1", "modified"),
        ("declared:" + identity + "2", "added"),
    ]


def test_legacy_query_and_diff_cursors_remain_accepted(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    queries = GraphQueries(store)
    before = snapshot(queries, "before")
    for identity in ("a", "b"):
        store.put_declaration(identity, "owner", Edge("target", "declared"), expected_version=0)
    after = snapshot(queries, "after")
    legacy_query = base64.urlsafe_b64encode(
        _json(
            {
                "query": _hash((store.scope.key, "owner", "outgoing")),
                "revision": store.revision(),
                "last": "declared:a",
            }
        ).encode()
    ).decode()
    assert [edge["id"] for edge in store.query("owner", cursor=legacy_query)["edges"]] == [
        "declared:b"
    ]
    legacy_diff = base64.urlsafe_b64encode(
        json.dumps(
            {"scope": store.scope.key, "before": before, "after": after, "last": "declared:a"}
        ).encode()
    ).decode()
    assert [item["id"] for item in queries.diff(before, after, cursor=legacy_diff)["changes"]] == [
        "declared:b"
    ]


@pytest.mark.parametrize("anchor", [None, True, 0, -1, 2**63, "1", []])
def test_malformed_row_anchors_are_rejected(tmp_path, anchor):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    queries = GraphQueries(store)
    before = snapshot(queries, "before")
    for identity in ("a", "b"):
        store.put_declaration(identity, "owner", Edge("target", "declared"), expected_version=0)
    after = snapshot(queries, "after")
    for token, resume in (
        (
            store.query("owner", limit=1)["next_cursor"],
            lambda cursor: store.query("owner", cursor=cursor),
        ),
        (
            queries.diff(before, after, limit=1)["next_cursor"],
            lambda cursor: queries.diff(before, after, cursor=cursor),
        ),
    ):
        position = json.loads(base64.urlsafe_b64decode(token))
        position["last_row"] = anchor
        malformed = base64.urlsafe_b64encode(_json(position).encode()).decode()
        with pytest.raises(ValueError, match="cursor"):
            resume(malformed)


def test_reused_row_anchor_cannot_silently_skip_another_edge(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    queries = GraphQueries(store)
    before = snapshot(queries, "before")
    for identity in ("a", "b"):
        store.put_declaration(identity, "owner", Edge("target", "declared"), expected_version=0)
    after = snapshot(queries, "after")
    for token, resume in (
        (
            store.query("owner", limit=1)["next_cursor"],
            lambda cursor: store.query("owner", cursor=cursor),
        ),
        (
            queries.diff(before, after, limit=1)["next_cursor"],
            lambda cursor: queries.diff(before, after, cursor=cursor),
        ),
    ):
        position = json.loads(base64.urlsafe_b64decode(token))
        position["last_row"] += 1
        shifted = base64.urlsafe_b64encode(_json(position).encode()).decode()
        with pytest.raises(ValueError, match="cursor anchor"):
            resume(shifted)
