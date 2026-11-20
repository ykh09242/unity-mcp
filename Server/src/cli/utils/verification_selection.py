"""Compare independently executed Unity NUnit selections on the same instrumented input."""

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from .play_scenario_analysis_io import open_artifact
from .play_scenario_report_json import REPORT_LIMIT
from .verification_nunit_xml import NUnitXml

_ASSET = re.compile(r"(Assets/.+?) ([0-9a-f]{32}) ([0-9a-f]{64})")
_CONTEXT = "MCP_VERIFICATION_CONTEXT "
_EXECUTION = "MCP_VERIFICATION_EXECUTION "
_EXPECTED_CASE = "MCP_VERIFICATION_EXPECTED_CASE "
_EXECUTION_ID = re.compile(r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}")
_DECISIVE = {"Passed", "Failed"}
_TERMINAL = _DECISIVE | {"Skipped", "Inconclusive"}


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
    if result not in _DECISIVE and not (inputs or contexts or executions or expected):
        return None  # NUnit can skip cases before the fixture records observations.
    if len(executions) != 1 or not _EXECUTION_ID.fullmatch(executions[0]):
        raise ValueError("Each instrumented case needs one fixture execution ID")
    if not inputs or len(contexts) != 1 or not contexts[0]:
        raise ValueError("Each instrumented case needs fixture input digests and one context")
    if not expected or not all(expected) or len(set(expected)) != len(expected):
        raise ValueError("Each instrumented case needs the complete fixture inventory")
    return FixtureObservation(executions[0], contexts[0], frozenset(expected), inputs)


def _case_duration(case: ElementTree.Element) -> float | None:
    try:
        duration = float(case.get("duration", "nan"))
    except ValueError:
        return None
    return duration if math.isfinite(duration) and duration >= 0 else None


def _keep_case_evidence(element: ElementTree.Element, ancestors: list[ElementTree.Element]) -> bool:
    parent = ancestors[-1]
    if parent.tag == "test-case" and element.tag in {"output", "failure"}:
        return parent.find(element.tag) is element
    return (
        len(ancestors) >= 2
        and ancestors[-2].tag == "test-case"
        and parent.tag == "failure"
        and element.tag == "message"
        and parent.find("message") is element
    )


def read_cohort(path: Path) -> Cohort:
    """Stream case evidence from a complete, regular local Unity NUnit XML file."""
    with open_artifact(path, REPORT_LIMIT) as (file, size):
        return _read_cohort(NUnitXml(file, size))


def _read_cohort(xml: NUnitXml) -> Cohort:
    stack: list[ElementTree.Element] = []
    error = None
    root = {}
    seconds = 0.0
    cases = {}
    durations = {}
    failures = {}
    fixture = None
    for event, element in xml.events():
        try:
            if event == "start":
                if not stack and error is None:
                    if element.tag != "test-run":
                        raise ValueError("Expected Unity NUnit test-run")
                    root = dict(element.attrib)
                    seconds = float(root.get("duration", "nan"))
                    if not math.isfinite(seconds) or seconds < 0:
                        raise ValueError("Missing finite NUnit run duration")
                if element.tag == "test-case" and error is None:
                    name, result = element.get("fullname", ""), element.get("result", "")
                    if not name or name in cases or result not in _TERMINAL:
                        raise ValueError("Expected unique named terminal NUnit cases")
                    cases[name] = result
            elif element.tag == "test-case" and error is None:
                name = element.get("fullname", "")
                observation = _observe_case(element, cases[name])
                if observation is not None:
                    if fixture is not None and observation != fixture:
                        raise ValueError("Fixture observations changed within the NUnit run")
                    fixture = observation
                duration = _case_duration(element)
                if duration is not None:
                    durations[name] = duration
                failure = element.find("failure")
                if failure is not None and (message := failure.findtext("message")):
                    failures[name] = message
        except ValueError as exc:
            # Keep validating the whole input before reporting semantic errors.
            error = exc
        if event == "start":
            stack.append(element)
        else:
            stack.pop()
            if not stack:
                element.clear()
            elif error is not None or not _keep_case_evidence(element, stack):
                stack[-1].remove(element)
                element.clear()
    if error is not None:
        raise error
    if not cases or len(cases) != int(root.get("total", "-1")):
        raise ValueError("Incomplete NUnit case inventory")
    result = root.get("result", "").removesuffix("(Child)")
    if result.startswith("Skipped:"):
        result = "Skipped"
    if result not in cases.values() or (result == "Failed") != ("Failed" in cases.values()):
        raise ValueError("Run outcome does not agree with individual case evidence")
    if fixture is None or not cases.keys() <= fixture.expected_cases:
        raise ValueError("Run must contain fixture observations and only declared fixture cases")
    return Cohort(xml.digest, fixture, cases, seconds, durations, failures)


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
    full_executed = {key for key, state in full.cases.items() if state in _DECISIVE}
    full_unresolved = sorted(expected - full_executed)
    failed = {key for key, state in full.cases.items() if state == "Failed"}

    def assess(run: Cohort):
        executed = {key for key, state in run.cases.items() if state in _DECISIVE}
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
