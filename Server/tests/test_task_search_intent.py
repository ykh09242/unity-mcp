"""The caller can select outcomes independently of the spelling of its task."""

import json

import pytest
from click.testing import CliRunner

from cli.main import cli


def result(*args):
    response = CliRunner().invoke(cli, ["--format", "json", "tool", *args])
    assert response.exit_code == 0, response.output
    return json.loads(response.stdout)


@pytest.mark.parametrize(
    "query",
    [
        "현재 게임 화면 중앙의 UI 입력 우선순위를 조회",
        "¿Qué elemento de la UI recibe el puntero primero?",
        "أي عنصر UI يتلقى المؤشر أولاً؟",
    ],
)
def test_one_shared_word_keeps_lexical_matches_but_recommends_browsing(query):
    lexical = result("search", query, "--catalog-query", query)
    assert lexical["matching"] == "lexical"
    assert lexical["results"] and lexical["next_action"] == "inspect_candidates"
    assert lexical["routes"][0]["matched_terms"] == ["ui"]
    assert (lexical["match_basis"], lexical["recommended_action"]) == (
        "single_term",
        "browse_catalog",
    )
    observation = result("search", query, "--catalog-query", query, "--intent", "observe")
    assert observation["results"] == lexical["results"]
    assert all(route["intent"] == "observe" for route in observation["routes"])
    assert all(
        group["browse"]["intent"] == "observe" for group in observation["navigation"]["groups"]
    )


@pytest.mark.parametrize(
    "query", ["UI は on the 画面", "게임 화면 중앙에서 the UI 가 입력을 먼저 받나요?"]
)
def test_common_catalog_words_do_not_corroborate_an_incidental_match(query):
    observed = result("search", query, "--catalog-query", query, "--intent", "observe")
    assert observed["recommended_action"] == "browse_catalog"
    assert len(observed["routes"][0]["distinctive_terms"]) <= 1


def test_short_identifier_words_still_corroborate_a_match():
    from services.registry.task_search import _route_evidence

    route = {"kind": "tool", "tool": "inspect_ui_id", "purpose": "Read UI ID on a panel."}
    words = {"ui", "id", "on"}
    assert _route_evidence(route, "ui id on", words, words, set()) == "multiple_terms"
    assert route["matched_terms"] == ["id", "on", "ui"]
    assert route["distinctive_terms"] == ["id", "ui"]


@pytest.mark.parametrize("term", ["et", "λ", "값", "层"])
def test_short_prose_terms_use_catalog_frequency_across_scripts(term):
    from services.registry.task_search import _route_evidence

    route = {"kind": "tool", "tool": "inspect_item", "purpose": term}
    words = {term}
    assert _route_evidence(route, term, words, words, set()) == "incidental"
    assert _route_evidence(route, term, words, words, words) == "single_term"


def test_domain_navigation_distinguishes_hit_order_from_dispatch_without_shared_words():
    query = "画面中央ではどの要素が入力を受けますか"
    response = result("search", query, "--intent", "observe")
    assert response["query"] == response["matched_query"] == query
    assert response["match_basis"] != "declared_observation"
    assert "required_values" not in response
    assert all(route["readiness"] == "unobserved" for route in response["routes"])
    ui = next(item["browse"] for item in response["navigation"]["groups"] if item["group"] == "ui")
    cards = result("catalog", "--group", ui["group"], "--intent", ui["intent"], "--limit", "20")
    while not any(card["action"] == "diagnose_raycast" for card in cards["actions"]):
        assert cards["next_offset"] is not None
        cards = result(
            "catalog",
            "--group",
            "ui",
            "--intent",
            "observe",
            "--offset",
            str(cards["next_offset"]),
            "--limit",
            "20",
        )
    raycast = next(card for card in cards["actions"] if card["action"] == "diagnose_raycast")
    assert raycast["intent"] == "observe" and raycast["effects"]["readOnlyHint"] is False
    inputs = result("catalog", "--tool", "manage_input", "--intent", "interact", "--limit", "20")
    assert {card["action"] for card in inputs["actions"]} >= {"ui_click", "mouse", "touch"}
    assert not any(card["action"] == "status" for card in inputs["actions"])


def test_exact_identifier_and_multiple_route_terms_have_distinct_lexical_bases():
    exact = result("search", "ＤＩＡＧＮＯＳＥ＿ＲＡＹＣＡＳＴ", "--intent", "observe")
    assert exact["match_basis"] == "exact_identifier"
    assert exact["routes"][0]["operation"] == "diagnose_raycast"
    query = "pointer raycast occlusion"
    multi = result("search", query, "--catalog-query", query, "--intent", "observe")
    assert (multi["match_basis"], multi["recommended_action"]) == (
        "multiple_terms",
        "inspect_candidates",
    )
    assert multi["routes"][0]["readiness"] == "unobserved"
    assert multi["routes"][0]["advertised"] is None


@pytest.mark.parametrize(
    "query",
    [
        "콘솔에 찍힌 에러만 보여줘",
        "Show only Console errors",
        "コンソールのエラーだけ見せて",
        "Enséñame solo los errores de la consola",
        "أرني أخطاء وحدة التحكم فقط",
        "Konsoldaki hataları göster",
    ],
)
def test_explicit_console_evidence_selection_keeps_the_original_request(query):
    from services.registry.observation_predicates import evaluate_observation

    search = result("search", query, "--intent", "observe", "--required-value", "console.entries")
    route = search["routes"][0]
    assert search["query"] == query and search["matching"] == "observation"
    assert (route["tool"], route["operation"]) == ("read_console", "get")
    assert route["arguments"] == {"action": "get", "format": "json", "page_size": 10}
    assert 'types=["error"]' in route["purpose"]
    for items, expected in (([], True), ([{"message": "오류"}], True), ("", False)):
        receipt = {"success": True, "data": {"items": items}}
        assert evaluate_observation(route["observation"], receipt, ["console.entries"]) == {
            "console.entries": expected
        }


def test_console_read_and_clear_have_separate_intents():
    read = result("catalog", "--tool", "read_console", "--intent", "observe")
    clear = result("catalog", "--tool", "read_console", "--intent", "interact")
    assert [c["action"] for c in read["actions"]] == ["get"]
    assert [c["action"] for c in clear["actions"]] == ["clear"]
