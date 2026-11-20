"""Plan ranked NUnit checks and compare the observed selection with a full control."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Literal, TypedDict

from cli.utils.play_scenario_analysis_io import read_json
from cli.utils.verification_selection import Cohort, selection_quality

MAX_HISTORY = 16
MAX_RANKED = 2048
DECISIVE = frozenset({"Passed", "Failed"})


@dataclass(frozen=True, slots=True)
class RankedTest:
    name: str
    reason: str


class PlannedTest(TypedDict):
    name: str
    rank: int | None
    selection_reason: str | None
    estimated_ms: int | None
    observed_samples: int
    disposition: Literal["selected", "budget", "missing_duration", "missing_overhead", "unranked"]


class BudgetPlan(TypedDict):
    schema: str
    state: Literal["ready", "incomplete"]
    budget_ms: int
    context: str
    history: list[dict[str, str]]
    estimated_overhead_ms: int | None
    estimated_total_ms: int | None
    test_names: list[str]
    tests: list[PlannedTest]


@dataclass(frozen=True, slots=True)
class BudgetInputs:
    ranked: tuple[RankedTest, ...]
    history: tuple[Cohort, ...]
    budget_ms: int


def read_ranking(path: Path) -> tuple[RankedTest, ...]:
    """Read an independently ordered candidate list and its impact reasons."""
    return parse_ranking(read_json(path))


def parse_ranking(raw) -> tuple[RankedTest, ...]:
    tests = raw.get("tests")
    if (
        raw.get("schema") != "verification_test_ranking@1"
        or not isinstance(tests, list)
        or not 1 <= len(tests) <= MAX_RANKED
    ):
        raise ValueError(f"Expected a verification test ranking with 1 to {MAX_RANKED} tests")
    ranked: list[RankedTest] = []
    seen: set[str] = set()
    for row in tests:
        match row:
            case {"name": str(name), "reason": str(reason)} if (
                0 < len(name) <= 1024
                and name == name.strip()
                and name not in seen
                and 0 < len(reason) <= 512
                and reason.strip()
            ):
                ranked.append(RankedTest(name, reason))
                seen.add(name)
            case _:
                raise ValueError("Ranked tests require distinct full names and bounded reasons")
    return tuple(ranked)


def _seconds(value: float) -> Decimal:
    return Decimal(str(value))


def _ceil_ms(seconds: Decimal) -> int:
    return int((seconds * 1000).to_integral_value(rounding=ROUND_CEILING))


def plan_budget(request: BudgetInputs) -> BudgetPlan:
    """Pack the earliest ranked checks that fit the historical timing estimate."""
    history = request.history
    if not 1 <= len(history) <= MAX_HISTORY or request.budget_ms < 1:
        raise ValueError("A positive time budget and 1 to 16 history trials are required")
    context = history[0].fixture.context
    if {run.fixture.context for run in history} != {context}:
        raise ValueError("Timing history requires the same execution context")
    if len({run.fixture.execution_id for run in history}) != len(history):
        raise ValueError("Timing history must use independent executions")

    known_tests: set[str] = set()
    samples: dict[str, list[int]] = {}
    overhead: list[int] = []
    for run in history:
        known_tests.update(run.fixture.expected_cases)
        timed = {
            name: _seconds(duration)
            for name, duration in run.case_seconds.items()
            if run.cases.get(name) in DECISIVE
        }
        for name, duration in timed.items():
            samples.setdefault(name, []).append(_ceil_ms(duration))
        if timed.keys() == run.cases.keys():
            residual = max(Decimal(0), _seconds(run.seconds) - sum(timed.values(), Decimal(0)))
            overhead.append(_ceil_ms(residual))

    reserve = max(overhead, default=None)
    spent = reserve
    ranked_names = {row.name for row in request.ranked}
    if len(known_tests | ranked_names) > MAX_RANKED:
        raise ValueError(f"A time budget supports at most {MAX_RANKED} distinct tests")
    rows: list[PlannedTest] = []
    selected: list[str] = []
    for rank, entry in enumerate(request.ranked, start=1):
        durations = samples.get(entry.name, [])
        estimate = max(durations, default=None)
        if estimate is None:
            disposition = "missing_duration"
        elif spent is None:
            disposition = "missing_overhead"
        elif spent + estimate > request.budget_ms:
            disposition = "budget"
        else:
            disposition = "selected"
            selected.append(entry.name)
            spent += estimate
        rows.append(
            {
                "name": entry.name,
                "rank": rank,
                "selection_reason": entry.reason,
                "estimated_ms": estimate,
                "observed_samples": len(durations),
                "disposition": disposition,
            }
        )
    for name in sorted(known_tests - ranked_names):
        durations = samples.get(name, [])
        rows.append(
            {
                "name": name,
                "rank": None,
                "selection_reason": None,
                "estimated_ms": max(durations, default=None),
                "observed_samples": len(durations),
                "disposition": "unranked",
            }
        )
    return {
        "schema": "verification_test_budget_plan@1",
        "state": "ready" if len(selected) == len(rows) else "incomplete",
        "budget_ms": request.budget_ms,
        "context": context,
        "history": [
            {"artifact_sha256": run.digest, "execution_id": run.fixture.execution_id}
            for run in history
        ],
        "estimated_overhead_ms": reserve,
        "estimated_total_ms": spent if selected else None,
        "test_names": selected,
        "tests": rows,
    }


def assess_budget(plan: BudgetPlan, full: Cohort, selected: Cohort | None) -> dict:
    """Measure elapsed time and missed failures while retaining any deferred obligations."""
    current = [full] if selected is None else [full, selected]
    historical = {row["execution_id"] for row in plan["history"]}
    if any(run.fixture.execution_id in historical for run in current):
        raise ValueError("Budget assessment requires independent runs outside timing history")
    expected = set(plan["test_names"])
    planned_inventory = {row["name"] for row in plan["tests"]}
    unplanned = sorted(full.fixture.expected_cases - planned_inventory)
    stale = sorted(planned_inventory - full.fixture.expected_cases)
    actual = set(selected.cases) if selected is not None else set()
    absent = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    context_changed = plan["context"] != full.fixture.context
    full_unresolved = sorted(
        full.fixture.expected_cases
        - {name for name, state in full.cases.items() if state in DECISIVE}
    )
    full_failed = sorted(name for name, state in full.cases.items() if state == "Failed")
    comparison = selection_quality(selected, full, ()) if selected is not None else None
    selection = comparison["selection"] if comparison is not None else None
    measured = not (
        absent
        or unexpected
        or context_changed
        or full_unresolved
        or (comparison is not None and comparison["state"] != "measured")
    )
    missed = None
    if measured:
        missed = full_failed if selection is None else selection["missed_failures"]
    within_budget = (
        _ceil_ms(_seconds(selected.seconds)) <= plan["budget_ms"] if selected is not None else None
    )
    incomplete = (
        plan["state"] != "ready" or not measured or unplanned or stale or within_budget is False
    )
    return {
        "schema": "verification_test_budget_assessment@1",
        "state": "incomplete" if incomplete else "measured",
        "plan": plan,
        "not_observed_from_plan": absent,
        "unexpected_executed": unexpected,
        "unplanned_full_inventory": unplanned,
        "planned_outside_full_inventory": stale,
        "planning_context_changed": context_changed,
        "within_observed_budget": within_budget,
        "full": {
            "artifact_sha256": full.digest,
            "execution_id": full.fixture.execution_id,
            "duration_seconds": full.seconds,
            "unresolved_checks": full_unresolved,
            "failed_checks": full_failed,
        },
        "selected": {
            "artifact_sha256": selected.digest,
            "execution_id": selected.fixture.execution_id,
            "duration_seconds": selected.seconds,
            "executed_count": selection["executed_count"],
            "unresolved_checks": selection["unresolved_checks"],
            "divergent_results": selection["divergent_results"],
        }
        if selected is not None and selection is not None
        else None,
        "missed_failures": missed,
        "measured_seconds_saved": selection["measured_seconds_saved"]
        if measured and selection is not None
        else None,
    }
