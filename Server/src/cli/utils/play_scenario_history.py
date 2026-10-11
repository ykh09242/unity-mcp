"""Diagnostic failure statistics from explicitly selected complete saved histories."""

from collections import Counter
from pathlib import Path

from cli.utils.play_scenario_analysis_io import AnalysisError, safe_local_path
from cli.utils.play_scenario_history_models import (
    HistoryAnalysis,
    HistoryEvidence,
    HistoryGroup,
    HistoryOutcome,
)
from cli.utils.play_scenario_history_session import read_session_history
from cli.utils.play_scenario_history_soak import read_soak_history
from cli.utils.play_scenario_player import PlayerLaunchError
from pydantic import JsonValue


def _rate(rows: tuple[HistoryOutcome, ...]) -> float | None:
    failed = sum(row.counts["failed"] + row.counts["timed_out"] for row in rows)
    denominator = sum(
        row.counts[state]
        for row in rows
        for state in ("passed", "failed", "timed_out", "cancelled")
    )
    return failed / denominator if denominator else None


def _statistics(evidence: HistoryEvidence) -> HistoryGroup:
    longest = current = 0
    previous_sequence = 0
    for row in evidence.outcomes:
        if row.sequence != previous_sequence + 1:
            current = 0
        failed = row.counts["failed"] + row.counts["timed_out"] > 0
        current = current + 1 if failed else 0
        longest = max(longest, current)
        previous_sequence = row.sequence
    counts = evidence.counts
    failed = counts["failed"] + counts["timed_out"]
    denominator = sum(counts[key] for key in ("passed", "failed", "timed_out", "cancelled"))
    midpoint = len(evidence.outcomes) // 2
    early = _rate(evidence.outcomes[:midpoint])
    late = _rate(evidence.outcomes[midpoint:])
    trend: dict[str, JsonValue] = {
        "unit": "iteration" if evidence.kind == "soak" else "process_outcome",
        "early_failure_rate": early,
        "late_failure_rate": late,
        "failure_rate_delta": late - early if early is not None and late is not None else None,
        "ordering": "journal",
        "cross_source": False,
    }
    return HistoryGroup(
        source=str(evidence.source),
        history_id=evidence.identity,
        kind=evidence.kind,
        mode=evidence.mode,
        parent_status=evidence.parent_status,
        evidence_state="complete" if evidence.complete else "incomplete",
        compatibility=evidence.compatibility,
        compatible_history=evidence.compatibility_known,
        counts=counts,
        outcomes=list(evidence.outcomes),
        first_failure_iteration=next(
            (
                row.first_failure_iteration
                for row in evidence.outcomes
                if row.first_failure_iteration is not None
            ),
            None,
        ),
        failure_rate_denominator=denominator,
        failure_rate=failed / denominator if denominator else None,
        intermittent=evidence.compatibility_known and counts["passed"] > 0 and failed > 0,
        longest_failure_streak=longest,
        trailing_failure_streak=current,
        streak_unit="iteration" if evidence.kind == "soak" else "process_outcome",
        failure_frequencies=dict(
            Counter(
                row.failure_code or row.status
                for row in evidence.outcomes
                if row.counts["failed"] + row.counts["timed_out"] > 0
            )
        ),
        trend=trend,
        evidence_notes=list(evidence.notes),
    )


def analyze_history(sources: tuple[Path, ...]) -> HistoryAnalysis:
    """Admit <=16 explicit source files; never discover histories, retry or run a Player."""
    if not 1 <= len(sources) <= 16:
        raise AnalysisError("Select between 1 and 16 explicit history files")
    groups = []
    unsupported = []
    identities = set()
    for source in sources:
        try:
            source = safe_local_path(source)
            readers = {
                "session.json": read_session_history,
                "soak.json": read_soak_history,
                "client-soak.json": read_soak_history,
                "run.json": read_soak_history,
            }
            reader = readers.get(source.name)
            if reader is None:
                raise AnalysisError(
                    "Select session.json, soak.json, client-soak.json or a soak run.json"
                )
            evidence = reader(source)
            identity = (evidence[0].kind, evidence[0].identity)
            if identity in identities:
                raise AnalysisError("Duplicate history identity; select a history only once")
            identities.add(identity)
            groups.extend(_statistics(partition) for partition in evidence)
        except (OSError, ValueError, PlayerLaunchError) as exc:
            unsupported.append({"source": str(source), "error": str(exc)[:1024]})
    return HistoryAnalysis(
        status="incomplete"
        if unsupported or any(group.evidence_state == "incomplete" for group in groups)
        else "analyzed",
        groups=groups,
        unsupported_inputs=unsupported,
    )
