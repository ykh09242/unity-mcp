"""Measure leave-one-family-out transfer on the advertised catalog without refitting in CI."""

from collections import Counter
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.registry import semantic_metric

from tools.experiments.discovery_evaluation_fixture import FIXTURE, load_suite
from tools.experiments.discovery_family_split import query_identity
from tools.experiments.evaluate_discovery_families import (
    FAMILIES,
    METRICS,
    metric_variant,
    prepare_fold,
    replay,
)
from tools.experiments.universal_discovery_evaluation import PROFILES

REFERENCE = json.loads((METRICS / "reference.json").read_bytes())


@pytest.fixture(scope="module")
def folds():
    return {name: prepare_fold(name, REFERENCE["suites"]) for name in FAMILIES}


def test_every_confirmation_query_is_assigned_once_and_folds_match_the_calibration_inputs(folds):
    measured = Counter(query_identity(r.query) for f in folds.values() for r in f.suite.requests)
    wanted = Counter(
        query_identity(r.query)
        for name in REFERENCE["suites"]
        for r in load_suite(FIXTURE.with_name(name)).requests
    )
    assert measured == wanted and set(wanted.values()) == {1}
    assert {name: f.training for name, f in folds.items()} == {
        name: ref["training"] for name, ref in REFERENCE["folds"].items()
    }
    digests = {ref["metric_sha256"] for ref in REFERENCE["folds"].values()}
    assert len(digests) == len(folds) and semantic_metric._SHA256 not in digests


def test_fold_projections_are_isolated_and_invalid_weights_fail_closed():
    shipped = semantic_metric._ARTIFACT, semantic_metric._SHA256
    fields = ("read", "Read the active scene.", ("Read the scene.",), "scene")
    original = semantic_metric._document(fields, 0)
    expected_digest = REFERENCE["folds"]["manage_scene"]["metric_sha256"]
    with metric_variant("manage_scene", "heldout", expected_digest):
        assert semantic_metric._document(fields, 0) != original
    assert semantic_metric._document(fields, 0) == original
    with pytest.raises(ValueError, match="Invalid heldout metric"):
        with metric_variant("manage_scene", "heldout", "0" * 64):
            pytest.fail("A corrupt benchmark must not fall back to shipped scores")
    assert (semantic_metric._ARTIFACT, semantic_metric._SHA256) == shipped
    assert semantic_metric._weights() is not None


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("profile", PROFILES)
def test_heldout_reads_and_edits_preserve_rank_and_opposite_effect_limits(folds, family, profile):
    ref = REFERENCE["folds"][family]
    report = replay(family, folds[family], "heldout", profile, ref["metric_sha256"])
    for effect, scores in report["directions"].items():
        bounds = ref["heldout"][profile][effect]
        assert scores["count"] == bounds["count"]
        for key in ("top1", "top3"):
            assert scores[key] >= bounds[key], (family, profile, effect, scores)
        for key in ("wrong_effect_top1", "wrong_effect_top3"):
            assert scores[key] <= bounds[key], (family, profile, effect, scores)
