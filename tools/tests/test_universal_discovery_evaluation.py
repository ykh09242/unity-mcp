"""Guard lexical lookup and public agent evidence on the original discovery fixtures."""

from dataclasses import replace
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.discovery_evaluation_fixture import FIXTURE, Request, load_suite
from tools.experiments.universal_discovery_evaluation import (
    PROFILES,
    Inventory,
    evaluate_profile,
    score,
)
from tools.tests.discovery_agent_contract import BindingExclusions, assert_public_agent_contract

SUITES = {
    FIXTURE: "645d3fcd1924820fd62fa9320bfde872c8a6af10793f974b4cbd70f93fa8d841",
    FIXTURE.with_name("universal_discovery_queries_v2.json"): (
        "8c96401f5fb62cf3a385ec0e0b1683a53dc29187958e67f857fe308e49c9d797"
    ),
}
EFFECT_FIXTURES = {
    "pairs": "85cad694d2991c66d9112be9091af247643b16ac8e9509b9963c40ff13d1854c",
    "confirmation": "9ea7ea36e00b700d41e393c811bb00a85a6f0288866c58e21aeaaa17984ac8d3",
}
SCENE_FIXTURES = {
    "edit_holdout": "386f463cf63b26cfecacec9bd05f3b0ae13f0e99f3a6ab1558c81c0138cea559",
    "activation_confirmation": "f8f123c5b3891a669da184fac148df70728bcd8d658d966766c391d86ce17a91",
}
_UNDECLARED_BINDINGS = frozenset({"manage_shader.modify"})
_SHADER_GRAPH_TEMPLATE = "mcpforunity://shader/graph/{encoded_path}{?page_size,page_number}"


@pytest.mark.parametrize("fixture", SUITES, ids=lambda path: path.stem)
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_agent_catalog_exposes_profile_bindings_for_original_multilingual_requests(
    fixture, profile
):
    suite = load_suite(fixture)
    assert suite.sha256 == SUITES[fixture]
    catalog_only = frozenset()
    if profile == "core":
        # Discovery honors the parent shader toggle even though MCP lists the template.
        catalog_only = frozenset({_SHADER_GRAPH_TEMPLATE})

    await assert_public_agent_contract(
        profile, suite, exclusions=BindingExclusions(_UNDECLARED_BINDINGS, catalog_only)
    )


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.asyncio
async def test_layer_number_queries_use_lexical_resource_identifier_with_original_text(profile):
    suite = load_suite(FIXTURE.with_name("universal_discovery_queries_v3.json"))
    requests = tuple(request for request in suite.requests if request.task == "layer_numbers")
    assert len(requests) == len({request.language for request in suite.requests}) == 8
    hints = {
        request.query: {
            "catalog_query": "mcpforunity://project/layers",
            "route_surfaces": "resources",
        }
        for request in requests
    }
    report = await evaluate_profile(profile, replace(suite, requests=requests), hints=hints)

    assert report["inventory_unchanged"]
    assert not report["unexpected_business_calls"]
    assert not report["native_dispatch_attempts"]
    assert report["matching"] == {"lexical": len(requests)}
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
            "matching": "lexical",
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
