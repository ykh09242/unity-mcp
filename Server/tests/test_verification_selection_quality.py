"""Selection measurements require independently observed matching Unity cohorts."""

from uuid import UUID
from xml.etree import ElementTree as ET

import pytest
from cli.utils.verification_selection import read_cohort, selection_quality

MAP = "VerificationFixtures.NavigationMaterialChecks.MapMarkersSupportInstancing"
ROUTE = "VerificationFixtures.NavigationMaterialChecks.RouteArrowsSupportInstancing"


@pytest.fixture
def xml_cohort(tmp_path):
    def write(execution, cases, duration=1, *, expected=(MAP, ROUTE), digest="a"):
        execution_id = UUID(int=execution, version=4).hex
        verdicts = cases.values()
        summary = next(
            (
                summary
                for status, summary in (
                    ("Failed", "Failed(Child)"),
                    ("Skipped", "Skipped:Ignored"),
                    ("Inconclusive", "Inconclusive"),
                )
                if status in verdicts
            ),
            "Passed",
        )
        root = ET.Element(
            "test-run",
            total=str(len(cases)),
            duration=str(duration),
            result=summary,
        )
        suite = ET.SubElement(root, "test-suite", type="TestFixture")
        for name, result in cases.items():
            row = ET.SubElement(suite, "test-case", fullname=name, result=result)
            if result == "Skipped":
                continue
            ET.SubElement(row, "output").text = "\n".join(
                [
                    f"MCP_VERIFICATION_EXECUTION {execution_id}",
                    "MCP_VERIFICATION_CONTEXT 6000.0.69f1|StandaloneWindows64|EditMode|Windows",
                    *(f"MCP_VERIFICATION_EXPECTED_CASE {case}" for case in expected),
                    f"Assets/Map.mat {'1' * 32} {digest * 64}",
                ]
            )
        path = tmp_path / f"{execution}.xml"
        path.write_bytes(ET.tostring(root, encoding="utf-8"))
        return path

    return write


def test_mapped_selection_misses_fault_and_executed_fallback_catches_it(xml_cohort):
    narrow = read_cohort(xml_cohort(1, {MAP: "Passed"}, 1))
    full = read_cohort(xml_cohort(2, {MAP: "Passed", ROUTE: "Failed"}, 3))
    fallback = read_cohort(xml_cohort(3, {MAP: "Passed", ROUTE: "Failed"}, 4))

    result = selection_quality(narrow, full, [ROUTE], fallback)

    assert result["state"] == "measured"
    assert result["selection"]["missed_failures"] == [ROUTE]
    assert result["selection"]["unnecessary_checks"] == [MAP]
    assert result["selection"]["measured_seconds_saved"] == 2
    assert result["fallback"]["missed_failures"] == []
    assert result["fallback"]["caught_failures"] == [ROUTE]
    assert result["fallback"]["measured_seconds_saved"] == -1


def test_a_fresh_run_can_have_identical_outcomes_and_duration(xml_cohort):
    narrow = read_cohort(xml_cohort(1, {MAP: "Passed", ROUTE: "Failed"}))
    full = read_cohort(xml_cohort(2, {MAP: "Passed", ROUTE: "Failed"}))

    result = selection_quality(narrow, full, [])

    assert result["state"] == "measured"
    assert result["selection"]["missed_failures"] == []
    assert result["selection"]["unnecessary_checks"] is None
    assert result["selection"]["measured_seconds_saved"] == 0


def test_rewritten_nunit_xml_cannot_supply_an_independent_run(tmp_path, xml_cohort):
    capture = xml_cohort(1, {MAP: "Passed", ROUTE: "Failed"})
    xml = ET.parse(capture)
    root = xml.getroot()
    suite = root.find("test-suite")
    root.remove(suite)
    ET.SubElement(root, "test-suite", type="Assembly").append(suite)
    ET.indent(xml)
    rewritten = tmp_path / "rewritten.xml"
    xml.write(rewritten, encoding="utf-8", xml_declaration=True)
    first, second = read_cohort(capture), read_cohort(rewritten)
    assert first.digest != second.digest

    with pytest.raises(ValueError, match="Independent NUnit runs"):
        selection_quality(first, second, [ROUTE])


@pytest.mark.parametrize("replacement", ["", "not-an-execution-id", UUID(int=2, version=4).hex])
def test_rejects_missing_invalid_or_mixed_run_ids(xml_cohort, replacement):
    capture = xml_cohort(1, {MAP: "Passed", ROUTE: "Failed"})
    xml = ET.parse(capture)
    output = xml.findall(".//test-case/output")[1]
    lines = output.text.splitlines()[1:]
    if replacement:
        lines.insert(0, f"MCP_VERIFICATION_EXECUTION {replacement}")
    output.text = "\n".join(lines)
    xml.write(capture, encoding="utf-8")

    with pytest.raises(ValueError, match="fixture execution ID|changed within the NUnit run"):
        read_cohort(capture)


@pytest.mark.parametrize("control_result", [None, "Skipped", "Inconclusive"])
def test_full_control_needs_a_definitive_result_for_every_fixture_case(xml_cohort, control_result):
    narrow = read_cohort(xml_cohort(1, {MAP: "Passed"}))
    full_cases = {MAP: "Passed"}
    if control_result is not None:
        full_cases[ROUTE] = control_result
    full = read_cohort(xml_cohort(2, full_cases, 2))

    result = selection_quality(narrow, full, [ROUTE])

    assert result["state"] == "incomplete"
    assert result["full"]["expected_count"] == 2
    assert result["full"]["executed_count"] == 1
    assert result["full"]["unresolved_checks"] == [ROUTE]
    assert result["selection"]["missed_failures"] is None
    assert result["selection"]["unnecessary_checks"] is None
    assert result["selection"]["measured_seconds_saved"] is None


@pytest.mark.parametrize("selected_result", ["Skipped", "Inconclusive", "Passed"])
def test_unresolved_or_divergent_selection_does_not_measure_a_miss(xml_cohort, selected_result):
    narrow = read_cohort(xml_cohort(1, {MAP: "Passed", ROUTE: selected_result}))
    full = read_cohort(xml_cohort(2, {MAP: "Passed", ROUTE: "Failed"}, 2))

    result = selection_quality(narrow, full, [ROUTE])

    assert result["state"] == "incomplete"
    assert result["full"]["failed_checks"] == [ROUTE]
    assert result["selection"]["unresolved_checks"] == (
        [] if selected_result == "Passed" else [ROUTE]
    )
    assert result["selection"]["divergent_results"] == (
        [ROUTE] if selected_result == "Passed" else []
    )
    assert result["selection"]["missed_failures"] is None
    assert result["selection"]["measured_seconds_saved"] is None


@pytest.mark.parametrize("change", ["inputs", "inventory", "affected"])
def test_refuses_unmatched_fixture_inputs_or_inventory(xml_cohort, change):
    narrow = read_cohort(xml_cohort(1, {MAP: "Passed"}))
    full = read_cohort(
        xml_cohort(
            2,
            {MAP: "Passed"},
            expected=(MAP,) if change == "inventory" else (MAP, ROUTE),
            digest="b" if change == "inputs" else "a",
        )
    )
    affected = ["unknown"] if change == "affected" else []

    with pytest.raises(
        ValueError, match="differs? across cohorts|checks must be in the fixture inventory"
    ):
        selection_quality(narrow, full, affected)
