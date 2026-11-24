"""Guard raw discovery and isolate requested cardinality on real registered reads."""

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.registry.action_catalog import TOOL_GUIDANCE, operation_owner
from services.registry.semantic_catalog import semantic_scores
from services.registry.semantic_operations import Operation
from services.registry.semantic_read_scope import rank_observations

from tools.experiments.discovery_evaluation_fixture import (
    FIXTURE,
    EvaluationSuite,
    Request,
    load_suite,
)
from tools.experiments.discovery_family_split import query_identity
from tools.experiments.fit_discovery_metric import TRAINING
from tools.experiments.fit_observation_scope import PAIRS
from tools.experiments.universal_discovery_evaluation import PROFILES, evaluate_profile

SUITE = load_suite(FIXTURE.with_name("universal_discovery_read_scope.json"))
CONFIRMATION = load_suite(FIXTURE.with_name("universal_discovery_read_scope_confirmation.json"))
CATALOG_RECALL = load_suite(FIXTURE.with_name("universal_discovery_read_recall_confirmation.json"))

# Per task and profile: correct first, correct in the first three, and opposite first.
REGRESSION_LIMITS = {
    "current_scene": ((2, 3, 3), (3, 3, 3)),
    "scene_list": ((7, 7, 0), (7, 7, 0)),
    "installed_package": ((0, 4, 0), (0, 4, 0)),
    "package_list": ((3, 5, 0), (3, 5, 0)),
    "selected_agent": ((0, 2, 0), (0, 3, 0)),
    "agent_list": ((1, 6, 0), (1, 5, 0)),
}
CONFIRMATION_LIMITS = {
    "current_scene": ((1, 2, 2), (1, 2, 1)),
    "scene_list": ((2, 4, 0), (2, 4, 0)),
    "installed_package": ((5, 6, 0), (5, 6, 0)),
    "package_list": ((1, 3, 0), (1, 3, 0)),
    "selected_agent": ((1, 2, 0), (0, 2, 0)),
    "agent_list": ((2, 4, 0), (1, 4, 0)),
}
CATALOG_RECALL_LIMITS = {
    "current_scene": ((4, 5, 2), (3, 3, 1)),
    "scene_list": ((5, 7, 0), (5, 6, 0)),
    "installed_package": ((2, 2, 0), (2, 2, 0)),
    "package_list": ((0, 0, 2), (0, 0, 2)),
    "selected_agent": ((2, 4, 0), (2, 4, 0)),
    "agent_list": ((0, 2, 0), (0, 2, 0)),
}


def test_scope_confirmation_is_outside_both_calibrations():
    assert SUITE.sha256 == "e41a771653167454c49a1c4c03f85b149d6d7a2a3da0e2e21fb6b1104f1d0f75"
    assert CONFIRMATION.sha256 == "db0940e98e3c88fe625f2d57fa59299bbfea205b638fb353f4038192363b70b7"
    assert (
        CATALOG_RECALL.sha256 == "cb2fd8781718d0bbdedda39db2f68309c3706d15edcf5e63b3595f58316c5a20"
    )
    pairs = json.loads(PAIRS.read_bytes())
    training = {query_identity(t) for group in pairs.values() for pair in group for t in pair}
    training.update(
        query_identity(r.query)
        for name in TRAINING
        for r in load_suite(FIXTURE.with_name(name)).requests
    )
    for suite, size in ((SUITE, 8), (CONFIRMATION, 6), (CATALOG_RECALL, 8)):
        queries = {query_identity(r.query) for r in suite.requests}
        assert len(queries) == len(suite.requests) == 6 * size
        assert not training.intersection(queries)
        assert set(Counter(r.task for r in suite.requests).values()) == {size}
        training.update(queries)


@pytest.mark.parametrize(
    ("suite", "limits"),
    [
        (SUITE, REGRESSION_LIMITS),
        (CONFIRMATION, CONFIRMATION_LIMITS),
        (CATALOG_RECALL, CATALOG_RECALL_LIMITS),
    ],
    ids=("original", "confirmation", "catalog-recall"),
)
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_public_raw_lookup_scope(profile, suite, limits):
    report = await evaluate_profile(profile, suite)
    assert (
        report["inventory_unchanged"]
        and not report["unexpected_business_calls"]
        and not report["native_dispatch_attempts"]
    )
    assert report["summary"]["available"] == len(suite.requests)
    assert not report["summary"]["hidden_expected_route"]
    assert report["matching"] == {"hybrid": len(suite.requests)}
    if suite is CATALOG_RECALL:
        assert report["summary"]["wrong_effect_top3"] <= (13, 11)[PROFILES.index(profile)]
    assert set(report["tasks"]) == set(limits)
    for task, metrics in report["tasks"].items():
        top1, top3, opposite = limits[task][PROFILES.index(profile)]
        assert metrics["top1"] >= top1 and metrics["top3"] >= top3, (task, metrics)
        assert metrics["wrong_effect_top1"] <= opposite, (task, metrics)


def _binding(routes):
    bindings = set()
    for route in routes:
        tool, _, selector = route.partition(".")
        bindings.add(("tool", operation_owner(tool, selector), selector))
    assert len(bindings) == 1
    return bindings.pop()


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_raw_search_preserves_the_scope_and_subject_of_the_answer(profile):
    examples = {r.task: r for r in CONFIRMATION.requests}
    requests = (
        replace(examples["current_scene"], language="ko", query="현재 활성 씬이 뭐야?"),
        replace(examples["current_scene"], language="ko", query="지금 활성 씬이 뭐야?"),
        replace(
            examples["package_list"], language="ko", query="프로젝트에 설치된 패키지 목록 보여줘"
        ),
        replace(
            examples["scene_list"],
            language="en",
            query="List my loaded scenes and mark the active one.",
        ),
        replace(
            examples["scene_list"],
            language="en",
            query="Which scenes are open and which one is active?",
        ),
        Request(
            "en",
            "package_job",
            "What is the state of my package installation job?",
            frozenset({"manage_packages.status"}),
            frozenset({"manage_packages.get_package_info"}),
            None,
        ),
    )
    report = await evaluate_profile(profile, EvaluationSuite("review-regressions", requests))
    for row, limit in zip(report["rows"], (1, 1, 1, 1, 1, 2), strict=True):
        assert row["rank"] is not None and row["rank"] <= limit, row
        assert not row["wrong_effect_top1"], row


def test_registered_read_pairs_can_correct_the_wrong_scope_and_preserve_the_right_one():
    """Keep the subject family fixed and put each possible read first in turn."""
    counts = {}
    for request in SUITE.requests:
        correct, wrong = (_binding(routes) for routes in (request.expected, request.wrong_effect))
        docs, ops = {}, {}
        for key in (correct, wrong):
            _, owner, selector = key
            card = TOOL_GUIDANCE[owner].actions[selector]
            assert card.intent == "observe"
            ops[key] = Operation(
                owner, selector, card.purpose, tuple(card.search_examples), card.required
            )
            docs[key] = (card.purpose,)
        model, _ = semantic_scores(request.query, docs)
        result = counts.setdefault(request.task, {"recovered": 0, "preserved": 0})
        for leader, runner, metric in (
            (wrong, correct, "recovered"),
            (correct, wrong, "preserved"),
        ):
            baseline = (model, {leader: 0.65, runner: 0.60})
            _, scores = rank_observations(
                request.query, docs, ops, dict.fromkeys(docs, "observe"), baseline
            )
            result[metric] += max(scores, key=scores.__getitem__) == correct
    limits = {
        "current_scene": (4, 7),
        "scene_list": (4, 8),
        "installed_package": (5, 8),
        "package_list": (3, 7),
        "selected_agent": (3, 8),
        "agent_list": (6, 8),
    }
    assert counts.keys() == limits.keys()
    for task, (recovered, preserved) in limits.items():
        assert counts[task]["recovered"] >= recovered and counts[task]["preserved"] >= preserved, (
            task
        )
