"""Exact offline regression budget behavior on truthful saved evidence."""

import pytest
from cli.utils.play_scenario_soak_compare import SoakComparisonError, compare_soak_metrics

from cli.utils.play_scenario_soak_budget import check_soak_budget
from .play_scenario_soak_compare_fixtures import _change_reproduction, _change_samples
from .play_scenario_soak_budget_fixtures import ENVIRONMENT, _known_evidence, _policy


def test_budget_threshold_is_inclusive_for_observed_net_and_exact_slope(tmp_path):
    # Given: known matching environments and deltas exactly equal to explicit limits.
    baseline = _known_evidence(tmp_path / "left", [0, 100, 110, 120])
    candidate = _known_evidence(tmp_path / "right", [0, 200, 220, 240])
    policy = _policy(tmp_path / "policy.json")
    # When: the offline budget is checked.
    result = check_soak_budget(baseline, candidate, policy)
    # Then: equality is within budget without changing or reclassifying scenario results.
    assert result.status == "within_budget" and result.leak_verdict is False
    assert result.metrics[0].net_change_delta == 20
    assert result.metrics[0].slope_delta_exact == {"numerator": "10", "denominator": "1"}


@pytest.mark.parametrize("limit,expected", [(0, "budget_exceeded"), (1, "within_budget")])
def test_near_int64_slope_difference_never_uses_rounded_float_decisions(tmp_path, limit, expected):
    # Given: huge slopes whose floating representations lose a one-unit difference.
    baseline = _known_evidence(tmp_path / "left", [0, 0, 2**63 - 2])
    candidate = _known_evidence(tmp_path / "right", [0, 0, 2**63 - 1])
    policy = _policy(tmp_path / "policy.json", net=1, slope=limit)
    # When: exact regression limits are evaluated.
    result = check_soak_budget(baseline, candidate, policy)
    # Then: the threshold decision retains the one-unit change.
    assert result.status == expected
    assert result.metrics[0].slope_delta_exact == {"numerator": "1", "denominator": "1"}


@pytest.mark.parametrize(
    "token,expected", [("0.3", "within_budget"), ("0.299999999999999999", "budget_exceeded")]
)
def test_exact_decimal_limits_distinguish_equality_from_tiny_regression(tmp_path, token, expected):
    # Given: a slope of exactly three tenths and an explicitly serialized decimal limit.
    baseline = _known_evidence(tmp_path / "left", [0, 0, 0, 0, 0])
    candidate = _known_evidence(tmp_path / "right", [0, 0, 0, 0, 1])
    policy = _policy(tmp_path / "policy.json", net=1, slope=10)
    policy.write_text(
        policy.read_text().replace(
            '"max_slope_increase_per_iteration": 10', f'"max_slope_increase_per_iteration": {token}'
        )
    )
    # When: the decimal is compared with the exact fitted rational slope.
    result = check_soak_budget(baseline, candidate, policy)
    # Then: inclusive equality passes and a smaller exact limit fails.
    assert result.status == expected
    assert result.metrics[0].slope_delta_exact == {"numerator": "3", "denominator": "10"}


@pytest.mark.parametrize("values", [[0, 30, 20, 10], [0, 10, 10, 10]])
def test_negative_and_stable_growth_are_within_explicit_zero_increase_limits(tmp_path, values):
    # Given: a stable baseline and stable or declining candidate.
    baseline = _known_evidence(tmp_path / "left", [0, 30, 30, 30])
    candidate = _known_evidence(tmp_path / "right", values)
    policy = _policy(tmp_path / "policy.json", net=0, slope=0)
    # When/Then: nonpositive increases satisfy the explicit regression limits.
    assert check_soak_budget(baseline, candidate, policy).status == "within_budget"


def test_scattered_singletons_cannot_satisfy_slope_sample_minimum(tmp_path):
    # Given: thirty aligned values separated by unavailable samples.
    values = [0] + [value for index in range(30) for value in (index, None)]
    baseline = _known_evidence(tmp_path / "left", values)
    candidate = _known_evidence(tmp_path / "right", values)
    policy = _policy(tmp_path / "policy.json", paired=30)
    # When: the policy requires enough consecutive measured evidence.
    result = check_soak_budget(baseline, candidate, policy)
    # Then: isolated points are excluded and cannot invent a passing slope.
    assert result.status == "not_comparable"
    assert (
        result.metrics[0].paired_samples == 0
        and result.metrics[0].excluded_short_segment_samples == 30
    )
    assert "insufficient_consecutive_samples" in result.metrics[0].reasons


def test_short_segments_do_not_pad_minimum_paired_sample_count(tmp_path):
    # Given: only one two-point segment plus three isolated points.
    values = [0, 1, 2, None, 3, None, 4, None, 5]
    baseline = _known_evidence(tmp_path / "left", values)
    candidate = _known_evidence(tmp_path / "right", values)
    policy = _policy(tmp_path / "policy.json", paired=4)
    # When: the minimum applies to samples in eligible consecutive segments.
    result = check_soak_budget(baseline, candidate, policy)
    # Then: two eligible samples cannot pass a minimum of four.
    assert result.status == "not_comparable" and result.metrics[0].paired_samples == 2
    assert (
        result.metrics[0].aligned_samples == 5
        and result.metrics[0].excluded_short_segment_samples == 3
    )


def test_gaps_have_separate_intercepts_and_do_not_create_an_offset_growth_verdict(tmp_path):
    # Given: large offsets between two separately measured intervals.
    baseline = _known_evidence(tmp_path / "left", [0, 10, 20, None, 1000, 1010])
    candidate = _known_evidence(tmp_path / "right", [0, 20, 30, None, 2000, 2010])
    policy = _policy(tmp_path / "policy.json", net=1000, slope=0, paired=4)
    # When: both intervals contribute within their own intercepts.
    result = check_soak_budget(baseline, candidate, policy)
    # Then: within-interval slope remains equal without interpolating the gap.
    assert result.status == "within_budget" and result.metrics[0].eligible_segment_count == 2
    assert result.metrics[0].slope_delta_exact == {"numerator": "0", "denominator": "1"}


def test_selected_metric_only_does_not_require_unselected_unavailable_channels(tmp_path):
    # Given: only managed bytes are requested by the explicit policy.
    baseline = _known_evidence(tmp_path / "left", [0, 1, 2])
    candidate = _known_evidence(tmp_path / "right", [0, 1, 2])
    _change_samples(
        candidate, lambda row: row["metrics"][0].update(allocated_bytes=None, object_count=None)
    )
    policy = _policy(tmp_path / "policy.json", net=0, slope=0)
    # When/Then: unselected unavailable channels cannot block selected valid evidence.
    result = check_soak_budget(baseline, candidate, policy)
    assert result.status == "within_budget" and [metric.metric for metric in result.metrics] == [
        "managed_bytes"
    ]


def test_sampler_error_and_missing_values_remain_unavailable_to_budget(tmp_path):
    # Given: candidate errors and absent measurements remove otherwise aligned points.
    baseline = _known_evidence(tmp_path / "left", [0, 1, 2, 3, 4])
    candidate = _known_evidence(tmp_path / "right", [0, 1, 2, 3, None])
    _change_samples(
        candidate,
        lambda row: (
            row["metrics"][0].update(error="Unavailable") if row["iteration"] == 2 else None
        ),
    )
    policy = _policy(tmp_path / "policy.json", paired=3)
    # When/Then: only actual valid eligible samples count, with error/missing counts preserved.
    result = check_soak_budget(baseline, candidate, policy)
    assert result.status == "not_comparable" and result.metrics[0].paired_samples == 2
    assert (
        result.metrics[0].candidate_counts.invalid == 1
        and result.metrics[0].candidate_counts.missing == 1
    )


def test_recorded_environment_knowns_remove_unverified_diagnostic_claims(tmp_path):
    # Given: matching one-time native environment snapshots.
    baseline = _known_evidence(tmp_path / "left", [0, 1, 2])
    candidate = _known_evidence(tmp_path / "right", [0, 1, 2])
    # When: the unchanged diagnostic command compares the histories.
    result = compare_soak_metrics(baseline, candidate)
    # Then: canonical environment evidence is exposed without inventing flat legacy facts.
    assert result.status == "compared" and result.environment_unverified == []
    assert result.baseline["environment"] == ENVIRONMENT and result.baseline["platform"] is None


@pytest.mark.parametrize(
    "field",
    [
        "operating_system",
        "platform",
        "architecture",
        "scripting_backend",
        "build_mode",
        "runtime_version",
    ],
)
def test_unknown_environment_remains_diagnostic_but_cannot_pass_budget(tmp_path, field):
    # Given: one unavailable fact in both matching snapshots.
    baseline = _known_evidence(tmp_path / "left", [0, 1, 2])
    candidate = _known_evidence(tmp_path / "right", [0, 1, 2])
    for directory in (baseline, candidate):
        _change_reproduction(directory, environment={**ENVIRONMENT, field: None})
    policy = _policy(tmp_path / "policy.json")
    # When/Then: raw diagnostics expose unknown facts, while gating rejects them.
    assert (
        f"environment.{field}" in compare_soak_metrics(baseline, candidate).environment_unverified
    )
    with pytest.raises(SoakComparisonError):
        check_soak_budget(baseline, candidate, policy)


@pytest.mark.parametrize(
    "environment",
    [
        {**ENVIRONMENT, "schema_version": 2},
        {**ENVIRONMENT, "extra": True},
        {key: value for key, value in ENVIRONMENT.items() if key != "runtime_version"},
        {**ENVIRONMENT, "scripting_backend": "unknown"},
        {**ENVIRONMENT, "platform": True},
        {**ENVIRONMENT, "operating_system": "Windows\n11"},
        {**ENVIRONMENT, "runtime_version": "x" * 65},
    ],
)
def test_malformed_recorded_environment_is_rejected_before_any_comparison(tmp_path, environment):
    # Given: a claimed canonical snapshot violating its strict persisted schema.
    baseline = _known_evidence(tmp_path / "left", [0, 1, 2])
    candidate = _known_evidence(tmp_path / "right", [0, 1, 2])
    _change_reproduction(candidate, environment=environment)
    # When/Then: malformed evidence is not silently treated as legacy unknown.
    with pytest.raises(SoakComparisonError):
        compare_soak_metrics(baseline, candidate)
