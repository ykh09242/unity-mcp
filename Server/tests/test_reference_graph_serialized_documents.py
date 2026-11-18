"""Private serialized documents stream into the graph with exact reference provenance."""

from dataclasses import asdict
import json

import pytest

from services.reference_graph import serialized_documents
from services.reference_graph.analysis import (
    Catalog,
    iter_serialized_references,
    serialized_references,
)
from services.reference_graph.reconcile import Reconciler
from services.reference_graph.restart import open_workspace
from services.reference_graph.store import Edge, GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace
from tests.test_reference_graph_reconcile import Editor, allowed
from tests.test_reference_graph_restart import VersionedEditor


@pytest.fixture
def graph(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("serialized", "editor", "project"))
    workspace = GraphWorkspace(store)
    engine = Reconciler(workspace, Editor(), allowed)
    assert workspace.acquire(engine.worker)
    return workspace, engine


def save(graph, source, fingerprint="new"):
    workspace, engine = graph
    serialized_documents.save(
        workspace,
        engine.worker,
        "owner",
        fingerprint,
        iter_serialized_references(source),
        lambda: None,
    )


def edges(workspace):
    with workspace.store._connect() as db:
        return [
            json.loads(row[0]) for row in db.execute("SELECT payload FROM edges ORDER BY rowid")
        ]


@pytest.mark.asyncio
async def test_byte_pages_publish_the_same_provenance_and_restart(graph, monkeypatch):
    workspace, engine = graph
    monkeypatch.setattr(serialized_documents, "PAGE_BYTES", 128)
    source = (
        "--- !u!1 &42 stripped\nAsset:\n  refs:\n"
        + "".join(f"    - {{fileID: -{i}, guid: {i:032x}}}\n" for i in range(1, 17))
        + f'  address: {{m_AssetGUID: {"A" * 32}, m_SubObjectName: "{"눈" * 256}", '
        + f"m_SubObjectGUID: {'B' * 32}}}\n"
    )
    expected = [asdict(edge) for edge in serialized_references(source, Catalog([])).edges]
    save(graph, source)
    await engine._analysis()
    assert sorted(edges(workspace), key=lambda e: e["line"]) == expected
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_staging").fetchone()[0] == 0
    reopened = GraphWorkspace(GraphStore(workspace.store.path, workspace.store.scope))
    assert [
        asdict(edge) for edge in serialized_documents.events(reopened, "owner", None, lambda: None)
    ] == expected


@pytest.mark.asyncio
async def test_cancelled_scan_and_publication_keep_previous_edges_and_retry(graph, monkeypatch):
    workspace, engine = graph
    monkeypatch.setattr(serialized_documents, "PAGE_BYTES", 128)
    save(graph, f"old: {{fileID: 11, guid: {'a' * 32}}}", "old")
    await engine._analysis()
    old = edges(workspace)
    source = "refs:\n" + "".join(f"  - {{fileID: {i}, guid: {i:032x}}}\n" for i in range(1, 15))

    def interrupted_scan():
        for count, reference in enumerate(iter_serialized_references(source)):
            if count == 8:
                with workspace.store._connect() as db:
                    assert db.execute("SELECT count(*) FROM graph_staging").fetchone()[0] > 0
                raise RuntimeError("stop scan")
            yield reference

    with pytest.raises(RuntimeError, match="stop scan"):
        serialized_documents.save(
            workspace, engine.worker, "owner", "new", interrupted_scan(), lambda: None
        )
    assert workspace.documents(["owner"])[0]["fingerprint"] == "old"
    assert edges(workspace) == old
    save(graph, source)
    events = serialized_documents.events

    def interrupted_publish(*args):
        for count, edge in enumerate(events(*args)):
            if count == 8:
                with workspace.store._connect() as db:
                    assert db.execute("SELECT count(*) FROM graph_staging").fetchone()[0] > 0
                raise RuntimeError("stop publication")
            yield edge

    with monkeypatch.context() as patch:
        publish = workspace.publish_events

        def small_batches(*args, **kwargs):
            kwargs["batch_rows"] = 2
            return publish(*args, **kwargs)

        patch.setattr(workspace, "publish_events", small_batches)
        patch.setattr(serialized_documents, "events", interrupted_publish)
        with pytest.raises(RuntimeError, match="stop publication"):
            await engine._analysis()
    assert edges(workspace) == old
    with workspace.store._connect() as db:
        assert db.execute("SELECT count(*) FROM graph_staging").fetchone()[0] == 0
    await engine._analysis()
    assert sorted(int(edge["target_file_id"]) for edge in edges(workspace)) == list(range(1, 15))


@pytest.mark.asyncio
async def test_large_legacy_summaries_publish_one_generation_with_issues(graph, monkeypatch):
    workspace, engine = graph
    monkeypatch.setattr(serialized_documents, "PAGE_BYTES", 256)
    old_edges = [
        asdict(Edge(f"asset:{i:032x}", "serialized_object_reference", line=i)) for i in range(12)
    ]
    for owner in ["one", "two"]:
        workspace.save_document(
            engine.worker,
            owner,
            "summary",
            owner,
            {
                "path": "Assets/Old.prefab",
                "coverage": "old",
                "unresolved": [{"reason": "old_issue"}],
                "edges": old_edges,
            },
        )
    assert workspace.document_groups(["one", "two"]) == [["one"], ["two"]]
    assert all("edges" not in row["data"] for row in workspace.documents(["one", "two"]))
    await engine._analysis()
    assert len(edges(workspace)) == 24
    assert workspace.diagnostics("two")["issues"][0]["reason"] == "old_issue"


@pytest.mark.asyncio
async def test_new_editor_reuses_verified_serialized_pages_without_source_reads(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(serialized_documents, "PAGE_BYTES", 128)
    editor = VersionedEditor()
    source = "refs:\n" + "".join(f"  - {{fileID: -{i}, guid: {2:032x}}}\n" for i in range(1, 11))
    owner = editor.add(1, "Assets/Scene.unity", source)
    editor.assets[owner[6:]]["source_format"] = "unity_yaml"
    editor.add(2, "Assets/Target.asset")

    async def collect(action, **params):
        page = await editor(action, **params)
        if action == "source":
            page["source_format"] = "unity_yaml"
        return page

    old = open_workspace(tmp_path, GraphScope("user", editor.instance, "project"))
    assert await Reconciler(old, collect, allowed).reconcile()
    expected = sorted(edges(old), key=lambda edge: edge["line"])
    assert [(edge["target_file_id"], edge["line"]) for edge in expected] == [
        (str(-i), i + 1) for i in range(1, 11)
    ]

    editor.instance, editor.epoch, editor.snapshot = "next", "new-epoch", None
    editor.events.clear()
    editor.calls.clear()
    new = open_workspace(tmp_path, GraphScope("user", editor.instance, "project"))
    assert await Reconciler(new, collect, allowed).reconcile()
    assert any(action == "probe" for action, _ in editor.calls)
    assert not any(action == "source" for action, _ in editor.calls)
    assert sorted(edges(new), key=lambda edge: edge["line"]) == expected
