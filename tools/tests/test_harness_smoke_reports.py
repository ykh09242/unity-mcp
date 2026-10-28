"""Exercise actual smoke report parsing and aggregation with an owned child boundary."""

import sys
import subprocess
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_harness as lh


def report(cases=7):
    suite = lh.JUnitSuite(
        "UnityMCP.E2E.Bridge", [lh.JUnitCase(f"step_{i}", time_s=0.125) for i in range(cases)]
    )
    return lh.merge_junit([suite])


def fake_child(monkeypatch, xml, returncode=0):
    def run(argv, **kwargs):
        path = Path(argv[argv.index("--junit") + 1])
        if xml is not None:
            if isinstance(xml, str):
                path.write_text(xml, encoding="utf-8")
            else:
                xml.write(path, encoding="utf-8", xml_declaration=True)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(lh.subprocess, "run", run)


def test_smoke_cases_are_retained_in_per_leg_and_combined_report(tmp_path, monkeypatch):
    fake_child(monkeypatch, report())
    requested = tmp_path / "custom" / "smoke.xml"
    outcome = lh.run_smoke_leg("owned-instance", requested, 2, 50)
    assert outcome.status == "pass"
    lh.write_reports(requested, tmp_path / "reports", [outcome])
    for path in [requested, tmp_path / "reports" / "junit-all.xml"]:
        root = ET.parse(path).getroot()
        assert root.get("tests") == "7"
        assert root.get("failures") == "0"
        assert [row.get("name") for row in root.findall(".//testcase")] == [
            f"step_{i}" for i in range(7)
        ]
        assert root.get("time") == "0.875"


@pytest.mark.parametrize(
    "bad_report",
    [
        None,
        "bad xml",
        "<testsuites />",
        '<testsuite tests="9"><testcase name="only-one" /></testsuite>',
    ],
)
def test_missing_or_invalid_report_cannot_reuse_old_success(tmp_path, monkeypatch, bad_report):
    requested = tmp_path / "smoke.xml"
    report().write(requested)
    fake_child(monkeypatch, bad_report)
    outcome = lh.run_smoke_leg("owned-instance", requested, 2, 50)
    assert outcome.status == "error"
    assert lh.aggregate_exit([outcome]) == 2
    lh.write_reports(requested, tmp_path / "reports", [outcome])
    root = ET.parse(tmp_path / "reports" / "junit-all.xml").getroot()
    assert root.get("tests") == "1" and root.get("errors") == "1"


def test_failed_skipped_and_error_cases_survive_aggregation(tmp_path, monkeypatch):
    suite = lh.JUnitSuite(
        "smoke",
        [
            lh.JUnitCase("assertion", failure="failed check"),
            lh.JUnitCase("ignored", skipped=True),
            lh.JUnitCase("exception", error="read failed"),
        ],
    )
    fake_child(monkeypatch, lh.merge_junit([suite]), returncode=1)
    requested = tmp_path / "smoke.xml"
    outcome = lh.run_smoke_leg("owned-instance", requested, 0, 0)
    assert outcome.status == "fail"
    lh.write_reports(requested, tmp_path / "reports", [outcome])
    root = ET.parse(tmp_path / "reports" / "junit-all.xml").getroot()
    assert [root.get(k) for k in ("tests", "failures", "errors", "skipped")] == ["3", "1", "1", "1"]
    assert root.find(".//failure").text == "failed check"
    assert root.find(".//error").text == "read failed"


@pytest.mark.parametrize("returncode", [1, 2])
def test_exit_code_cannot_contradict_report_success(tmp_path, monkeypatch, returncode):
    fake_child(monkeypatch, report(), returncode)
    outcome = lh.run_smoke_leg("owned-instance", tmp_path / "smoke.xml", 0, 0)
    assert outcome.status == "error" and lh.aggregate_exit([outcome]) == 2


def test_timeout_is_visible_in_aggregate_report(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("smoke", 1)

    monkeypatch.setattr(lh.subprocess, "run", timeout)
    outcome = lh.run_smoke_leg("owned-instance", tmp_path / "smoke.xml", 0, 0)
    assert outcome.junit_suite.cases[0].error
    assert outcome.status == "error"
