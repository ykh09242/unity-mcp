"""Retired rows consume bounded work without corrupting response or cursor accounting."""

import base64
from contextlib import contextmanager
import json

import pytest

from services.reference_graph import reclamation, response_budget
from services.reference_graph.operations import cleanup
from services.reference_graph.runtime import RuntimeObservations
from services.reference_graph.store import (
    Edge,
    GraphScope,
    GraphStore,
    RevisionConflict,
    edge_candidates,
)
from services.reference_graph.workspace import GraphWorkspace


def retired_graph(tmp_path, *, hidden_first=True):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    workspace = GraphWorkspace(store)
    runtime = RuntimeObservations(workspace)
    capture = runtime.begin()["id"]
    hidden, visible = ("a-retired", "z-visible") if hidden_first else ("z-retired", "a-visible")
    with store._connect(write=True) as db:
        store._insert_edge(db, "owner", "runtime:" + capture, Edge("target", "runtime"), hidden)
        store._insert_edge(
            db, "owner", "manual", Edge("target", "declared", evidence="x" * 300), visible
        )
    runtime.release(capture)
    return workspace, capture, hidden, visible


@pytest.mark.parametrize("direction,node", [("incoming", "target"), ("outgoing", "owner")])
def test_reclaimed_hidden_anchor_requires_explicit_restart_after_reopen(tmp_path, direction, node):
    workspace, _, hidden, visible = retired_graph(tmp_path)
    store = workspace.store
    first = store.query(node, direction=direction, limit=1)
    assert first["edges"] == [] and first["next_cursor"]
    assert len(first["next_cursor"]) <= 1024
    position = json.loads(base64.urlsafe_b64decode(first["next_cursor"]))
    assert [
        edge["id"]
        for edge in store.query(node, direction=direction, cursor=first["next_cursor"])["edges"]
    ] == [visible]
    assert not cleanup(workspace, before=0, limit=100)["more"]
    reopened = GraphStore(store.path, store.scope)
    assert reopened.revision() == first["revision"]
    with pytest.raises(RevisionConflict, match="reclaimed.*restart"):
        reopened.query(node, direction=direction, cursor=first["next_cursor"])
    assert [edge["id"] for edge in reopened.query(node, direction=direction)["edges"]] == [visible]
    # Legacy stable ordering keys remain usable even when the physical row is gone.
    legacy = {key: value for key, value in position.items() if key in ("query", "revision")}
    legacy["last"] = hidden
    cursor = base64.urlsafe_b64encode(json.dumps(legacy).encode()).decode()
    assert [
        edge["id"] for edge in reopened.query(node, direction=direction, cursor=cursor)["edges"]
    ] == [visible]


def test_reclamation_generation_changes_only_with_committed_edge_deletion(tmp_path):
    workspace, capture, _, visible = retired_graph(tmp_path)
    store = workspace.store
    first = store.query("target", direction="incoming", limit=1)
    with pytest.raises(InterruptedError):
        with store._connect(write=True) as db:
            assert reclamation.runtime(db, capture, 100)[0] == 1
            raise InterruptedError("rollback reclamation")
    assert [
        edge["id"]
        for edge in store.query("target", direction="incoming", cursor=first["next_cursor"])[
            "edges"
        ]
    ] == [visible]
    with store._connect(write=True) as db:
        assert reclamation.runtime(db, "absent", 100)[0] == 0
    assert store.query("target", direction="incoming", cursor=first["next_cursor"])["edges"]
    with store._connect(write=True) as db:
        reclamation.runtime(db, capture, 100)
    with pytest.raises(RevisionConflict, match="reclaimed"):
        store.query("target", direction="incoming", cursor=first["next_cursor"])


@pytest.mark.parametrize("operation", ["query", "snapshot"])
def test_retired_rows_do_not_charge_near_capacity_visible_page(tmp_path, monkeypatch, operation):
    workspace, _, _, visible = retired_graph(tmp_path, hidden_first=False)
    store = workspace.store
    queries = workspace.queries()
    with store._connect() as db:
        rows = edge_candidates(db, "id=?", (visible,), 1)
        charge = rows.fetchone()["_response_bytes"]
        rows.close()
    monkeypatch.setattr(response_budget, "MAX_PAGE_BYTES", charge + 1)
    result = store.query("owner") if operation == "query" else queries.snapshot()
    assert [edge["id"] for edge in result["edges"]] == [visible]
    assert result["next_cursor"] is None


def test_cursor_state_migration_is_transactional_and_preserves_existing_graph(
    tmp_path, monkeypatch
):
    workspace, _, _, visible = retired_graph(tmp_path)
    store = workspace.store
    with store._connect(write=True) as db:
        db.execute("DROP TABLE IF EXISTS graph_cursor_state")
    connect = GraphStore._connect

    @contextmanager
    def interrupted(self, *args, **kwargs):
        with connect(self, *args, **kwargs) as db:
            yield db
            if kwargs.get("write"):
                raise InterruptedError("cursor migration rollback")

    with monkeypatch.context() as patch:
        patch.setattr(GraphStore, "_connect", interrupted)
        with pytest.raises(InterruptedError, match="migration"):
            GraphStore(store.path, store.scope)
    with store._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='graph_cursor_state'"
        ).fetchone()
    reopened = GraphStore(store.path, store.scope)
    assert [edge["id"] for edge in reopened.query("owner")["edges"]] == [visible]
    with reopened._connect() as db:
        assert (
            db.execute("SELECT reclamation FROM graph_cursor_state WHERE id=1").fetchone()[0] == 0
        )


@pytest.mark.parametrize("generation", [None, True, -1, 2**63, "0", []])
def test_invalid_reclamation_generation_is_rejected(tmp_path, generation):
    workspace, _, _, _ = retired_graph(tmp_path)
    cursor = workspace.store.query("owner", limit=1)["next_cursor"]
    position = json.loads(base64.urlsafe_b64decode(cursor))
    position["reclamation"] = generation
    cursor = base64.urlsafe_b64encode(json.dumps(position).encode()).decode()
    with pytest.raises(ValueError, match="cursor"):
        workspace.store.query("owner", cursor=cursor)


def test_legacy_row_anchor_stays_usable_when_reclamation_did_not_remove_it(tmp_path):
    workspace, _, _, visible = retired_graph(tmp_path, hidden_first=False)
    first = workspace.store.query("owner", limit=1)
    assert first["edges"][0]["id"] == visible
    position = json.loads(base64.urlsafe_b64decode(first["next_cursor"]))
    position.pop("reclamation")
    legacy = base64.urlsafe_b64encode(json.dumps(position).encode()).decode()
    cleanup(workspace, before=0, limit=100)
    result = workspace.store.query("owner", cursor=legacy)
    assert result["edges"] == [] and result["next_cursor"] is None
