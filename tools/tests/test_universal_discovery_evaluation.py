"""Guard bundled ranking and public agent evidence on the original discovery fixtures."""

import argparse
from dataclasses import replace
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.registry.semantic_coverage import MODEL_ID as COVERAGE_ID
from services.registry.semantic_encoder import COMBINED_MODEL_ID
from services.registry.semantic_encoder import MODEL_ID as PRIMARY_ID

from tools.experiments.discovery_evaluation_fixture import FIXTURE, Request, load_suite
from tools.experiments.universal_discovery_evaluation import (
    PREREGISTERED_LIMITS,
    PROFILES,
    Inventory,
    ProfileResult,
    evaluate_profile,
    gate_failures,
    score,
)
from tools.tests.discovery_agent_contract import assert_public_agent_contract

REFERENCE = FIXTURE.with_name("universal_discovery_bundled_reference.json")
SUITES = (FIXTURE, FIXTURE.with_name("universal_discovery_queries_v2.json"))
EFFECT_FIXTURES = {
    "pairs": "85cad694d2991c66d9112be9091af247643b16ac8e9509b9963c40ff13d1854c",
    "confirmation": "9ea7ea36e00b700d41e393c811bb00a85a6f0288866c58e21aeaaa17984ac8d3",
}
SCENE_FIXTURES = {
    "edit_holdout": "386f463cf63b26cfecacec9bd05f3b0ae13f0e99f3a6ab1558c81c0138cea559",
    "activation_confirmation": "f8f123c5b3891a669da184fac148df70728bcd8d658d966766c391d86ce17a91",
}


def compare_bundled_reference(actual: ProfileResult, expected: dict) -> list[str]:
    if actual["languages"].keys() != expected["languages"].keys():
        return ["changed language coverage"]
    regressions = []
    for scope, observed, baseline in (
        ("all", actual["summary"], expected["summary"]),
        *(
            (language, actual["languages"][language], baseline)
            for language, baseline in expected["languages"].items()
        ),
    ):
        if observed["count"] != baseline["count"]:
            regressions.append(f"{scope}: changed denominator")
        for metric in ("available", "top1", "top3"):
            if observed[metric] < baseline[metric]:
                regressions.append(
                    f"{scope}: {metric} {observed[metric]} below shipped {baseline[metric]}"
                )
    for metric in ("wrong_effect_top1", "wrong_effect_top3"):
        if actual["summary"][metric] > expected["summary"][metric]:
            regressions.append(
                f"{metric} {actual['summary'][metric]} exceeds shipped {expected['summary'][metric]}"
            )
    return regressions


@pytest.mark.asyncio
async def test_raw_multilingual_ranking_does_not_regress_from_the_shipped_bundle():
    # Given the two frozen corpora and the independent measurement of the shipped ranking.
    reference = json.loads(REFERENCE.read_bytes())
    limits = argparse.Namespace(
        require_matching="hybrid",
        require_model=True,
        expect_model=(PRIMARY_ID, COVERAGE_ID, COMBINED_MODEL_ID),
        min_top1=0.0,
        min_top3=0.0,
        min_language_top1=0.0,
        min_language_top3=0.0,
        max_wrong_effect_top1=PREREGISTERED_LIMITS["max_wrong_effect_top1"],
    )

    # When both local profiles search the exact original utterances without structured hints.
    reports = {}
    for fixture in SUITES:
        snapshot = reference["suites"][fixture.name]
        suite = load_suite(fixture)
        assert suite.sha256 == snapshot["fixture_sha256"], fixture.name
        for profile in PROFILES:
            reports[fixture.name, profile] = await evaluate_profile(profile, suite)

    # Then preserve the measured bundled quality, model identity and read-only behavior.
    failures = {}
    for (name, profile), report in reports.items():
        snapshot = reference["suites"][name]["profiles"][profile]
        reasons = gate_failures(report, limits) + compare_bundled_reference(report, snapshot)
        models = set(report["semantic_models"])
        if COMBINED_MODEL_ID not in models and not {PRIMARY_ID, COVERAGE_ID} <= models:
            reasons.append("both bundled encoders must contribute to the evaluation")
        if reasons:
            failures[name, profile] = reasons
    assert not failures, failures


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_layer_number_queries_rank_the_readable_resource_first(profile):
    suite = load_suite(FIXTURE.with_name("universal_discovery_queries_v3.json"))
    requests = tuple(request for request in suite.requests if request.task == "layer_numbers")
    assert len(requests) == len({request.language for request in suite.requests}) == 8
    report = await evaluate_profile(profile, replace(suite, requests=requests))

    assert report["inventory_unchanged"]
    assert not report["unexpected_business_calls"]
    assert not report["native_dispatch_attempts"]
    assert all(row["availability"] == "available" and row["rank"] == 1 for row in report["rows"])
    assert report["summary"]["hidden_expected_route"] == 0


@pytest.mark.parametrize("suite_name", EFFECT_FIXTURES)
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_agent_catalog_exposes_distinct_public_read_and_edit_effects(suite_name, profile):
    suite = load_suite(FIXTURE.with_name(f"universal_discovery_effect_{suite_name}.json"))
    assert suite.sha256 == EFFECT_FIXTURES[suite_name]
    await assert_public_agent_contract(profile, suite)


@pytest.mark.parametrize("fixture", SCENE_FIXTURES)
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_agent_catalog_exposes_activation_and_competing_scene_operations(fixture, profile):
    suite = load_suite(FIXTURE.with_name(f"universal_discovery_{fixture}.json"))
    assert suite.sha256 == SCENE_FIXTURES[fixture]
    await assert_public_agent_contract(profile, suite)


def test_unadvertised_action_cannot_earn_rank_or_disappear_from_the_record():
    # Given a visible tool with only its read action available.
    request = Request("sv", "clear", "Töm kön nu.", frozenset({"queue.clear"}), frozenset(), None)
    inventory = Inventory(frozenset({"queue.get"}), frozenset(), frozenset({"queue"}))

    # When the search response nevertheless offers the unavailable action.
    row = score(
        request,
        {
            "query": request.query,
            "matching": "hybrid",
            "route_groups": [
                {"representative": {"tool": "queue", "operation": "clear"}, "alternatives": []}
            ],
        },
        inventory,
    )

    # Then it remains a measured bad suggestion without credit for the correct spelling.
    assert (row["availability"], row["rank"], row["group_rank"], row["hidden_expected_route"]) == (
        "unavailable",
        None,
        None,
        True,
    )
