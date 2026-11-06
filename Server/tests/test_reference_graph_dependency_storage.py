"""Persist local code relationships while retaining transitive invalidation."""

import pytest

from services.reference_graph.store import GraphScope, GraphStore
from services.reference_graph.workspace import GraphWorkspace
from services.reference_graph.reconcile import Reconciler
from tests.test_reference_graph_analysis_deltas import save_csharp
from tests.test_reference_graph_reconcile import Editor, allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("target_count", [1, 300])
async def test_wrapper_chain_stores_linear_dependencies_and_invalidates_after_reopen(
    tmp_path, target_count
):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("u", "e", "p")))
    engine = Reconciler(workspace, Editor(), allowed)
    assert workspace.acquire(engine.worker)
    workspace.record_page(
        engine.worker,
        [
            {
                "guid": f"{i:032x}",
                "path": f"Assets/Resources/Folder/{i}.prefab",
                "keys": [{"scheme": "resources", "value": f"Folder/{i}"}],
            }
            for i in range(target_count)
        ],
        {},
        "scan",
    )
    size = 32
    for i in range(size):
        body = f"T{i + 1}.Load();" if i + 1 < size else 'UnityEngine.Resources.LoadAll("Folder");'
        save_csharp(
            workspace,
            engine.worker,
            str(i),
            f"class T{i} {{ public static void Load() {{ {body} }} }}",
        )
    await engine._analysis()
    with workspace.store._connect() as db:
        count = db.execute(
            "SELECT count(*) FROM graph_dependencies WHERE dependency NOT LIKE 'catalog:%' AND owner<>dependency"
        ).fetchone()[0]
        assert count == size - 1
        assert (
            db.execute("SELECT count(*) FROM edges WHERE owner='0'").fetchone()[0] == target_count
        )
    workspace.release(engine.worker)
    reopened = GraphWorkspace(GraphStore(workspace.store.path, workspace.store.scope))
    assert reopened.acquire("next")
    reopened.invalidate_dependents("next", [str(size - 1)])
    with reopened.store._connect() as db:
        pending = {
            row[0]
            for row in db.execute(
                "SELECT owner FROM observations WHERE provider='code' AND state='pending'"
            )
        }
    assert pending == {str(i) for i in range(size)}


@pytest.mark.asyncio
async def test_default_constant_chain_rebinds_leaf_and_catalog_changes(tmp_path):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("u", "e", "p")))
    engine = Reconciler(workspace, Editor(), allowed)
    worker = engine.worker
    assert workspace.acquire(worker)
    workspace.record_page(
        worker,
        [
            {
                "guid": key,
                "path": f"Assets/Resources/{key}.prefab",
                "keys": [{"scheme": "resources", "value": key}],
            }
            for key in ("Old", "New")
        ],
        {},
        "scan",
    )
    sources = {
        "caller": "class Caller { void M() { Loader.Load(); } }",
        "loader": "class Loader { public static void Load(string key = Names.Key) { UnityEngine.Resources.Load(key); } }",
        "names": "class Names { public const string Key = Leaf.Key; }",
        "leaf": 'class Leaf { public const string Key = "Old"; }',
    }
    for owner, source in sources.items():
        save_csharp(workspace, worker, owner, source)
    await engine._analysis()
    assert [edge["target"] for edge in workspace.store.query("caller")["edges"]] == ["asset:old"]
    save_csharp(workspace, worker, "leaf", 'class Leaf { public const string Key = "New"; }')
    await engine._analysis()
    assert [edge["target"] for edge in workspace.store.query("caller")["edges"]] == ["asset:new"]
    workspace.remove_path(worker, "Assets/Resources/New.prefab")
    await engine._analysis()
    assert workspace.store.query("caller")["edges"] == []


@pytest.mark.asyncio
async def test_recursive_code_dependencies_invalidate_without_looping(tmp_path):
    workspace = GraphWorkspace(GraphStore.open(tmp_path, GraphScope("u", "e", "p")))
    engine = Reconciler(workspace, Editor(), allowed)
    assert workspace.acquire(engine.worker)
    save_csharp(workspace, engine.worker, "a", "class A { public static void M() { B.M(); } }")
    save_csharp(workspace, engine.worker, "b", "class B { public static void M() { A.M(); } }")
    await engine._analysis()
    workspace.invalidate_dependents(engine.worker, ["a"])
    with workspace.store._connect() as db:
        assert {
            row[0]
            for row in db.execute(
                "SELECT owner FROM observations WHERE provider='code' AND state='pending'"
            )
        } == {"a", "b"}
