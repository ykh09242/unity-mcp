"""Replay raw read and edit requests with one operation family excluded from calibration."""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
from unittest.mock import patch

if not __package__:
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root / "Server/src"), str(root)]

import anyio
from services.registry import semantic_coverage, semantic_encoder, semantic_metric, task_search
from services.registry.action_catalog import TOOL_GUIDANCE, operation_owner
from services.registry.tool_registry import FIXED_OBSERVATION_ACTIONS

from tools.experiments.discovery_evaluation_fixture import FIXTURE, EvaluationSuite, load_suite
from tools.experiments.discovery_family_split import belongs_to, query_identity
from tools.experiments.fit_discovery_metric import training_inputs
from tools.experiments.universal_discovery_evaluation import PROFILES, evaluate_profile, summarize

METRICS = Path(__file__).with_name("discovery_family_metrics")
FAMILIES = ("manage_scene", "manage_editor")
VARIANTS = ("unadapted", "heldout", "shipped")
COUNTS = ("count", "top1", "top3", "wrong_effect_top1", "wrong_effect_top3")


def digest(value):
    return sha256(json.dumps(value, separators=(",", ":")).encode("ascii")).hexdigest()


def direction(route):
    if "://" in route:
        return "observe"
    name, _, selector = route.partition(".")
    name, selector = FIXED_OBSERVATION_ACTIONS.get(name, (name, selector))
    owner = operation_owner(name, selector)
    guidance = TOOL_GUIDANCE.get(owner)
    action = guidance.actions.get(selector) if guidance else None
    if not action or action.intent not in ("observe", "edit"):
        raise ValueError(f"Missing read/edit declaration for {route}")
    return action.intent


@dataclass(frozen=True)
class Fold:
    suite: EvaluationSuite
    directions: tuple[str, ...]
    training: dict


def prepare_fold(family, sources):
    effects = {}
    documents, operations, training, held = training_inputs(family, effects=effects)
    training_queries = {query_identity(r.query) for r in training}
    selection = digest(
        [
            [semantic_encoder._SHA256, semantic_coverage._SHA256, semantic_metric._SHA256],
            sorted(held),
            [
                [
                    key,
                    texts,
                    semantic_metric.document_fields(key, texts, operations.get(key)),
                    effects[key],
                ]
                for key, texts in sorted(documents.items())
            ],
            [[r.query, sorted(r.expected), sorted(r.wrong_effect)] for r in training],
        ]
    )
    cases, directions, seen = [], [], set()
    for filename, checksum in sources.items():
        suite = load_suite(FIXTURE.with_name(filename))
        if suite.sha256 != checksum:
            raise ValueError(f"Evaluation corpus digest mismatch: {filename}")
        for request in suite.requests:
            members = {r for r in request.expected if r in held or belongs_to(r, family)}
            if not members:
                continue
            effects = {direction(r) for r in members}
            if members != request.expected or len(effects) != 1:
                raise ValueError(f"Ambiguous family or effect: {request.task}")
            identity = query_identity(request.query)
            if identity in training_queries or identity in seen:
                raise ValueError(f"Training overlap or duplicate evaluation query: {request.task}")
            seen.add(identity)
            cases.append(request)
            directions.append(effects.pop())
    if set(directions) != {"observe", "edit"}:
        raise ValueError(f"Missing evaluation direction for {family}")
    return Fold(
        EvaluationSuite(digest([family, sources]), tuple(cases)),
        tuple(directions),
        {"declarations": len(documents), "requests": len(training), "selection_sha256": selection},
    )


@contextmanager
def metric_variant(family, variant, expected_digest):
    with ExitStack() as stack:
        if variant == "heldout":
            stack.enter_context(
                patch.multiple(
                    semantic_metric, _ARTIFACT=METRICS / f"{family}.q8", _SHA256=expected_digest
                )
            )
        elif variant == "unadapted":
            stack.enter_context(
                patch.object(
                    task_search,
                    "recall_operation",
                    lambda query, docs, ops, base, canonical, effects=None: canonical,
                )
            )
        elif variant != "shipped":
            raise ValueError(f"Unknown metric variant: {variant}")
        semantic_metric._weights.cache_clear()
        semantic_metric._document.cache_clear()
        try:
            if variant != "unadapted" and semantic_metric._weights() is None:
                raise ValueError(f"Invalid {variant} metric for {family}")
            yield
        finally:
            semantic_metric._weights.cache_clear()
            semantic_metric._document.cache_clear()


def replay(family, fold, variant, profile, expected_digest):
    with metric_variant(family, variant, expected_digest):
        report = anyio.run(evaluate_profile, profile, fold.suite)
    summary = report["summary"]
    models = report["semantic_models"]
    bundled_models = {
        semantic_encoder.MODEL_ID,
        semantic_coverage.MODEL_ID,
        semantic_encoder.COMBINED_MODEL_ID,
    }
    if (
        not report["inventory_unchanged"]
        or report["unexpected_business_calls"]
        or report["native_dispatch_attempts"]
        or summary["hidden_expected_route"]
        or summary["available"] != len(fold.suite.requests)
        or report["matching"] != {"hybrid": len(fold.suite.requests)}
        or not models.keys() <= bundled_models
        or sum(models.values()) != len(fold.suite.requests)
    ):
        raise ValueError(f"Incomplete or state-changing replay for {family}/{variant}/{profile}")
    directions = {}
    for effect in ("observe", "edit"):
        rows = [r for r, d in zip(report["rows"], fold.directions, strict=True) if d == effect]
        metrics = summarize(rows)
        directions[effect] = {key: metrics[key] for key in COUNTS}
    return {
        "directions": directions,
        "languages": report["languages"],
        "semantic_models": models,
        "rows": report["rows"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=FAMILIES, action="append")
    parser.add_argument("--variant", choices=VARIANTS, action="append")
    parser.add_argument("--profile", choices=PROFILES, action="append")
    parser.add_argument(
        "--output", type=Path, help="Write counts, language metrics and route ranks."
    )
    args = parser.parse_args()
    reference = json.loads((METRICS / "reference.json").read_bytes())
    results = {}
    for family in args.family or FAMILIES:
        fold = prepare_fold(family, reference["suites"])
        baseline = reference["folds"][family]
        if baseline["training"] != fold.training:
            raise ValueError(f"Calibration inputs changed for {family}: {fold.training}")
        for variant in args.variant or VARIANTS:
            for profile in args.profile or PROFILES:
                report = replay(family, fold, variant, profile, baseline["metric_sha256"])
                label = f"{family}/{variant}/{profile}"
                print(label, json.dumps(report["directions"]), flush=True)
                results[label] = report
    if args.output:
        evidence = {
            "suites": reference["suites"],
            "folds": {f: reference["folds"][f] for f in args.family or FAMILIES},
            "shipped_metric_sha256": semantic_metric._SHA256,
            "results": results,
        }
        args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
