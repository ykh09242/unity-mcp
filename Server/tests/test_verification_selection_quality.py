"""Selection measurements require independently observed matching Unity cohorts."""

from xml.etree import ElementTree as ET

import pytest
from cli.utils.verification_selection import read_cohort, selection_quality


def cohort(path, cases, duration, context="6000.0.69f1|Windows|EditMode", digest="a"):
    root = ET.Element(
        "test-run",
        total=str(len(cases)),
        duration=str(duration),
        result="Failed" if "Failed" in cases.values() else "Passed",
    )
    for name, result in cases.items():
        row = ET.SubElement(root, "test-case", fullname=name, result=result)
        ET.SubElement(
            row, "output"
        ).text = f"MCP_VERIFICATION_CONTEXT {context}\nAssets/Map.mat {'1' * 32} {digest * 64}"
    path.write_bytes(ET.tostring(root, encoding="utf-8"))
    return read_cohort(path)


def test_mapped_selection_misses_fault_and_executed_fallback_catches_it(tmp_path):
    narrow = cohort(tmp_path / "narrow", {"map": "Passed"}, 1)
    full = cohort(tmp_path / "full", {"map": "Passed", "route": "Failed"}, 3)
    fallback = cohort(tmp_path / "broad", full.cases, 4)
    result = selection_quality(narrow, full, ["route"], fallback)
    assert result["state"] == "measured"
    assert result["selection"]["missed_failures"] == ["route"]
    assert result["selection"]["unnecessary_checks"] == ["map"]
    assert result["selection"]["measured_seconds_saved"] == 2
    assert result["fallback"]["missed_failures"] == []
    assert result["fallback"]["caught_failures"] == ["route"]
    assert result["fallback"]["measured_seconds_saved"] == -1


def test_unknown_fault_scope_and_disagreeing_controls_are_not_evidence_of_precision(tmp_path):
    narrow = cohort(tmp_path / "a", {"one": "Failed"}, 2)
    control = cohort(tmp_path / "b", {"one": "Passed", "two": "Skipped"}, 5)
    result = selection_quality(narrow, control, [])
    assert result["state"] == "incomplete"
    assert result["selection"]["unnecessary_checks"] is None
    assert result["selection"]["divergent_results"] == ["one"]


def test_changed_inputs_reused_runs_and_missing_control_are_refused(tmp_path):
    narrow = cohort(tmp_path / "a", {"one": "Passed"}, 2)
    wrong = cohort(tmp_path / "b", {"one": "Passed"}, 5, digest="b")
    for full, affected in [(wrong, []), (narrow, []), (wrong, ["missing"])]:
        with pytest.raises(ValueError):
            selection_quality(narrow, full, affected)
