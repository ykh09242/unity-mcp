"""Wrapper admission preserves a usable graph across rejected and racing writes."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from services.reference_graph.analysis import AnalysisIndex, Catalog, parse_csharp
from services.reference_graph.store import Edge, GraphScope, GraphStore, RevisionConflict
from tests.timing import watchdog_timeout


@pytest.fixture
def graph(tmp_path):
    return GraphStore(tmp_path / "rules.sqlite", GraphScope("user", "editor", "project"))


def assert_wrapper_usable(graph):
    index = AnalysisIndex(
        [
            parse_csharp(
                "caller", "Assets/Caller.cs", 'class C { void M() { Custom.Loader.Load("One"); } }'
            )
        ],
        Catalog(
            [
                {
                    "guid": "1" * 32,
                    "path": "Assets/Resources/One.prefab",
                    "keys": [{"scheme": "resources", "value": "One"}],
                }
            ]
        ),
        [row["config"] for row in graph.rules() if row["config"].get("enabled", True)],
    )
    assert [edge.target for edge in index.analyze("caller").edges] == ["asset:" + "1" * 32]


@pytest.mark.parametrize("conflict", ["insert", "replace", "enable"])
def test_duplicate_enabled_wrapper_rejection_preserves_published_graph(graph, conflict):
    active = {"call": "Custom.Loader.Load", "scheme": "resources"}
    assert graph.put_rule("first", active, expected_version=0) == 1
    expected_version = 0
    if conflict != "insert":
        previous = (
            {"call": "Other.Loader.Load", "scheme": "resources"}
            if conflict == "replace"
            else {**active, "enabled": False}
        )
        expected_version = graph.put_rule("second", previous, expected_version=0)
    generation = graph.observe(
        "caller", "code", "source", rules_revision=graph.rules_state()["revision"]
    )
    assert graph.publish("caller", "code", generation, [Edge("asset:" + "1" * 32, "resources")])
    before = (graph.rules_state(), graph.status(), graph.query("caller"), graph.pending())

    with pytest.raises(ValueError, match="[Dd]uplicate.*wrapper|[Ww]rapper.*already"):
        graph.put_rule("second", {**active, "prefix": "UI/"}, expected_version=expected_version)

    assert (graph.rules_state(), graph.status(), graph.query("caller"), graph.pending()) == before
    assert_wrapper_usable(graph)
    reopened = GraphStore(graph.path, graph.scope)
    assert reopened.rules_state() == before[0]
    assert_wrapper_usable(reopened)


def test_same_wrapper_id_updates_and_disabled_duplicates_remain_valid(graph):
    active = {"call": "Custom.Loader.Load", "scheme": "resources"}
    assert graph.put_rule("first", active, expected_version=0) == 1
    assert graph.put_rule("first", {**active, "argument": 1}, expected_version=1) == 2
    assert graph.put_rule("second", {**active, "enabled": False}, expected_version=0) == 1
    assert graph.put_rule("third", {**active, "enabled": False}, expected_version=0) == 1
    before = graph.rules_state()
    with pytest.raises(RevisionConflict):
        graph.put_rule("first", active, expected_version=1)
    assert graph.rules_state() == before
    assert graph.put_rule("first", {"enabled": False}, expected_version=2) == 3
    assert graph.put_rule("second", active, expected_version=1) == 2
    assert_wrapper_usable(graph)


def test_concurrent_wrapper_registration_admits_only_one_enabled_call(graph):
    other = GraphStore(graph.path, graph.scope)
    ready = threading.Barrier(2)
    before = graph.status()["revision"]
    rules_revision = graph.rules_state()["revision"]

    def register(store, identity):
        ready.wait(timeout=watchdog_timeout())
        try:
            return store.put_rule(
                identity, {"call": "Custom.Loader.Load", "scheme": "resources"}, expected_version=0
            )
        except ValueError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [
            pool.submit(register, store, identity)
            for store, identity in ((graph, "one"), (other, "two"))
        ]
        results = [attempt.result(timeout=watchdog_timeout()) for attempt in attempts]

    assert results.count(1) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    state = graph.rules_state()
    assert len(state["rules"]) == 1
    assert state["rules"][0]["version"] == 1
    assert state["revision"] == rules_revision + 1
    assert graph.status()["revision"] == before + 1
    assert_wrapper_usable(graph)
