"""JUnit outcome and bounded evidence contracts for scenario reports."""

from xml.etree import ElementTree

import pytest
from cli.utils.play_scenario_reports import junit_xml, write_suite_artifacts


def test_junit_preserves_outcomes_when_suite_has_mixed_results():
    # Given: one native suite records success, assertion failure, timeout and skipped work.
    report = {
        "suite_id": "a" * 32,
        "suite": {"name": "smoke"},
        "started_unix_ms": 1000,
        "finished_unix_ms": 6500,
        "scenarios": [
            {
                "name": "ok",
                "status": "succeeded",
                "report": {"started_unix_ms": 1000, "finished_unix_ms": 1500},
            },
            {
                "name": "bad",
                "status": "failed",
                "report": {"error": "A < B & C", "logs": [{"message": "bounded"}]},
            },
            {"name": "late", "status": "timed_out", "report": {"error": "deadline"}},
            {"name": "later", "status": "skipped", "skip_reason": "stop policy"},
        ],
    }
    # When: the report is exported for CI parsers.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: counts and testcase outcomes agree and literal diagnostics survive escaping.
    assert xml.attrib["tests"] == "4"
    assert xml.attrib["failures"] == "1"
    assert xml.attrib["errors"] == "1"
    assert xml.attrib["skipped"] == "1"
    assert xml.attrib["time"] == "5.500"
    assert xml.find("testcase").attrib["time"] == "0.500"
    assert xml.find("testcase/failure").text == "A < B & C"


def test_junit_reports_persistence_error_when_execution_succeeded():
    # Given: execution succeeded but its native report could not be retained.
    report = {
        "suite": {"name": "smoke"},
        "scenarios": [
            {"name": "ok", "status": "succeeded", "report": {"report_error": "disk full"}}
        ],
    }
    # When: a machine-readable summary is generated.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: CI cannot mistake incomplete evidence for a clean result.
    assert xml.attrib["errors"] == "1"
    assert xml.find("testcase/error").attrib["type"] == "report_persist_failed"


def test_junit_bounds_logs_and_sanitizes_xml_controls_when_logs_are_untrusted():
    # Given: native diagnostic text contains XML-invalid controls and a large log payload.
    report = {
        "suite": {"name": "smoke"},
        "scenarios": [
            {
                "name": "bad",
                "status": "failed",
                "report": {
                    "error": "broken\x00",
                    "logs": [{"message": "x" * 50000}],
                    "failure_diagnostics": {"screenshot_path": "Artifacts/a.png"},
                    "metadata": {"unity_version": "6000.3"},
                },
            }
        ],
    }
    # When: diagnostic evidence is exported.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: valid XML retains bounded evidence and attachment references.
    assert len(xml.find("testcase/system-out").text) <= 16384
    assert "\x00" not in xml.find("testcase/failure").text
    properties = {
        item.attrib["name"]: item.attrib["value"]
        for item in xml.findall("testcase/properties/property")
    }
    assert properties["screenshot_path"] == "Artifacts/a.png"
    assert properties["unity_version"] == "6000.3"


def test_artifact_export_preserves_json_when_output_directory_is_explicit(tmp_path):
    # Given: the caller explicitly selects a local artifact directory.
    report = {
        "suite_id": "b" * 32,
        "suite": {"name": "smoke"},
        "status": "succeeded",
        "scenarios": [],
    }
    # When: both CI artifact formats are persisted.
    paths = write_suite_artifacts(report, tmp_path)
    # Then: the original JSON and a readable XML artifact are created there.
    import json

    assert json.loads(paths[0].read_text(encoding="utf-8")) == report
    assert ElementTree.parse(paths[1]).getroot().attrib["tests"] == "0"


def test_junit_contains_error_when_suite_failed_before_any_child_started():
    # Given: no child report exists because suite infrastructure failed before admission.
    report = {"suite": {"name": "smoke"}, "scenarios": [], "client_error": "status unavailable"}
    # When: the suite error receipt is exported.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: CI sees one explicit infrastructure error instead of a passing empty suite.
    assert xml.attrib["tests"] == "1"
    assert xml.attrib["errors"] == "1"
    assert xml.find("testcase/error").text == "status unavailable"


def test_junit_preserves_skipped_children_when_suite_has_finalization_error():
    # Given: stop policy skipped one child and suite persistence then failed.
    report = {
        "suite": {"name": "smoke"},
        "report_error": "disk full",
        "scenarios": [
            {
                "name": "failed",
                "status": "failed",
                "report": {"error": "original", "report_error": "child disk full"},
            },
            {"name": "skip", "status": "skipped", "skip_reason": "stop policy"},
        ],
    }
    # When: CI receives both child outcomes and the suite infrastructure failure.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: primary failure, skipped count and a distinct infrastructure error all survive.
    assert xml.attrib["tests"] == "3"
    assert xml.attrib["failures"] == "1"
    assert xml.attrib["skipped"] == "1"
    assert xml.attrib["errors"] == "1"
    assert xml.find("testcase/failure").text == "original"
    assert xml.find("testcase/system-err").text == "child disk full"


def test_junit_keeps_admission_hash_when_child_is_skipped():
    # Given: a frozen child definition was selected but stop policy prevented execution.
    report = {
        "suite": {"name": "smoke"},
        "scenarios": [
            {
                "name": "skip",
                "status": "skipped",
                "definition_hash": "a" * 64,
                "skip_reason": "stop policy",
            }
        ],
    }
    # When: its reproducibility evidence is exported.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: the child hash survives even without an executed report.
    properties = {
        item.attrib["name"]: item.attrib["value"]
        for item in xml.findall("testcase/properties/property")
    }
    assert properties["definition_hash"] == "a" * 64


def test_junit_keeps_admission_failure_reason_when_no_child_report_exists():
    # Given: native child admission failed before its runner could create a report.
    report = {
        "suite": {"name": "smoke"},
        "scenarios": [{"name": "bad", "status": "failed", "skip_reason": "Scene preflight failed"}],
    }
    # When: JUnit projects the native failure receipt.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: the actual admission error survives rather than a generic failure message.
    assert xml.find("testcase/failure").text == "Scene preflight failed"


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
@pytest.mark.parametrize("completed_child", [False, True])
def test_junit_records_parent_outcome_when_suite_stops_without_failed_child(
    status, completed_child
):
    # Given: native cancellation/deadline/failure stops before admission or in a queue gap.
    original_error = (
        "Suite cancelled by request."
        if status == "cancelled"
        else "Suite execution budget expired."
        if status == "timed_out"
        else "Suite coordination failed."
    )
    scenarios = [{"name": "next", "status": "skipped", "skip_reason": original_error}]
    if completed_child:
        scenarios.insert(
            0,
            {
                "name": "first",
                "status": "succeeded",
                "report": {"started_unix_ms": 1000, "finished_unix_ms": 1500},
            },
        )
    report = {
        "suite": {"name": "smoke"},
        "status": status,
        "error": original_error,
        "started_unix_ms": 1000,
        "finished_unix_ms": 1600,
        "scenarios": scenarios,
    }
    # When: native parent and child receipts are exported for CI.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: the skipped/completed children survive and parent failure produces one error case.
    assert xml.attrib["tests"] == str(len(scenarios) + 1)
    assert xml.attrib["errors"] == "1"
    assert xml.attrib["failures"] == "0"
    assert xml.attrib["skipped"] == "1"
    error = xml.find("testcase/error")
    assert error.attrib["type"] == "suite_" + status
    assert error.text == original_error


@pytest.mark.parametrize("status", ["failed", "timed_out", "cancelled"])
def test_junit_does_not_duplicate_parent_outcome_when_child_already_records_same_failure(status):
    # Given: native suite status derives from one child failure/cancellation/deadline outcome.
    original_error = "Original child outcome"
    report = {
        "suite": {"name": "smoke"},
        "status": status,
        "error": original_error,
        "scenarios": [
            {"name": "first", "status": status, "report": {"error": original_error}},
            {"name": "next", "status": "skipped", "skip_reason": "stop policy"},
        ],
    }
    # When: parent and child lifecycle records are projected into JUnit.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: a single failed/error child represents the original cause without a duplicate case.
    assert xml.attrib["tests"] == "2"
    assert int(xml.attrib["failures"]) + int(xml.attrib["errors"]) == 1
    assert xml.attrib["skipped"] == "1"


def test_junit_does_not_duplicate_parent_outcome_when_suite_persistence_already_records_failure():
    # Given: suite persistence fails in a queue gap and is already an infrastructure error.
    report = {
        "suite": {"name": "smoke"},
        "status": "failed",
        "error": "disk full",
        "report_error": "disk full",
        "scenarios": [{"name": "next", "status": "skipped"}],
    }
    # When: both parent outcome and infrastructure evidence are exported.
    xml = ElementTree.fromstring(junit_xml(report))
    # Then: the one suite infrastructure error represents that cause.
    assert xml.attrib["tests"] == "2"
    assert xml.attrib["errors"] == "1"
    assert xml.find("testcase/error").text == "disk full"
