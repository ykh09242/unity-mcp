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
def test_one_shared_word_keeps_legacy_matches_but_recommends_browsing(query):
    raw = result("search", query)
    assert raw["results"] and raw["next_action"] == "inspect_candidates"
    assert raw["routes"][0]["matched_terms"] == ["ui"]
    assert (raw["match_basis"], raw["recommended_action"]) == ("single_term", "browse_catalog")
    observation = result("search", query, "--intent", "observe")
    assert observation["results"] == raw["results"]
    assert all(route["intent"] == "observe" for route in observation["routes"])
    assert all(
        group["browse"]["intent"] == "observe" for group in observation["navigation"]["groups"]
    )


@pytest.mark.parametrize(
    "query", ["UI は on the 画面", "게임 화면 중앙에서 the UI 가 입력을 먼저 받나요?"]
)
def test_common_catalog_words_do_not_corroborate_an_incidental_match(query):
    observed = result("search", query, "--intent", "observe")
    assert observed["recommended_action"] == "browse_catalog"
    assert len(observed["routes"][0]["distinctive_terms"]) <= 1


def test_domain_navigation_distinguishes_hit_order_from_dispatch_without_shared_words():
    response = result("search", "画面中央ではどの要素が入力を受けますか", "--intent", "observe")
    assert response["recommended_action"] == "browse_catalog"
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
    multi = result("search", "pointer raycast occlusion", "--intent", "observe")
    assert (multi["match_basis"], multi["recommended_action"]) == (
        "multiple_terms",
        "inspect_candidates",
    )
    assert multi["routes"][0]["readiness"] == "unobserved"
    assert multi["routes"][0]["advertised"] is None
