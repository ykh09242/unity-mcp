"""Offline command contracts exercised with synthetic NUnit exports."""

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree as ET

import pytest
from cli.main import cli
from click.testing import CliRunner

PRIMARY = "Fixture.Suite.PrimaryLoads"
SCENE = "Fixture.Suite.SceneResolves"
INPUT = "Fixture.Suite.ButtonReceivesClick"
FALLBACK = "Fixture.Suite.FallbackRuns"
SUITE = (PRIMARY, SCENE, INPUT, FALLBACK)
WITNESS = "Fixture.IndependentWitness.BadKey"
ASSET = "Assets/PrimaryKey.asset"


@dataclass(frozen=True)
class Case:
    state: str = "Passed"
    seconds: int | None = 1
    fault: str | None = None


def captured_xml(
    folder: Path, execution: int, cases: dict[str, Case], *, seconds=10, inventory=SUITE, digest="a"
) -> Path:
    root = ET.Element(
        "test-run",
        total=str(len(cases)),
        duration=str(seconds),
        result="Failed(Child)"
        if any(case.state == "Failed" for case in cases.values())
        else "Passed",
    )
    suite = ET.SubElement(root, "test-suite", type="TestFixture")
    for name, case in cases.items():
        row = ET.SubElement(suite, "test-case", fullname=name, result=case.state)
        if case.seconds is not None:
            row.set("duration", str(case.seconds))
        if case.fault:
            ET.SubElement(ET.SubElement(row, "failure"), "message").text = (
                "MCP_MUTATION_WITNESS " + case.fault + "\nIndependently declared contract failed."
            )
        ET.SubElement(row, "output").text = "\n".join(
            [
                f"MCP_VERIFICATION_EXECUTION {UUID(int=execution, version=4).hex}",
                "MCP_VERIFICATION_CONTEXT 6000.0.69f1|StandaloneWindows64|EditMode|Windows",
                *(f"MCP_VERIFICATION_EXPECTED_CASE {test}" for test in inventory),
                f"{ASSET} {'1' * 32} {digest * 64}",
                f"Assets/Fixture.cs {'2' * 32} {'c' * 64}",
            ]
        )
    path = folder / f"execution-{execution}.xml"
    path.write_bytes(ET.tostring(root, encoding="utf-8"))
    return path


def offline(command, *args):
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "verification-evidence",
            command,
            *map(str, args),
        ],
    )
    assert result.stdout, result.exception
    return result.exit_code, json.loads(result.stdout)


def budget_args(folder, timings):
    ranking = folder / "ranking.json"
    ranking.write_text(
        json.dumps(
            {
                "schema": "verification_test_ranking@1",
                "tests": [
                    {"name": test, "reason": f"Impact review rank {index}"}
                    for index, test in enumerate(SUITE, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )
    history = captured_xml(folder, 1, timings, seconds=630)
    return ("--ranking", ranking, "--history", history)


@pytest.mark.parametrize(
    "budget_ms, planned, missed, duration",
    [
        (120_000, (PRIMARY, INPUT), [FALLBACK, SCENE], 100),
        (600_000, (PRIMARY, SCENE, INPUT), [FALLBACK], 530),
        (700_000, SUITE, [], 650),
    ],
)
def test_budget_reports_observed_misses_and_saved_time_against_the_same_full_scope(
    tmp_path, budget_ms, planned, missed, duration
):
    # Given: an ordered impact list, independently timed cases and the same faulted input in both trials.
    args = budget_args(
        tmp_path, dict(zip(SUITE, (Case(seconds=s) for s in (85, 420, 20, 95)), strict=True))
    )
    cases = {PRIMARY: Case(), SCENE: Case("Failed"), INPUT: Case(), FALLBACK: Case("Failed")}
    full = captured_xml(tmp_path, 2, cases, seconds=650, digest="b")
    selected = captured_xml(
        tmp_path, 3, {name: cases[name] for name in planned}, seconds=duration, digest="b"
    )

    # When: the published command evaluates the planned selection and independent full control.
    code, report = offline(
        "budget-assess", *args, "--budget-ms", budget_ms, "--selected", selected, "--full", full
    )

    # Then: narrower runs remain incomplete while retaining measured differences.
    assert code == (2 if missed else 0), report
    assert report["plan"]["test_names"] == list(planned)
    assert report["plan"]["estimated_overhead_ms"] == 10_000
    assert report["plan"]["tests"][0]["selection_reason"] == "Impact review rank 1"
    assert [row["name"] for row in report["plan"]["tests"] if row["disposition"] == "budget"] == [
        name for name in SUITE if name not in planned
    ]
    assert report["missed_failures"] == missed
    assert report["measured_seconds_saved"] == 650 - duration


def test_missing_case_timing_or_startup_history_leaves_the_plan_incomplete(tmp_path):
    # Given: a full historical case inventory with one untimed case.
    args = budget_args(
        tmp_path, {name: Case(seconds=None if name == SCENE else 10) for name in SUITE}
    )

    # When: a generous budget is requested.
    code, plan = offline("budget-plan", *args, "--budget-ms", 700_000)

    # Then: the planner reports the timing gaps and proposes no unestimated executions.
    assert code == 2, plan
    assert plan["test_names"] == []
    assert {row["name"]: row["disposition"] for row in plan["tests"]} == {
        PRIMARY: "missing_overhead",
        SCENE: "missing_duration",
        INPUT: "missing_overhead",
        FALLBACK: "missing_overhead",
    }


@pytest.mark.parametrize("gap", ["missing_selected", "missing_full", "budget_overrun"])
def test_budget_assessment_with_unresolved_or_overrun_execution_is_incomplete(tmp_path, gap):
    # Given: a plan that fits the budget but one runtime discrepancy.
    args = budget_args(tmp_path, {name: Case(seconds=150) for name in SUITE})
    cases = {name: Case() for name in SUITE}
    full_cases = {
        name: row for name, row in cases.items() if gap != "missing_full" or name != FALLBACK
    }
    selected_cases = {
        name: row for name, row in cases.items() if gap != "missing_selected" or name != FALLBACK
    }
    full = captured_xml(tmp_path, 2, full_cases, seconds=650)
    selected = captured_xml(
        tmp_path, 3, selected_cases, seconds=800 if gap == "budget_overrun" else 650
    )

    # When: the exact-plan comparison is requested.
    code, report = offline(
        "budget-assess", *args, "--budget-ms", 700_000, "--selected", selected, "--full", full
    )

    # Then: the result exposes the specific unresolved control and refuses a complete verdict.
    assert code == 2, report
    assert report["state"] == "incomplete"
    assert report["not_observed_from_plan"] == ([FALLBACK] if gap == "missing_selected" else [])
    assert report["full"]["unresolved_checks"] == ([FALLBACK] if gap == "missing_full" else [])
    assert report["within_observed_budget"] is (gap != "budget_overrun")
    assert report["missed_failures"] == ([] if gap == "budget_overrun" else None)


@pytest.mark.parametrize(
    "scope,witness_fault,expected,exit_code",
    [
        ("selected", "loader-key", "caught_by_selection", 0),
        ("full", "loader-key", "missed_by_selection", 1),
        ("neither", "loader-key", "missed_by_full", 1),
        ("neither", "other-fault", "incomplete", 2),
    ],
)
def test_mutation_requires_a_clean_baseline_and_an_independent_matching_failure(
    tmp_path, scope, witness_fault, expected, exit_code
):
    # Given: one changed asset and separate baseline, selection, full and witness executions.
    baseline_cases = {name: Case() for name in SUITE}
    baseline = captured_xml(tmp_path, 1, baseline_cases)
    witness_baseline = captured_xml(tmp_path, 2, {WITNESS: Case()}, inventory=(WITNESS,))
    witness_mutant = captured_xml(
        tmp_path,
        3,
        {WITNESS: Case("Failed", fault=witness_fault)},
        inventory=(WITNESS,),
        digest="b",
    )
    full_cases = (
        {**baseline_cases, PRIMARY: Case("Failed", fault="loader-key")}
        if scope != "neither"
        else baseline_cases
    )
    full = captured_xml(tmp_path, 4, full_cases, digest="b")
    test_name = PRIMARY if scope == "selected" else INPUT
    selected = captured_xml(tmp_path, 5, {test_name: full_cases[test_name]}, digest="b")

    # When: the measured suite is compared against the held-out witness.
    code, report = offline(
        "mutation",
        "--mutation-id",
        "loader-key",
        "--mutated-asset",
        ASSET,
        "--baseline",
        baseline,
        "--selected",
        selected,
        "--full",
        full,
        "--witness-test",
        WITNESS,
        "--witness",
        witness_baseline,
        witness_mutant,
    )

    # Then: only a matching witness supports a catch or an established escape.
    assert code == exit_code, report
    assert report["state"] == expected
    assert report["full"]["caught_tests"] == ([PRIMARY] if scope != "neither" else [])
    assert report["selected"]["caught_tests"] == ([PRIMARY] if scope == "selected" else [])
    assert report["missed_witnessing_full_tests"] == (
        None if exit_code == 2 else [PRIMARY] if scope == "full" else []
    )
