"""The Windows regression gate cannot certify missing or skipped execution."""

from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml

from tools.check_windows_regressions import CASES, check_report


def test_report_requires_every_selected_case_to_execute(tmp_path):
    suite = ElementTree.Element("testsuite")
    for node, count in CASES.items():
        for index in range(count):
            ElementTree.SubElement(suite, "testcase", name=f"{node.split('::')[1]}[{index}]")
    report = tmp_path / "results.xml"
    tree = ElementTree.ElementTree(suite)
    tree.write(report)
    assert check_report(report) == sum(CASES.values())
    case = suite[-1]
    for kind in ("skipped", "failure", "error"):
        failure = ElementTree.SubElement(case, kind)
        tree.write(report)
        with pytest.raises(ValueError, match="without skips"):
            check_report(report)
        case.remove(failure)
    suite.remove(case)
    tree.write(report)
    with pytest.raises(ValueError, match="count mismatch"):
        check_report(report)


def test_windows_ci_runs_required_hermetic_gate_without_license_condition():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/python-tests.yml").read_text())
    job = workflow["jobs"]["windows_hermetic"]
    assert job["runs-on"] == "windows-latest"
    assert "needs" not in job and "if" not in job
    assert not job.get("continue-on-error")
    runs = [step for step in job["steps"] if "check_windows_regressions.py" in step.get("run", "")]
    assert len(runs) == 1 and not runs[0].get("continue-on-error") and "if" not in runs[0]
