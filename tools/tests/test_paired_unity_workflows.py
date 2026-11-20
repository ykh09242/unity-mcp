"""Unmatched or unavailable native arms cannot enter the benchmark denominators."""

import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.paired_unity_workflows import compare


def trial_pair():
    initial = {
        "status": "completed",
        "execution": "native",
        "unity_version": "6000.0.69f1",
        "model": "model",
        "effort": "medium",
        "host": "host@1",
        "product_identity": "fork@revision",
        "fixture_digest": "a" * 64,
        "permission_digest": "b" * 64,
        "prompt_digest": "c" * 64,
        "observer_digest": "d" * 64,
        "fixture_run_id": "fork-fixture",
        "actor_claimed_success": True,
        "mcp_calls": 4,
        "shell_calls": 0,
        "duplicate_writes": 0,
        "elapsed_seconds": 1.5,
        "independent": {
            "coverage": "complete",
            "fixture_run_id": "fork-fixture",
            "artifact_digest": "e" * 64,
            "facts": {
                "input_mode": "pointer",
                "blocked_target_first": False,
                "repaired_target_first": True,
                "blocked_transitions": 0,
                "repaired_transitions": 1,
            },
        },
    }
    official = deepcopy(initial)
    official.update(
        product_identity="official@revision",
        unity_cli="1.0.1",
        pipeline_package="1.0.0",
        fixture_run_id="official-fixture",
    )
    official["independent"].update(fixture_run_id="official-fixture", artifact_digest="f" * 64)
    return {
        "task": "blocked-click",
        "mode": "mcp-only",
        "repetition": 1,
        "fork": initial,
        "official": official,
    }


def test_scores_observed_pointer_outcome_and_rejects_direct_dispatched_claim():
    pair = trial_pair()
    pair["official"]["independent"]["facts"]["input_mode"] = "direct"
    pair["official"]["duplicate_writes"] = 2
    pair["official"]["tokens"] = 100
    summary = compare([pair])["summary"]["mcp-only"]
    assert summary["scored"] == 1
    assert summary["arms"]["fork"]["successes"] == 1
    assert summary["arms"]["fork"]["tokens"] is None
    assert summary["arms"]["official"]["false_passes"] == 1
    assert summary["arms"]["official"]["duplicate_writes"] == 2
    assert summary["arms"]["official"]["tokens"] == 100


def test_unavailable_official_arm_has_no_score():
    pair = trial_pair()
    pair["official"] = {"status": "unavailable", "reason": "no_pipeline_editor"}
    report = compare([pair])
    assert report["summary"]["mcp-only"] == {"planned": 1, "scored": 0, "arms": None}
    assert report["pairs"][0]["scores"] is None
    assert report["pairs"][0]["unscored_reasons"] == {"official": "no_pipeline_editor"}


def test_different_permissions_and_shared_witness_are_incomparable():
    pair = trial_pair()
    pair["official"]["permission_digest"] = "e" * 64
    pair["official"]["independent"]["artifact_digest"] = "e" * 64
    result = compare([pair])["pairs"][0]
    assert result["scores"] is None
    assert result["unscored_reasons"] == {
        "permission_digest": "control_mismatch",
        "artifact_digest": "shared_witness",
    }


def test_reusing_native_runs_across_repetitions_and_modes_withholds_all_duplicate_pairs():
    original = trial_pair()
    repeated = deepcopy(original)
    repeated["repetition"] = 2
    shell = deepcopy(original)
    shell["mode"] = "shell-enabled"
    fresh = deepcopy(shell)
    fresh["repetition"] = 2
    # A different fixture label still cannot supply a second independent observation.
    for arm, artifact in (("fork", "a"), ("official", "b")):
        shell[arm]["fixture_run_id"] += "-renamed"
        shell[arm]["independent"]["fixture_run_id"] = shell[arm]["fixture_run_id"]
        fresh[arm]["fixture_run_id"] += "-fresh"
        fresh[arm]["independent"].update(
            fixture_run_id=fresh[arm]["fixture_run_id"], artifact_digest=artifact * 64
        )

    report = compare([original, repeated, shell, fresh])
    assert report["summary"]["mcp-only"] == {"planned": 2, "scored": 0, "arms": None}
    assert report["summary"]["shell-enabled"]["planned"] == 2
    assert report["summary"]["shell-enabled"]["scored"] == 1
    for pair in report["pairs"][:2]:
        assert pair["unscored_reasons"] == {
            "fixture_run_id": "reused_across_pairs",
            "artifact_digest": "reused_across_pairs",
        }
    assert report["pairs"][2]["unscored_reasons"] == {"artifact_digest": "reused_across_pairs"}
    assert report["pairs"][3]["scores"]["official"]["independent_pass"]
