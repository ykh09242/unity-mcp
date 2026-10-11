"""Aligned diagnostic trend calculations on admitted offline evidence."""

import pytest

from cli.utils.play_scenario_soak_compare import SoakComparisonError, compare_soak_metrics
from .play_scenario_soak_compare_fixtures import (
    _evidence,
    _change,
    _change_reproduction,
    _change_samples,
)


def test_offline_comparison_reports_actual_post_warmup_slope_and_delta(tmp_path):
    # Given: compatible measured histories with different build labels and warmup spikes.
    baseline = _evidence(tmp_path / "baseline", [9999, 100, 110, 120])
    candidate = _evidence(tmp_path / "candidate", [8888, 200, 220, 240], revision="candidate-build")
    # When: both saved histories are compared without launching a Player.
    result = compare_soak_metrics(baseline, candidate)
    # Then: actual post-warmup points determine diagnostic slopes and deltas.
    metric = result.metrics[0]
    assert result.status == "compared" and result.diagnostic_only is True
    assert metric.baseline.first == 100 and metric.baseline.last == 120
    assert metric.baseline.slope_per_iteration == 10
    assert metric.candidate.slope_per_iteration == 20
    assert metric.slope_delta == 10 and metric.net_change_delta == 20


@pytest.mark.parametrize(
    "values,slope,net",
    [
        ([999, 30, 20, 10], -10, -20),
        ([999, 10, 10, 10], 0, 0),
        ([999, 2**63 - 4, 2**63 - 3, 2**63 - 2], 1, 2),
    ],
)
def test_negative_stable_and_near_int64_growth_preserve_exact_differences(
    tmp_path, values, slope, net
):
    baseline = _evidence(tmp_path / "left", values)
    candidate = _evidence(tmp_path / "right", values)
    metric = compare_soak_metrics(baseline, candidate).metrics[0]
    assert metric.baseline.slope_per_iteration == slope
    assert metric.baseline.net_change == net and metric.slope_delta == 0


def test_gaps_never_interpolate_or_join_offset_segments(tmp_path):
    baseline = _evidence(tmp_path / "left", [0, 10, 20, None, 1000, 1010])
    candidate = _evidence(tmp_path / "right", [0, 20, 40, None, 5000, 5020])
    metric = compare_soak_metrics(baseline, candidate).metrics[0]
    assert metric.paired_samples == 4 and metric.segment_count == 2
    assert metric.baseline.slope_per_iteration == 10 and metric.candidate.slope_per_iteration == 20
    assert metric.baseline_counts.missing == 1 and metric.baseline_counts.excluded_warmup == 1
    assert [(segment.first_iteration, segment.last_iteration) for segment in metric.segments] == [
        (2, 3),
        (5, 6),
    ]


@pytest.mark.parametrize("values", [[0, None, None], [0, 3, None], [0, 3, None, 7]])
def test_no_aligned_consecutive_pair_is_explicitly_insufficient(tmp_path, values):
    baseline = _evidence(tmp_path / "left", values)
    candidate = _evidence(tmp_path / "right", values)
    result = compare_soak_metrics(baseline, candidate)
    assert result.status == "insufficient_evidence"
    assert all(metric.slope_delta is None for metric in result.metrics)
    if values[1] is None:
        assert (
            result.metrics[0].baseline.first is None
            and result.metrics[0].baseline.net_change is None
        )


def test_unpaired_and_failed_samples_are_counted_without_zero_fabrication(tmp_path):
    baseline = _evidence(tmp_path / "left", [0, 1, 2, 3, 4])
    candidate = _evidence(tmp_path / "right", [0, 1, 2, 3, None])
    _change_samples(
        candidate,
        lambda row: (
            row["metrics"][0].update(error="Sampler unavailable") if row["iteration"] == 2 else None
        ),
    )
    metric = compare_soak_metrics(baseline, candidate).metrics[0]
    assert metric.paired_samples == 2 and metric.baseline_counts.unpaired == 2
    assert metric.candidate_counts.invalid == 1 and metric.candidate_counts.missing == 1
    assert metric.candidate.first == 2 and metric.candidate.last == 3


def test_optional_environment_unknowns_and_different_builds_remain_visible(tmp_path):
    baseline = _evidence(tmp_path / "left", [0, 1, 2], environment={"platform": "WindowsPlayer"})
    candidate = _evidence(tmp_path / "right", [0, 1, 2], revision="other")
    result = compare_soak_metrics(baseline, candidate)
    assert (
        result.baseline["build_source_revision"] == "baseline-build"
        and result.candidate["build_source_revision"] == "other"
    )
    assert result.baseline["platform"] == "WindowsPlayer" and result.candidate["platform"] is None
    assert "platform" in result.environment_unverified


def test_segment_details_are_bounded_and_all_points_still_contribute(tmp_path):
    values = [0] + [value for index in range(20) for value in (index * 100, index * 100 + 10, None)]
    baseline = _evidence(tmp_path / "left", values)
    candidate = _evidence(tmp_path / "right", values)
    metric = compare_soak_metrics(baseline, candidate).metrics[0]
    assert (
        metric.segment_count == 20 and metric.omitted_segments == 4 and len(metric.segments) == 16
    )
    assert metric.paired_samples == 40 and metric.baseline.slope_per_iteration == 10


def test_all_warmup_samples_produce_insufficient_evidence(tmp_path):
    # Given: every recorded sample is excluded by the frozen warmup policy.
    baseline = _evidence(tmp_path / "left", [1, 2, 3], warmup=3)
    candidate = _evidence(tmp_path / "right", [2, 3, 4], warmup=3)
    # When: the histories are compared.
    result = compare_soak_metrics(baseline, candidate)
    # Then: no metric invents an available value or trend.
    assert result.status == "insufficient_evidence"
    assert all(
        metric.paired_samples == 0 and metric.baseline_counts.excluded_warmup == 3
        for metric in result.metrics
    )


def test_absent_boundary_sample_stays_missing(tmp_path):
    # Given: a successfully finalized iteration whose sampler produced no boundary sample.
    baseline = _evidence(tmp_path / "left", [0, 1, 2, 3, 4])
    candidate = _evidence(tmp_path / "right", [0, 1, 2, 3, 4])
    _change_samples(
        candidate, lambda row: row.update(metrics=None) if row["iteration"] == 2 else None
    )
    # When: the remaining aligned measured samples are compared.
    metric = compare_soak_metrics(baseline, candidate).metrics[0]
    # Then: the absent sample counts as missing and cannot become zero.
    assert metric.paired_samples == 3 and metric.candidate_counts.missing == 1
    assert metric.candidate.first == 2 and metric.candidate.slope_per_iteration == 1
