"""Raw model scores rank current declarations and retain the public search boundaries."""

import pytest

from services.registry import semantic_catalog, task_search
from services.registry.semantic_encoder import SemanticModelUnavailable


def entry(name, description):
    return {"name": name, "group": None, "description": description, "kwargs": {}}


def test_dynamic_semantic_candidates_are_limited_to_visible_host_declarations(monkeypatch):
    captured = []

    def score(query, texts):
        captured.extend(texts)
        return "local-test-model", (0.7,) * len(texts)

    monkeypatch.setattr(semantic_catalog, "encode_scores", score)
    entries = [entry("read_sample", "Return a sample."), entry("hidden_sample", "Private label.")]
    response = task_search.search_catalog(
        task_search.SearchRequest(query="consulta original", host_tool_names=["read_sample"]),
        entries,
        visible_names={"read_sample"},
    )
    assert response["query"] == response["matched_query"] == "consulta original"
    assert response["matching"] == "hybrid" and response["semantic_model"] == "local-test-model"
    assert captured == ["read sample. Return a sample."]
    route = response["route_groups"][0]["representative"]
    assert (route["tool"], route["semantic_similarity"], route["readiness"]) == (
        "read_sample",
        0.7,
        "unobserved",
    )


def test_broken_encoder_degrades_to_lexical_without_a_model_receipt(monkeypatch):
    def unavailable(*args):
        raise SemanticModelUnavailable("unavailable")

    monkeypatch.setattr(task_search, "semantic_scores", unavailable)
    response = task_search.search_catalog(
        task_search.SearchRequest(query="inspect samples"),
        [entry("read_sample", "Inspect samples.")],
    )
    assert response["matching"] == "lexical" and response["semantic_status"] == "unavailable"
    assert "semantic_model" not in response
    assert response["results"][0]["name"] == "read_sample"


@pytest.mark.parametrize(
    "query",
    [
        'locate "My Tool" please',
        "locate Étude Première please",
        "locate Assets/One Two/File.asset please",
    ],
)
def test_literal_context_retains_original_query_for_full_comparison(query):
    assert semantic_catalog._request_context(query).split() == ["locate", "please"]


def test_dispatcher_operation_prose_cannot_cross_action_boundaries():
    declarations = (
        "Operations:\n- read_sample: Report the sample.\n- clear_sample: Erase the sample."
    )
    descriptions = semantic_catalog.documented_actions(declarations)
    assert descriptions == {
        "read_sample": "Report the sample.",
        "clear_sample": "Erase the sample.",
    }


def test_resource_candidates_are_scored_against_the_requested_surface(monkeypatch):
    def scores(query, texts):
        return "local-test-model", tuple(
            0.92 if t.startswith("read errors.") else 0.42 for t in texts
        )

    monkeypatch.setattr(semantic_catalog, "encode_scores", scores)
    resource = {
        "uri": "mcpforunity://editor/logs",
        "name": "journal",
        "description": "List recent error messages.",
        "kwargs": {},
    }
    response = task_search.search_catalog(
        task_search.SearchRequest(
            query="Afficher les erreurs actuelles du compilateur",
            route_surfaces="resources",
            limit=1,
        ),
        [entry("read_errors", "Show compiler errors.")],
        visible_names={"read_errors"},
        resources=[resource],
    )
    assert response["route_groups"][0]["representative"]["uri"] == resource["uri"]
    assert response["match_basis"] == "semantic_similarity"


def test_hidden_identifier_cannot_supply_the_displayed_groups_evidence(monkeypatch):
    monkeypatch.setattr(
        semantic_catalog,
        "encode_scores",
        lambda q, texts: ("local-test-model", (0.48,) * len(texts)),
    )
    resource = {
        "uri": "mcpforunity://editor/history",
        "name": "console_history",
        "description": "Previous events.",
        "kwargs": {},
    }
    response = task_search.search_catalog(
        task_search.SearchRequest(query="archive_logs", route_surfaces="resources", limit=1),
        [entry("archive_logs", "Archive logs.")],
        visible_names=set(),
        resources=[resource],
    )
    assert response["route_groups"][0]["representative"]["uri"] == resource["uri"]
    assert response["match_basis"] == "semantic_similarity"
