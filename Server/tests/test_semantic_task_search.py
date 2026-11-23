"""Raw model scores rank current declarations and retain the public search boundaries."""

import pytest
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.registry import semantic_catalog, task_search
from services.registry.action_catalog import describe_action, documented_actions
from services.registry.semantic_encoder import SemanticModelUnavailable


def entry(name, description):
    return {"name": name, "group": None, "description": description, "kwargs": {}}


@pytest.mark.asyncio
async def test_mcp_resolves_scope_before_running_cpu_search_off_the_event_loop(monkeypatch):
    from services.tools import manage_tools

    caller = get_ident()
    item = entry("read_sample", "Read a sample.")
    monkeypatch.setattr(
        manage_tools,
        "_catalog_inventory",
        AsyncMock(return_value=({"read_sample": item}, {"read_sample"})),
    )
    monkeypatch.setattr(manage_tools, "_catalog_resources", AsyncMock(return_value=[]))

    def rank(request, entries, *, visible_names, resources):
        assert get_ident() != caller
        assert entries == [item] and visible_names == {"read_sample"} and resources == []
        return {"matching": "hybrid", "query": request.query}

    monkeypatch.setattr(task_search, "search_catalog", rank)
    ctx = SimpleNamespace(fastmcp=SimpleNamespace())
    result = await manage_tools.search_unity_tools(ctx, query="fonction disponible")
    assert result["query"] == "fonction disponible"


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
    ("query", "expected"), [("촬영 장면", "장면_촬영"), ("촬영 장면 지금", "장면_목록")]
)
def test_reordered_name_requires_all_query_words(monkeypatch, query, expected):
    entries = [entry("장면_촬영", ""), entry("장면_목록", "")]
    scores = {("tool", "장면_촬영", ""): 0.50, ("tool", "장면_목록", ""): 0.575}
    monkeypatch.setattr(task_search, "semantic_scores", lambda *_: ("test-model", scores))

    response = task_search.search_catalog(task_search.SearchRequest(query=query), entries)

    assert response["results"][0]["name"] == expected
    assert response["route_groups"][0]["representative"]["tool"] == expected


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
    declarations = "Operations:\n- empty:  \r\n- read_sample: Report the sample.\n- clear_sample: Erase the sample."
    descriptions = documented_actions(declarations)
    assert descriptions == {
        "read_sample": "Report the sample.",
        "clear_sample": "Erase the sample.",
    }


@pytest.mark.parametrize("description", [{}, {"description": " \t "}])
def test_dynamic_action_uses_its_own_declared_purpose_in_search_and_describe(description):
    item = entry(
        "stock_desk",
        "Inventory operations.\n- alpha: Report remaining stock.\n- beta: Discard damaged stock.",
    )
    item["input_schema"] = {
        "properties": {"action": {"enum": ["alpha", "beta", "gamma"]}, "aisle": {"type": "string"}},
        "oneOf": [
            {
                "properties": {"action": {"const": "alpha"}},
                "required": ["action", "aisle"],
                **description,
            },
            {"properties": {"action": {"const": "beta"}}, "description": "Remove damaged stock."},
            {"properties": {"action": {"const": "gamma"}}},
        ],
    }
    capabilities = [(item, task_search.tool_capability(item))]

    assert task_search.semantic_documents(capabilities, [], set(), None) == {
        ("tool", "stock_desk", "alpha"): ("stock desk alpha. Report remaining stock.",),
        ("tool", "stock_desk", "beta"): ("stock desk beta. Remove damaged stock.",),
        ("tool", "stock_desk", "gamma"): ("stock desk gamma",),
    }
    for action, purpose, source, required in (
        ("alpha", "Report remaining stock.", "declared", ["aisle"]),
        ("beta", "Remove damaged stock.", "declared", []),
        ("gamma", "gamma", "identifier", []),
    ):
        result = task_search.search_catalog(task_search.SearchRequest(query=action), [item])
        route = result["route_groups"][0]["representative"]
        assert (route["operation"], route["purpose"]) == (action, purpose)
        bound = describe_action(item, {"inputSchema": item["input_schema"]}, action)
        card = bound["operation"]
        assert (card["purpose"], card["purpose_source"], card["required_parameters"]) == (
            purpose,
            source,
            required,
        )
        assert bound["tool"]["inputSchema"]["required"] == ["action", *required]


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


def test_embedded_similarity_ranks_the_closest_routes_and_reports_their_scores(monkeypatch):
    scores = {
        "empty log. Erase the log.": 0.59,
        "observe log. Report entries.": 0.69,
        "inspect archive. Read historical data.": 0.42,
    }

    def encode(query, texts):
        assert query == "quelles erreurs"
        return semantic_catalog.STATIC_MODEL_ID, tuple(scores[text] for text in texts)

    monkeypatch.setattr(semantic_catalog, "encode_scores", encode)
    response = task_search.search_catalog(
        task_search.SearchRequest(query="quelles erreurs", route_surfaces="tools"),
        [
            entry("empty_log", "Erase the log."),
            entry("observe_log", "Report entries."),
            entry("inspect_archive", "Read historical data."),
        ],
    )
    assert response["semantic_model"] == semantic_catalog.STATIC_MODEL_ID
    assert response["match_basis"] == "semantic_similarity"
    routes = [group["representative"] for group in response["route_groups"]]
    assert [(route["tool"], route["semantic_similarity"]) for route in routes] == [
        ("observe_log", 0.69),
        ("empty_log", 0.59),
    ]
