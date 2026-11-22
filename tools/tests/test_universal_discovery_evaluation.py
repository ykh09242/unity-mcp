"""Guard the shipped bundled ranking separately from the absolute evaluation targets."""

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.registry.semantic_coverage import MODEL_ID as COVERAGE_ID
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

REFERENCE = FIXTURE.with_name("universal_discovery_bundled_reference.json")
SUITES = (FIXTURE, FIXTURE.with_name("universal_discovery_queries_v2.json"))


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
async def test_raw_multilingual_ranking_does_not_regress_from_the_shipped_bundle(
    monkeypatch, tmp_path
):
    # Given the two frozen corpora and the independent measurement of the shipped ranking.
    reference = json.loads(REFERENCE.read_bytes())
    monkeypatch.setenv("UNITY_MCP_SEARCH_MODEL_DIR", str(tmp_path / "unprovisioned"))
    limits = argparse.Namespace(
        require_matching="hybrid",
        require_model=True,
        expect_model=(PRIMARY_ID, COVERAGE_ID),
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
        if set(report["semantic_models"]) != {PRIMARY_ID, COVERAGE_ID}:
            reasons.append("both bundled model paths must be exercised")
        if reasons:
            failures[name, profile] = reasons
    assert not failures, failures


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
