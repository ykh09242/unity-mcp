"""Apply explicit regression budgets to compatible saved soak measurements."""

from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from cli.utils.play_scenario_soak_budget_policy import MetricBudget, read_budget_policy
from cli.utils.play_scenario_soak_compare import (
    MetricStatistics,
    SampleCounts,
    SoakComparisonError,
    _read_history,
    _segments,
    _statistics,
    _values,
    exact_slope,
)
from cli.utils.play_scenario_soak_compare_evidence import SavedSoak, require_compatible


@dataclass(frozen=True, slots=True)
class BudgetMetricResult:
    metric: str
    status: Literal["within_budget", "budget_exceeded", "not_comparable"]
    reasons: tuple[str, ...]
    aligned_samples: int
    paired_samples: int
    baseline_counts: SampleCounts
    candidate_counts: SampleCounts
    excluded_short_segment_samples: int
    longest_consecutive_samples: int
    segment_count: int
    eligible_segment_count: int
    baseline: MetricStatistics
    candidate: MetricStatistics
    net_change_delta: int | None
    slope_delta_exact: dict[str, str] | None
    limits: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SoakBudgetResult:
    status: Literal["within_budget", "budget_exceeded", "not_comparable"]
    baseline: dict[str, JsonValue]
    candidate: dict[str, JsonValue]
    policy_sha256: str
    metrics: tuple[BudgetMetricResult, ...]
    schema_version: int = 1
    environment_verified: bool = True
    leak_verdict: bool = False
    interpretation: str = "Regression budget decision only; original scenario results remain unchanged. Growth does not establish a leak."

    def to_dict(self) -> dict[str, JsonValue]:
        return asdict(self)


def _check_metric(
    left: SavedSoak, right: SavedSoak, metric: str, budget: MetricBudget
) -> BudgetMetricResult:
    left_values, left_counts = _values(left, metric)
    right_values, right_counts = _values(right, metric)
    aligned = sorted(left_values.keys() & right_values.keys())
    segments = _segments(aligned)
    eligible = [segment for segment in segments if len(segment) >= budget.min_consecutive_samples]
    paired = sum(len(segment) for segment in eligible)
    left_stats, right_stats = (
        _statistics(left_values, eligible),
        _statistics(right_values, eligible),
    )
    reasons = []
    if not eligible:
        reasons.append("insufficient_consecutive_samples")
    if paired < budget.min_paired_samples:
        reasons.append("insufficient_paired_samples")
    left_slope, right_slope = (
        exact_slope(left_values, eligible),
        exact_slope(right_values, eligible),
    )
    slope_delta = (
        right_slope - left_slope if left_slope is not None and right_slope is not None else None
    )
    net_delta = (
        right_stats.net_change - left_stats.net_change
        if left_stats.net_change is not None and right_stats.net_change is not None
        else None
    )
    if reasons:
        status = "not_comparable"
    else:
        if net_delta is not None and net_delta > budget.max_net_change_increase:
            reasons.append("net_change_increase_exceeded")
        if slope_delta is not None and slope_delta > Fraction(
            budget.max_slope_increase_per_iteration
        ):
            reasons.append("slope_increase_exceeded")
        status = "budget_exceeded" if reasons else "within_budget"
    return BudgetMetricResult(
        metric,
        status,
        tuple(reasons),
        len(aligned),
        paired,
        replace(left_counts, unpaired=len(left_values) - len(aligned)),
        replace(right_counts, unpaired=len(right_values) - len(aligned)),
        len(aligned) - paired,
        max((len(segment) for segment in segments), default=0),
        len(segments),
        len(eligible),
        left_stats,
        right_stats,
        net_delta,
        {"numerator": str(slope_delta.numerator), "denominator": str(slope_delta.denominator)}
        if slope_delta is not None
        else None,
        budget.model_dump(mode="json"),
    )


def check_soak_budget(baseline: Path, candidate: Path, policy_path: Path) -> SoakBudgetResult:
    """Gate selected measurements exactly; incomplete/unknown conditions never imply pass."""
    try:
        saved = read_budget_policy(policy_path)
    except (OSError, ValueError, RecursionError) as exc:
        raise SoakComparisonError(
            f"Invalid budget policy: {' '.join(str(exc).split())[:512]}"
        ) from exc
    left, right = _read_history(baseline, "baseline"), _read_history(candidate, "candidate")
    try:
        require_compatible(left, right)
        for history in (left, right):
            if history.environment.environment is None:
                raise SoakComparisonError(
                    "Budget requires a recorded schema-1 environment snapshot"
                )
            history.environment.environment.require_budget_support()
    except ValueError as exc:
        raise SoakComparisonError(str(exc)) from exc
    metrics = tuple(
        _check_metric(left, right, metric, budget)
        for metric, budget in saved.policy.metrics.items()
    )
    status = (
        "not_comparable"
        if any(metric.status == "not_comparable" for metric in metrics)
        else "budget_exceeded"
        if any(metric.status == "budget_exceeded" for metric in metrics)
        else "within_budget"
    )
    return SoakBudgetResult(status, left.provenance(), right.provenance(), saved.digest, metrics)
