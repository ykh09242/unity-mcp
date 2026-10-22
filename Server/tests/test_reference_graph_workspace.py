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


def test_document_state_hydrates_from_observation_after_schema5(workspace):
    generation = workspace.observe("worker", Observation("owner", "code", "hash"))
    with workspace.store._connect(write=True) as connection:
        connection.execute(
            "INSERT INTO graph_documents VALUES (?,?,?,?,?)",
            ("owner", "csharp", "hash", "{}", "assembly-signature"),
        )
    workspace.publish("worker", "owner", "code", generation, Analysis())
    assert workspace.documents()[0]["state"] == "ready"


def test_streamed_analysis_is_atomic_batched_and_cleans_cancelled_staging(workspace):
    generation = workspace.observe("worker", Observation("owner", "code", "old"))
    workspace.publish("worker", "owner", "code", generation, Analysis([Edge("old", "load")]))
    generation = workspace.observe("worker", Observation("owner", "code", "new"))

    def events():
        for index in range(900):
            if index == 600:
                assert {edge["target"] for edge in workspace.store.query("owner")["edges"]} == {
                    "old"
                }
                with workspace.store._connect() as connection:
                    assert (
                        connection.execute("SELECT count(*) FROM graph_staging").fetchone()[0] > 0
                    )
            yield Edge(f"target{index}", "load")
        yield {"reason": "dynamic_key"}

    workspace.publish_code_events("worker", "owner", generation, events(), lambda: {"dependency"})
    with workspace.store._connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM edges WHERE owner='owner'").fetchone()[0]
            == 900
        )
        assert connection.execute("SELECT count(*) FROM graph_staging").fetchone()[0] == 0
    assert workspace.diagnostics("owner")["issues"][0]["reason"] == "dynamic_key"
    generation = workspace.observe("worker", Observation("owner", "code", "next"))

    def interrupted():
        yield from (Edge(f"partial{index}", "load") for index in range(600))
        raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError):
        workspace.publish_code_events("worker", "owner", generation, interrupted(), set)
    with workspace.store._connect() as connection:
        assert connection.execute("SELECT count(*) FROM graph_staging").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM edges WHERE owner='owner'").fetchone()[0]
            == 900
        )


def test_streamed_analysis_cannot_commit_after_generation_or_worker_changes(workspace):
    generation = workspace.observe("worker", Observation("owner", "code", "source"))

    def stale():
        yield Edge("target", "load")
        workspace.observe("worker", Observation("owner", "code", "changed"))

    with pytest.raises(RevisionConflict):
        workspace.publish_code_events("worker", "owner", generation, stale(), set)
    assert workspace.store.query("owner")["edges"] == []


def test_version_one_upgrade_preserves_evidence_and_progress(workspace):
    workspace.record_page("worker", [row()], {"phase": "collect", "after": 7}, "scan")
    generation = workspace.observe("worker", Observation("owner", "code", "hash"))
    workspace.publish("worker", "owner", "code", generation, Analysis([Edge("target", "load")]))
    workspace.store.put_declaration("manual", "owner", Edge("other", "manual"), expected_version=0)
    before = workspace.store.query("owner")
    with workspace.store._connect(write=True) as connection:
        connection.execute("DROP TABLE graph_asset_failures")
        connection.execute("ALTER TABLE graph_documents DROP COLUMN compilation_signature")
        connection.execute("UPDATE graph_workspace SET version=1")
    restored = GraphWorkspace(GraphStore(workspace.store.path, workspace.store.scope))
    assert restored.store.query("owner") == before
    assert restored.progress()["after"] == 7
    assert restored.counts()["failed_assets"] == 0
    with restored.store._connect() as connection:
        assert connection.execute("SELECT version FROM graph_workspace").fetchone()[0] == 6


def test_version_two_upgrade_retains_addressables_failures(workspace):
    asset = {**row(), "addressables": "unavailable", "addressables_error": "IOException"}
    workspace.record_page("worker", [asset], {}, "scan")
    with workspace.store._connect(write=True) as connection:
        connection.execute("DROP TABLE graph_addressables")
        connection.execute("ALTER TABLE graph_documents DROP COLUMN compilation_signature")
        connection.execute("UPDATE graph_workspace SET version=2")
    restored = GraphWorkspace(workspace.store)
    assert restored.provider_status()["addressables"]["errors"] == 1
    assert restored.diagnostics("asset:" + asset["guid"])["coverage"][0]["state"] == "failed"
    restored.remove_path("worker", asset["path"])
    assert restored.provider_status()["addressables"]["errors"] == 0


def test_version_four_migrates_compilation_signature_without_losing_facts(workspace):
    asset = {**row(), "source_format": "csharp", "path": "Assets/Caller.cs"}
    owner = "asset:" + asset["guid"]
    workspace.record_page("worker", [asset], {}, "scan")
    workspace.save_document(
        "worker", owner, "csharp", "bytes", {"build": {"binding_revision": "assembly-a"}}
    )
    workspace.clean_asset("worker", asset["guid"])
    with workspace.store._connect(write=True) as connection:
        connection.execute("ALTER TABLE graph_documents DROP COLUMN compilation_signature")
        connection.execute("UPDATE graph_workspace SET version=4")
    restored = GraphWorkspace(workspace.store)
    assert restored.compilation_candidates()[0]["signature"] == "assembly-a"
    assert restored.documents([owner])[0]["fingerprint"] == "bytes"


def test_source_guard_observes_concurrent_revocation_without_a_pinned_snapshot(workspace):
    source = "external:fixture"
    workspace.store.set_source(source, {}, expected_version=0)
    with workspace.source_guard(source, 1) as require_source:
        require_source()
        # A distinct writer must be visible on the next guard check, even while
        # the scan's connection remains open. This is not a cached grant.
        other = GraphStore(workspace.store.path, workspace.store.scope)
        other.set_source(source, {}, expected_version=1, enabled=False)
        with pytest.raises(RevisionConflict, match="grant changed"):
            require_source()


def test_retired_worker_cannot_record_failure_or_replace_diagnostics(workspace):
    workspace.record_page("worker", [row()], {}, "scan")
    workspace.release("worker")
    assert workspace.acquire("replacement")
    before = workspace.store.status()
    with pytest.raises(RevisionConflict):
        workspace.fail_asset("worker", row(), ValueError("retired input"))
    assert workspace.store.status() == before
    assert workspace.counts()["failed_assets"] == 0


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
