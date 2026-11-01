"""Run the authored NL workflow report steps with disposable JUnit fixtures."""

import os
from pathlib import Path
import subprocess
import sys
from typing import Final
import xml.etree.ElementTree as ET

import pytest
import yaml


WORKFLOW: Final = Path(__file__).resolve().parents[2] / ".github/workflows/claude-nl-suite.yml"
REQUIRED: Final = (
    *(f"NL-{index}" for index in range(5)),
    *(f"T-{letter}" for letter in "ABCDEFGHIJ"),
    *(f"GO-{index}" for index in range(11)),
)


def run_report_step(tmp_path: Path, name: str) -> subprocess.CompletedProcess[str]:
    """Execute the actual workflow heredoc, without a shell or licensed tools."""
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    script = next(
        step["run"] for step in config["jobs"]["nl-suite"]["steps"] if step.get("name") == name
    )
    source = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    return subprocess.run(
        [sys.executable, "-W", "error", "-c", source],
        cwd=tmp_path,
        env={**os.environ, "JUNIT_OUT": "reports/suite.xml", "MD_OUT": "reports/suite.md"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def report(tmp_path: Path, outcomes: tuple[tuple[str, str | None], ...]) -> Path:
    """Write representative cases; expected IDs are independent of workflow logic."""
    suite = ET.Element("testsuite")
    for name, outcome in outcomes:
        case = ET.SubElement(suite, "testcase", name=name)
        if outcome:
            ET.SubElement(case, outcome)
    path = tmp_path / "reports/suite.xml"
    path.parent.mkdir(exist_ok=True)
    ET.ElementTree(suite).write(path)
    return path


@pytest.mark.parametrize("outcome", [None, "skipped", "failure", "error"])
def test_gate_requires_all_required_cases_to_pass(tmp_path: Path, outcome: str | None) -> None:
    # Given all 26 required cases have the same execution outcome.
    report(tmp_path, tuple((name, outcome) for name in REQUIRED))
    # When the production gate runs.
    result = run_report_step(tmp_path, "Fail CI if NL/T incomplete or failed")
    # Then only executed, passing cases satisfy the gate.
    assert result.returncode == (0 if outcome is None else 1), result.stdout + result.stderr


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
@pytest.mark.parametrize("bad_first", [False, True])
def test_duplicate_pass_cannot_hide_bad_required_case(
    tmp_path: Path, outcome: str, bad_first: bool
) -> None:
    # Given a passing suite and a duplicate nonpassing required case in either order.
    cases = [(name, None) for name in REQUIRED]
    cases.insert(0 if bad_first else len(cases), ("NL-0 duplicate", outcome))
    report(tmp_path, tuple(cases))
    # When the production gate runs.
    result = run_report_step(tmp_path, "Fail CI if NL/T incomplete or failed")
    # Then no ordering can make that ID pass.
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("state", ["absent", "empty", "missing-case", "cancelled"])
def test_gate_rejects_incomplete_execution(tmp_path: Path, state: str) -> None:
    # Given a missing report, empty report, omitted case, or cancelled partial execution.
    if state != "absent":
        cases = () if state == "empty" else tuple((name, None) for name in REQUIRED[:-1])
        if state == "cancelled":
            cases += ((REQUIRED[-1], "skipped"),)
        report(tmp_path, cases)
    # When the production gate runs.
    result = run_report_step(tmp_path, "Fail CI if NL/T incomplete or failed")
    # Then incomplete execution cannot certify a passing suite.
    assert result.returncode == 1, result.stdout + result.stderr


def test_optional_skipped_case_does_not_fail_required_gate(tmp_path: Path) -> None:
    # Given all required cases pass and an additional optional case is skipped.
    report(tmp_path, tuple((name, None) for name in REQUIRED) + (("optional", "skipped"),))
    # When the production gate runs.
    result = run_report_step(tmp_path, "Fail CI if NL/T incomplete or failed")
    # Then the required-case policy preserves optional skips.
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_gate_checks_duplicate_cases_in_every_junit_suite(tmp_path: Path, outcome: str) -> None:
    # Given passing required cases in one suite and a nonpassing duplicate in another.
    path = report(tmp_path, tuple((name, None) for name in REQUIRED))
    wrapper = ET.Element("testsuites")
    wrapper.append(ET.parse(path).getroot())
    second = ET.SubElement(wrapper, "testsuite")
    ET.SubElement(ET.SubElement(second, "testcase", name="NL-0"), outcome)
    ET.ElementTree(wrapper).write(path)
    # When the production gate runs.
    result = run_report_step(tmp_path, "Fail CI if NL/T incomplete or failed")
    # Then the later suite cannot conceal nonpassing required execution.
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("location", ["same-fragment", "staging"])
def test_assembler_does_not_discard_nonpassing_duplicates(tmp_path: Path, location: str) -> None:
    # Given all required IDs pass, plus a duplicate skip in fragment input.
    path = report(tmp_path, tuple((name, None) for name in REQUIRED))
    first = ET.Element("testcase", name="NL-0")
    bad = ET.Element("testcase", name="NL-0")
    ET.SubElement(bad, "skipped")
    if location == "same-fragment":
        fragment = ET.Element("testsuite")
        fragment.extend([first, bad])
        ET.ElementTree(fragment).write(path.parent / "NL-0_results.xml")
    else:
        staging = path.parent / "_staging"
        staging.mkdir()
        ET.ElementTree(first).write(path.parent / "NL-0_results.xml")
        ET.ElementTree(bad).write(staging / "NL-0_results.xml")
    # When authored assembly and gating process the production inputs.
    assembled = run_report_step(tmp_path, "Normalize/assemble JUnit in-place (single file)")
    assert assembled.returncode == 0, assembled.stdout + assembled.stderr
    result = run_report_step(tmp_path, "Fail CI if NL/T incomplete or failed")
    # Then neither fragment order nor location hides the duplicate skip.
    assert result.returncode == 1, result.stdout + result.stderr


def test_assembler_preserves_junit_outcome_counts(tmp_path: Path) -> None:
    # Given a bootstrap report and fragments with distinct JUnit outcomes.
    path = report(tmp_path, (("NL-Suite.Bootstrap", "failure"),))
    for name, outcome in zip(REQUIRED, (None, "failure", "error", "skipped")):
        case = ET.Element("testcase", name=name)
        if outcome:
            ET.SubElement(case, outcome)
        ET.ElementTree(case).write(path.parent / f"{name}_results.xml")
    # When the authored assembler merges the fragments.
    result = run_report_step(tmp_path, "Normalize/assemble JUnit in-place (single file)")
    # Then aggregate counts describe the preserved nodes accurately.
    assert result.returncode == 0, result.stdout + result.stderr
    suite = ET.parse(path).getroot()
    assert {key: suite.get(key) for key in ("tests", "failures", "errors", "skipped")} == {
        "tests": "4",
        "failures": "1",
        "errors": "1",
        "skipped": "1",
    }


def test_summary_does_not_label_skipped_or_hidden_duplicate_as_pass(tmp_path: Path) -> None:
    # Given a passing duplicate followed by a skipped required case.
    report(tmp_path, (("NL-0", None), ("NL-0 duplicate", "skipped")))
    # When the authored summary is built.
    result = run_report_step(tmp_path, "Build markdown summary from JUnit")
    # Then the checklist and totals do not certify skipped execution.
    assert result.returncode == 0, result.stdout + result.stderr
    summary = (tmp_path / "reports/suite.md").read_text(encoding="utf-8")
    assert "- [x] NL-0" not in summary
    assert "1 passed, 0 failed, 1 skipped, 2 total" in summary
