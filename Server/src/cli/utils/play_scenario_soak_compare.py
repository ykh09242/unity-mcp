"""Compare bounded aligned soak diagnostics without interpolation or leak verdicts."""

from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Final, Literal

from pydantic import JsonValue

from cli.utils.play_scenario_player import PlayerLaunchError
from cli.utils.play_scenario_soak_compare_evidence import (
    SavedSoak,
    read_saved_soak,
    require_compatible,
)

METRICS: Final = ("managed_bytes", "allocated_bytes", "object_count")
SEGMENT_DETAILS_LIMIT: Final = 16


class SoakComparisonError(ValueError):
    """Saved artifacts cannot establish a compatible complete comparison."""


@dataclass(frozen=True, slots=True)
class MetricStatistics:
    first: int | None
    last: int | None
    minimum: int | None
    maximum: int | None
    net_change: int | None
    slope_per_iteration: float | None


@dataclass(frozen=True, slots=True)
class SampleCounts:
    available: int
    missing: int
    invalid: int
    excluded_warmup: int
    unpaired: int


@dataclass(frozen=True, slots=True)
class MetricSegment:
    first_iteration: int
    last_iteration: int
    sample_count: int
    baseline: MetricStatistics
    candidate: MetricStatistics


@dataclass(frozen=True, slots=True)
class MetricComparison:
    metric: str
    state: Literal["compared", "insufficient_samples", "unavailable"]
    paired_samples: int
    baseline_counts: SampleCounts
    candidate_counts: SampleCounts
    baseline: MetricStatistics
    candidate: MetricStatistics
    slope_delta: float | None
    net_change_delta: int | None
    segment_count: int
    omitted_segments: int
    segments: tuple[MetricSegment, ...]


@dataclass(frozen=True, slots=True)
class SoakComparison:
    status: Literal["compared", "insufficient_evidence"]
    baseline: dict[str, JsonValue]
    candidate: dict[str, JsonValue]
    environment_unverified: list[str]
    metrics: tuple[MetricComparison, ...]
    schema_version: int = 1
    diagnostic_only: bool = True
    trend_method: str = "least_squares_with_separate_intercepts_for_consecutive_segments"
    segment_details_limit: int = SEGMENT_DETAILS_LIMIT
    interpretation: str = (
        "Observed growth is diagnostic evidence and does not establish a memory leak."
    )

    def to_dict(self) -> dict[str, JsonValue]:
        return asdict(self)


def _values(history: SavedSoak, metric: str) -> tuple[dict[int, int], SampleCounts]:
    values: dict[int, int] = {}
    missing = invalid = excluded = 0
    for row in history.journal.rows:
        if row.iteration <= history.scenario.metrics.warmup_iterations:
            excluded += 1
            continue
        sample = row.metrics[0] if row.metrics else None
        if sample is not None and sample.error is not None:
            invalid += 1
        elif sample is None or getattr(sample, metric) is None:
            missing += 1
        else:
            values[row.iteration] = getattr(sample, metric)
    return values, SampleCounts(len(values), missing, invalid, excluded, 0)


def _segments(iterations: list[int]) -> list[list[int]]:
    segments: list[list[int]] = []
    for iteration in iterations:
        if not segments or iteration != segments[-1][-1] + 1:
            segments.append([])
        segments[-1].append(iteration)
    return segments


def _statistics(values: dict[int, int], segments: list[list[int]]) -> MetricStatistics:
    selected = [values[iteration] for segment in segments for iteration in segment]
    numerator = Fraction(0)
    denominator = Fraction(0)
    for segment in segments:
        size = len(segment)
        # Center exact integers before floating arithmetic so near-int64 samples
        # changing by one byte retain that difference. Gaps get separate intercepts.
        xs = [iteration - segment[0] for iteration in segment]
        ys = [values[iteration] - values[segment[0]] for iteration in segment]
        numerator += Fraction(size * sum(x * y for x, y in zip(xs, ys)) - sum(xs) * sum(ys), size)
        denominator += Fraction(size * sum(x * x for x in xs) - sum(xs) ** 2, size)
    return MetricStatistics(
        selected[0] if selected else None,
        selected[-1] if selected else None,
        min(selected) if selected else None,
        max(selected) if selected else None,
        selected[-1] - selected[0] if len(selected) >= 2 else None,
        float(numerator / denominator) if denominator else None,
    )


def _compare_metric(baseline: SavedSoak, candidate: SavedSoak, metric: str) -> MetricComparison:
    left, left_counts = _values(baseline, metric)
    right, right_counts = _values(candidate, metric)
    iterations = sorted(left.keys() & right.keys())
    segments = _segments(iterations)
    left_stats, right_stats = _statistics(left, segments), _statistics(right, segments)
    slope_delta = (
        right_stats.slope_per_iteration - left_stats.slope_per_iteration
        if left_stats.slope_per_iteration is not None
        and right_stats.slope_per_iteration is not None
        else None
    )
    return MetricComparison(
        metric,
        "compared"
        if slope_delta is not None
        else "insufficient_samples"
        if iterations
        else "unavailable",
        len(iterations),
        replace(left_counts, unpaired=len(left) - len(iterations)),
        replace(right_counts, unpaired=len(right) - len(iterations)),
        left_stats,
        right_stats,
        slope_delta,
        right_stats.net_change - left_stats.net_change
        if left_stats.net_change is not None and right_stats.net_change is not None
        else None,
        len(segments),
        max(0, len(segments) - SEGMENT_DETAILS_LIMIT),
        tuple(
            MetricSegment(
                segment[0],
                segment[-1],
                len(segment),
                _statistics(left, [segment]),
                _statistics(right, [segment]),
            )
            for segment in segments[-SEGMENT_DETAILS_LIMIT:]
        ),
    )


def _read_history(directory: Path, side: str) -> SavedSoak:
    try:
        return read_saved_soak(directory)
    except (OSError, ValueError, PlayerLaunchError, RecursionError) as exc:
        message = " ".join(str(exc).split())[:512]
        raise SoakComparisonError(f"Invalid {side} evidence: {message}") from exc


def compare_soak_metrics(baseline: Path, candidate: Path) -> SoakComparison:
    """Compare finalized successful evidence, never launching or modifying artifacts."""
    left, right = _read_history(baseline, "baseline"), _read_history(candidate, "candidate")
    try:
        unknown = require_compatible(left, right)
    except ValueError as exc:
        raise SoakComparisonError(str(exc)) from exc
    metrics = tuple(_compare_metric(left, right, metric) for metric in METRICS)
    return SoakComparison(
        "compared"
        if any(metric.state == "compared" for metric in metrics)
        else "insufficient_evidence",
        left.provenance(),
        right.provenance(),
        unknown,
        metrics,
    )
