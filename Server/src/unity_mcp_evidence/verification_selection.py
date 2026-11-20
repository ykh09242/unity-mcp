"""Compare independently executed Unity NUnit selections on the same instrumented input."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from .verification_nunit_xml import DECISIVE_RESULTS, read_test_run

_ASSET = re.compile(r"(Assets/.+?) ([0-9a-f]{32}) ([0-9a-f]{64})")
_CONTEXT = "MCP_VERIFICATION_CONTEXT "
_EXECUTION = "MCP_VERIFICATION_EXECUTION "
_EXPECTED_CASE = "MCP_VERIFICATION_EXPECTED_CASE "
_EXECUTION_ID = re.compile(r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}")


@dataclass(frozen=True, slots=True)
class FixtureObservation:
    execution_id: str
    context: str
    expected_cases: frozenset[str]
    inputs: dict[str, tuple[str, str]]


@dataclass(frozen=True, slots=True)
class Cohort:
    digest: str
    fixture: FixtureObservation
    cases: dict[str, str]
    seconds: float
    case_seconds: dict[str, float] = field(default_factory=dict)
    failure_messages: dict[str, str] = field(default_factory=dict)


def _observe_case(case: ElementTree.Element, result: str) -> FixtureObservation | None:
    inputs = {}
    contexts = []
    executions = []
    expected = []
    for line in (case.findtext("output") or "").splitlines():
        if line.startswith(_CONTEXT):
            contexts.append(line.removeprefix(_CONTEXT).strip())
        elif line.startswith(_EXECUTION):
            executions.append(line.removeprefix(_EXECUTION).strip())
        elif line.startswith(_EXPECTED_CASE):
            expected.append(line.removeprefix(_EXPECTED_CASE).strip())
        match = _ASSET.fullmatch(line)
        if match:
            asset, guid, digest = match.groups()
            if asset in inputs:
                raise ValueError("Duplicate fixture input observation")
            inputs[asset] = (guid, digest)
    if result not in DECISIVE_RESULTS and not (inputs or contexts or executions or expected):
        return None  # NUnit can skip cases before the fixture records observations.
    if len(executions) != 1 or not _EXECUTION_ID.fullmatch(executions[0]):
        raise ValueError("Each instrumented case needs one fixture execution ID")
    if not inputs or len(contexts) != 1 or not contexts[0]:
        raise ValueError("Each instrumented case needs fixture input digests and one context")
    if not expected or not all(expected) or len(set(expected)) != len(expected):
        raise ValueError("Each instrumented case needs the complete fixture inventory")
    return FixtureObservation(executions[0], contexts[0], frozenset(expected), inputs)


def read_cohort(path: Path) -> Cohort:
    """Read NUnit selection evidence with its separately recorded fixture inventory."""
    fixture = None

    def observe(case: ElementTree.Element, result: str) -> None:
        nonlocal fixture
        observation = _observe_case(case, result)
        if observation is not None:
            if fixture is not None and observation != fixture:
                raise ValueError("Fixture observations changed within the NUnit run")
            fixture = observation

    run = read_test_run(path, observe_case=observe)
    cases = {name: case.result for name, case in run.cases.items()}
    if fixture is None or not cases.keys() <= fixture.expected_cases:
        raise ValueError("Run must contain fixture observations and only declared fixture cases")
    durations = {
        name: case.duration for name, case in run.cases.items() if case.duration is not None
    }
    failures = {
        name: case.failure_message
        for name, case in run.cases.items()
        if case.failure_message is not None
    }
    return Cohort(run.digest, fixture, cases, run.seconds, durations, failures)


def selection_quality(
    selected: Cohort, full: Cohort, affected: Iterable[str], fallback: Cohort | None = None
):
    cohorts = [selected, full] if fallback is None else [selected, full, fallback]
    if len({row.fixture.execution_id for row in cohorts}) != len(cohorts):
        raise ValueError("Independent NUnit runs require distinct fixture execution IDs")
    context = full.fixture.context
    expected = full.fixture.expected_cases
    inputs = full.fixture.inputs
    if any(
        (row.fixture.context, row.fixture.expected_cases, row.fixture.inputs)
        != (context, expected, inputs)
        for row in cohorts
    ):
        raise ValueError("Inputs, execution context, or fixture inventory differ across cohorts")
    truth = set(affected)
    if not truth <= expected:
        raise ValueError("Declared affected checks must be in the fixture inventory")
    full_executed = {key for key, state in full.cases.items() if state in DECISIVE_RESULTS}
    full_unresolved = sorted(expected - full_executed)
    failed = {key for key, state in full.cases.items() if state == "Failed"}

    def assess(run: Cohort):
        executed = {key for key, state in run.cases.items() if state in DECISIVE_RESULTS}
        unresolved = sorted(run.cases.keys() - executed)
        mismatched = sorted(
            key for key in executed & full_executed if full.cases[key] != run.cases[key]
        )
        caught = {key for key in failed if run.cases.get(key) == "Failed"}
        measured = not (full_unresolved or unresolved or mismatched)
        return {
            "artifact_sha256": run.digest,
            "execution_id": run.fixture.execution_id,
            "executed_count": len(executed),
            "unresolved_checks": unresolved,
            "caught_failures": sorted(caught),
            "missed_failures": sorted(failed - caught) if measured else None,
            "unnecessary_checks": sorted(executed - truth) if measured and truth else None,
            "divergent_results": mismatched,
            "duration_seconds": run.seconds,
            "measured_seconds_saved": full.seconds - run.seconds if measured else None,
        }

    narrow = assess(selected)
    broader = assess(fallback) if fallback is not None else None
    measured = narrow["missed_failures"] is not None and (
        broader is None or broader["missed_failures"] is not None
    )
    return {
        "schema": "verification_selection_quality@1",
        "state": "measured" if measured else "incomplete",
        "context": context,
        "input_count": len(inputs),
        "full": {
            "artifact_sha256": full.digest,
            "execution_id": full.fixture.execution_id,
            "expected_count": len(expected),
            "executed_count": len(full_executed),
            "unresolved_checks": full_unresolved,
            "failed_checks": sorted(failed),
            "duration_seconds": full.seconds,
        },
        "selection": narrow,
        "fallback": broader,
    }
