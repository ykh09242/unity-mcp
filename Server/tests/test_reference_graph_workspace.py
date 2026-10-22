"""Derived state and recovery share the graph's identity and SQLite transaction."""

import time

import pytest

from services.reference_graph.analysis import Analysis
from services.reference_graph.store import (
    Edge,
    GraphScope,
    GraphStore,
    Observation,
    RevisionConflict,
)
from services.reference_graph.workspace import GraphWorkspace


@pytest.fixture
def workspace(tmp_path):
    store = GraphStore.open(tmp_path, GraphScope("user", "editor", "project"))
    result = GraphWorkspace(store)
    assert result.acquire("worker")
    return result


def row(guid="a" * 32, path="Assets/Resources/Fixture.prefab"):
    return {
        "guid": guid,
        "path": path,
        "kind": "asset",
        "source_format": "none",
        "keys": [{"scheme": "resources", "value": "Fixture"}],
    }


def test_worker_lease_excludes_other_server_and_fences_expired_owner(workspace):
    other = GraphWorkspace(GraphStore(workspace.store.path, workspace.store.scope))
    assert not other.acquire("other")
    with workspace.store._connect(write=True) as connection:
        connection.execute("UPDATE graph_worker SET expires=?", (time.time() - 1,))
    assert other.acquire("other")
    with pytest.raises(RevisionConflict):
        workspace.record_page("worker", [row()], {"phase": "census"}, "scan")
    other.release("other")
    assert workspace.acquire("worker")


def test_census_pages_and_cursor_resume_atomically_and_remove_missing_assets(workspace):
    workspace.record_page(
        "worker", [row(), row("b" * 32, "Assets/B.prefab")], {"phase": "census", "offset": 2}, "one"
    )
    restored = GraphWorkspace(GraphStore(workspace.store.path, workspace.store.scope))
    assert restored.progress()["offset"] == 2
    assert len(restored.dirty_assets()) == 2
    generation = workspace.store.observe("asset:" + "b" * 32, "unity", "old")
    workspace.store.publish(
        "asset:" + "b" * 32, "unity", generation, [Edge("asset:" + "a" * 32, "unity_dependency")]
    )
    workspace.record_page("worker", [row()], {"phase": "census", "offset": 1}, "two")
    workspace.finish_census("worker", "two", {"phase": "collect", "epoch": "epoch", "after": 7})
    assert len(workspace.catalog()) == 1
    assert not workspace.store.query("asset:" + "b" * 32)["edges"]
    assert workspace.progress()["after"] == 7


def test_publication_keeps_facts_coverage_and_edges_in_one_generation(workspace):
    owner = "asset:" + "a" * 32
    workspace.record_page("worker", [row()], {}, "scan")
    generation = workspace.store.observe(owner, "unity", "hash")
    result = Analysis(
        [Edge("target", "unity_dependency")], [{"reason": "unavailable"}], coverage="partial"
    )
    assert workspace.publish("worker", owner, "unity", generation, result)
    assert workspace.diagnostics(owner)["issues"][0]["reason"] == "unavailable"
    workspace.store.observe(owner, "unity", "new")
    assert not workspace.publish(
        "worker", owner, "unity", generation, Analysis([Edge("stale", "old")])
    )
    assert workspace.store.query(owner)["edges"][0]["target"] == "target"
    assert workspace.store.query(owner)["edges"][0]["stale"]


def test_optional_exclusion_policy_is_generic_and_cas_clears_generated_cache(workspace):
    workspace.record_page("worker", [row()], {}, "scan")
    workspace.store.put_declaration("kept", "owner", Edge("target", "manual"), expected_version=0)
    assert workspace.set_policy({"exclude": ["Assets/Private/**"]}, expected_version=0) == 1
    assert workspace.admitted("Assets/Resources/GameData/Any.asset")
    assert not workspace.admitted("Assets/Private/Any.asset")
    assert workspace.catalog() == []
    assert workspace.store.declarations()[0]["id"] == "kept"
    with pytest.raises(RevisionConflict):
        workspace.set_policy({"exclude": []}, expected_version=0)


def test_reconciliation_preserves_durable_dependency_invalidation(workspace):
    for name in ("wrapper", "caller"):
        generation = workspace.store.observe(name, "code", "hash")
        analysis = Analysis(
            [Edge("asset", "resources_load")],
            dependencies={"wrapper"} if name == "caller" else set(),
        )
        assert workspace.publish("worker", name, "code", generation, analysis)
    workspace.invalidate_dependents("worker", {"wrapper"})
    assert {item["owner"] for item in workspace.store.pending()} == {"caller", "wrapper"}


@pytest.mark.parametrize("provider", ["unity", "code", "external:settings"])
def test_observation_admission_is_fenced_before_any_durable_change(workspace, provider):
    if provider.startswith("external:"):
        workspace.store.set_source(provider, {}, expected_version=0)
    observation = Observation("owner", provider, "hash", source_version=1, force=True)
    generation = workspace.observe("worker", observation)
    workspace.publish(
        "worker", "owner", provider, generation, Analysis([Edge("target", "reference")])
    )
    workspace.release("worker")
    assert workspace.acquire("next")
    before = workspace.store.status()
    with pytest.raises(RevisionConflict):
        workspace.observe("worker", observation)
    assert workspace.store.status() == before
    assert not workspace.store.query("owner")["edges"][0]["stale"]


def test_source_grant_cleanup_and_retirement_are_atomic(workspace):
    workspace.set_source("external:settings", {}, expected_version=0)
    assert workspace.acquire("worker")
    generation = workspace.observe(
        "worker", Observation("owner", "external:settings", "hash", source_version=1)
    )
    workspace.publish(
        "worker",
        "owner",
        "external:settings",
        generation,
        Analysis([], [{"key": "old-root-value"}]),
    )
    workspace.record_external(
        "worker", "external:settings", "owner", "a.json", "hash", "stamp", "key", "scan"
    )
    with pytest.raises(RevisionConflict):
        workspace.set_source("external:settings", {}, expected_version=0)
    assert workspace.diagnostics("owner")["issues"]
    workspace.set_source("external:settings", {}, expected_version=1)
    assert workspace.diagnostics("owner")["issues"] == []
    assert workspace.store.status()["queue_idle"]
    with pytest.raises(RevisionConflict):
        workspace.record_external(
            "worker", "external:settings", "owner", "a.json", "hash", "stamp", "key", "scan"
        )
    assert not workspace.external_inputs("external:settings")
