"""Capture the public MCP choices for one arm of the family-first trial."""

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys
from unittest.mock import patch

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import anyio
from services.registry import task_search

from tools.experiments import family_first_search, universal_discovery_evaluation as public
from tools.experiments.discovery_evaluation_fixture import FIXTURE, load_suite


HOLDOUT = FIXTURE.with_name("universal_discovery_family_first_holdout.json")
HOLDOUT_SHA256 = "8affd52855ed447aaa3355143b8055b696a0921476d4da5c8b3a72b01b8f43b7"


def candidate_search(query, capabilities, resources, unavailable, intent, surfaces):
    operations, effects = {}, {}
    catalog = task_search.semantic_documents(
        capabilities,
        resources,
        unavailable,
        intent,
        exact_query=query,
        surfaces=surfaces,
        operations=operations,
        effects=effects,
    )
    return (
        family_first_search.rank_families(query, catalog, operations, effects) if catalog else None
    )


def describe(route):
    return {
        "route": public.route_name(route),
        **{
            key: route[key]
            for key in ("purpose", "intent", "required_parameters", "observation")
            if route.get(key)
        },
    }


async def capture(profile, suite, candidate):
    results = []
    score = public.score

    def inspect(request, response, inventory):
        row = score(request, response, inventory)
        groups = [
            [describe(group["representative"]), *(describe(a) for a in group["alternatives"])]
            for group in response["route_groups"]
        ]
        if len(groups) > 3 or response["query"] != request.query:
            raise ValueError(
                "The public response did not preserve the requested limit and original"
            )
        results.append({"language": request.language, "task": request.task, "groups": groups})
        return row

    arm = (
        patch.object(task_search, "_semantic_catalog", candidate_search)
        if candidate
        else nullcontext()
    )
    with arm, patch.object(public, "score", inspect):
        report = await public.evaluate_profile(profile, suite)
    size = len(suite.requests)
    if (
        report["summary"]["available"] != size
        or report["summary"]["hidden_expected_route"]
        or not report["inventory_unchanged"]
        or report["unexpected_business_calls"]
        or report["native_dispatch_attempts"]
        or report["matching"] != {"hybrid": size}
        or len(results) != size
    ):
        raise ValueError(f"Incomplete or state-changing public replay: {profile}")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("shipping", "candidate"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite = load_suite(HOLDOUT)
    if suite.sha256 != HOLDOUT_SHA256:
        raise ValueError("Sealed corpus digest mismatch")
    profiles = {
        profile: anyio.run(capture, profile, suite, args.arm == "candidate")
        for profile in public.PROFILES
    }
    args.output.write_text(
        json.dumps(
            {"corpus": suite.sha256, "arm": args.arm, "profiles": profiles}, ensure_ascii=False
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
