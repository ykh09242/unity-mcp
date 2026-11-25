"""Guard the public evidence for comparing distinct read scopes in the agent catalog."""

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.discovery_evaluation_fixture import (
    FIXTURE,
    EvaluationSuite,
    Request,
    load_suite,
)
from tools.experiments.discovery_family_split import query_identity
from tools.experiments.universal_discovery_evaluation import PROFILES
from tools.tests.discovery_agent_contract import assert_public_agent_contract

SUITE = load_suite(FIXTURE.with_name("universal_discovery_read_scope.json"))
CONFIRMATION = load_suite(FIXTURE.with_name("universal_discovery_read_scope_confirmation.json"))
CATALOG_RECALL = load_suite(FIXTURE.with_name("universal_discovery_read_recall_confirmation.json"))

# Historical inputs to the retired ranking calibrations, retained as fixture provenance.
_CALIBRATION_SUITES = (
    FIXTURE.name,
    "universal_discovery_queries_v2.json",
    "universal_discovery_queries_v3.json",
    "universal_discovery_effect_pairs.json",
)


def test_scope_confirmation_is_outside_both_calibrations():
    assert SUITE.sha256 == "e41a771653167454c49a1c4c03f85b149d6d7a2a3da0e2e21fb6b1104f1d0f75"
    assert CONFIRMATION.sha256 == "db0940e98e3c88fe625f2d57fa59299bbfea205b638fb353f4038192363b70b7"
    assert (
        CATALOG_RECALL.sha256 == "cb2fd8781718d0bbdedda39db2f68309c3706d15edcf5e63b3595f58316c5a20"
    )
    pairs = json.loads(FIXTURE.with_name("observation_scope_pairs.json").read_bytes())
    training = {query_identity(t) for group in pairs.values() for pair in group for t in pair}
    training.update(
        query_identity(r.query)
        for name in _CALIBRATION_SUITES
        for r in load_suite(FIXTURE.with_name(name)).requests
    )
    for suite, size in ((SUITE, 8), (CONFIRMATION, 6), (CATALOG_RECALL, 8)):
        queries = {query_identity(r.query) for r in suite.requests}
        assert len(queries) == len(suite.requests) == 6 * size
        assert not training.intersection(queries)
        assert set(Counter(r.task for r in suite.requests).values()) == {size}
        training.update(queries)


@pytest.mark.parametrize(
    "suite",
    (SUITE, CONFIRMATION, CATALOG_RECALL),
    ids=("original", "confirmation", "catalog-recall"),
)
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_agent_catalog_exposes_both_public_read_scopes(profile, suite):
    await assert_public_agent_contract(profile, suite, shared_family=True)


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_agent_catalog_distinguishes_scope_and_subject_of_public_reads(profile):
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
    await assert_public_agent_contract(
        profile, EvaluationSuite("review-regressions", requests), shared_family=True
    )
