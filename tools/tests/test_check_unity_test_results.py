"""Exercise the CI gate with real result files and runner outcomes."""

from pathlib import Path
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


GATE = Path(__file__).resolve().parents[1] / "check_unity_test_results.py"
COUNTS = 'inconclusive="0" skipped="0"'
PASSING = '<test-run result="Passed" total="2" passed="1" failed="0" inconclusive="0" skipped="1"><test-case result="Passed"/><test-case result="Skipped"/></test-run>'
# <test-run> attributes exactly as Unity wrote them on green beta run 33978935244 (all four Unity versions):
# a clean run that contains [Ignore]d tests reports result="Skipped:Ignored", not "Passed".
UNITY_CLEAN_RUN = (
    '<test-run id="2" testcasecount="1229" result="Skipped:Ignored" total="1229" passed="1162" failed="0" inconclusive="0" skipped="67" asserts="0" engine-version="3.5.0.0">'
    "<test-suite>"
    + '<test-case result="Passed"/>' * 1162
    + '<test-case result="Skipped"/>' * 67
    + "</test-suite></test-run>"
)
# Beta run 29283113713 (6000.0.75f1), before #1294 moved ManageGraphicsTests off Assume.That.
UNITY_INCONCLUSIVE_RUN = """<test-run id="2" testcasecount="1166" result="Skipped:Ignored" total="1166" passed="1100" failed="0" inconclusive="18" skipped="48" asserts="0" engine-version="3.5.0.0">
  <test-case fullname="MCPForUnityTests.Editor.Tools.ManageGraphicsTests.FeatureAdd_InvalidType_ReturnsError" result="Inconclusive">
    <reason><message><![CDATA[URP not available — skipping.
::error::injected]]></message></reason>
  </test-case></test-run>"""


def run_gate(tmp_path, xml, outcome="success", required_tests=()):
    results = tmp_path / "editmode-results.xml"
    if xml is not None:
        results.write_text(xml, encoding="utf-8")
    command = [sys.executable, str(GATE), str(results), "--runner-outcome", outcome]
    for required in required_tests:
        command.extend(("--require-test", required))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


@pytest.mark.parametrize("xml", [PASSING, UNITY_CLEAN_RUN], ids=["passing", "unity-ignored"])
def test_successful_runner_and_completed_results_pass(tmp_path, xml):
    result = run_gate(tmp_path, xml)
    assert result.returncode == 0, result.stdout
    assert "failed, 0 inconclusive" in result.stdout


@pytest.mark.parametrize(
    "records", ["", '<test-suite result="Passed"/>', '<test-case result="Passed"/>' * 2]
)
def test_summary_cannot_claim_passes_without_matching_test_cases(tmp_path, records):
    xml = f'<test-run result="Passed" total="1" passed="1" failed="0" {COUNTS}>{records}</test-run>'
    result = run_gate(tmp_path, xml)
    assert result.returncode == 1
    assert "passing test-case records" in result.stdout


# Unity exits 2 when any test is Inconclusive, so with githubToken "" game-ci fails the step and CI
# passes "failure"; that run logged "0 failed" and then "Exiting with code 2". A local caller may pass
# "success". Either way the gate must fail and name the inconclusive test.
@pytest.mark.parametrize("outcome", ["failure", "success"])
def test_inconclusive_tests_fail_and_are_named(tmp_path, outcome):
    result = run_gate(tmp_path, UNITY_INCONCLUSIVE_RUN, outcome)
    assert result.returncode == 1, result.stdout
    assert "18 inconclusive, 48 skipped (total: 1166)" in result.stdout
    assert (
        "::error title=Inconclusive: MCPForUnityTests.Editor.Tools.ManageGraphicsTests"
        in result.stdout
    )
    assert "URP not available" in result.stdout
    assert "use Assert.Ignore" in result.stdout
    assert "\n::error::injected" not in result.stdout


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", ""])
def test_passing_xml_cannot_hide_runner_failure(tmp_path, outcome):
    result = run_gate(tmp_path, PASSING, outcome)
    assert result.returncode == 1
    assert "runner did not succeed" in result.stdout


@pytest.mark.parametrize(
    "xml",
    [
        None,
        "not XML",
        f'<coverage total="3" passed="3" failed="0" {COUNTS}/>',
        '<test-run result="Passed"/>',
        f'<test-run result="Passed" total="invalid" passed="1" failed="0" {COUNTS}/>',
        f'<test-run result="Passed" total="1" passed="-1" failed="0" {COUNTS}/>',
        f'<test-run result="Passed" total="1" passed="2" failed="0" {COUNTS}/>',
        # Outcome buckets must account for every test, not just stay under total.
        f'<test-run result="Passed" total="100" passed="1" failed="0" {COUNTS}/>',
        '<test-run result="Passed" total="2" passed="1" failed="0" inconclusive="-1" skipped="2"/>',
        '<test-run result="Passed" total="1" passed="1" failed="0" skipped="0"/>',
        '<test-run result="Passed" total="1" passed="1" failed="0" inconclusive="0"/>',
    ],
)
def test_missing_or_invalid_results_fail(tmp_path, xml):
    result = run_gate(tmp_path, xml)
    assert result.returncode == 1
    assert "Cannot validate" in result.stdout


@pytest.mark.parametrize(
    "xml",
    [
        f'<test-run result="Failed" total="1" passed="1" failed="0" {COUNTS}/>',
        f'<test-run result="Failed(Child)" total="1" passed="1" failed="0" {COUNTS}/>',
        f'<test-run result="Failed:Cancelled" total="1" passed="1" failed="0" {COUNTS}/>',
        f'<test-run result="Passed" total="1" passed="0" failed="1" {COUNTS}/>',
        f'<test-run result="Cancelled" total="1" passed="1" failed="0" {COUNTS}/>',
        f'<test-run result="Warning" total="1" passed="1" failed="0" {COUNTS}/>',
        '<test-run result="Inconclusive" total="3" passed="0" failed="0" inconclusive="3" skipped="0"/>',
        f'<test-run total="1" passed="1" failed="0" {COUNTS}/>',
        f'<test-run result="Passed" total="1" passed="1" failed="0" {COUNTS}><test-case result="Failed"/></test-run>',
    ],
)
def test_suite_or_case_failure_is_not_a_pass(tmp_path, xml):
    result = run_gate(tmp_path, xml)
    assert result.returncode == 1
    assert "unsuccessful test run" in result.stdout


@pytest.mark.parametrize(
    "result_state, inconclusive, skipped",
    [
        ("Passed", 0, 0),
        ("Skipped:Ignored", 0, 3),
        ("Skipped:Ignored", 2, 1),
    ],
)
def test_runs_without_a_passing_test_fail(tmp_path, result_state, inconclusive, skipped):
    total = inconclusive + skipped
    xml = (
        f'<test-run result="{result_state}" total="{total}" passed="0" failed="0" '
        f'inconclusive="{inconclusive}" skipped="{skipped}"/>'
    )
    result = run_gate(tmp_path, xml)
    assert result.returncode == 1
    assert "did not execute" in result.stdout


def test_failure_details_cannot_inject_workflow_commands(tmp_path):
    xml = f"""<test-run result="Failed" total="1" passed="0" failed="1" {COUNTS}>
      <test-case result="Failed" fullname="Suite:Name,Percent%">
        <failure><message>First line
::warning::injected</message><stack-trace>trace
::error::injected</stack-trace></failure>
      </test-case></test-run>"""
    result = run_gate(tmp_path, xml)
    assert result.returncode == 1
    assert "Suite%3AName%2CPercent%25" in result.stdout
    assert "\n::warning::injected" not in result.stdout
    assert "\n::error::injected" not in result.stdout
    assert "%0A::warning::injected" in result.stdout


REQUIRED_METHOD = "MCPForUnityTests.OptionalPackageTests.PackageFeature_Works"


def required_result_xml(cases):
    # An unrelated pass must never hide required cases that did not execute.
    records = [("OtherSuite.OtherTest", "Passed"), *cases]
    passed = sum(result == "Passed" for _, result in records)
    failed = sum(result == "Failed" for _, result in records)
    root = ET.Element(
        "test-run",
        result="Failed" if failed else "Skipped:Ignored",
        total=str(len(records)),
        passed=str(passed),
        failed=str(failed),
        inconclusive="0",
        skipped=str(len(records) - passed - failed),
    )
    for fullname, result in records:
        ET.SubElement(root, "test-case", fullname=fullname, result=result)
    return ET.tostring(root, encoding="unicode")


@pytest.mark.parametrize("state", ["Skipped", "Explicit", "NotRunnable", "", "Passed:Ignored"])
def test_required_method_must_actually_pass(tmp_path, state):
    xml = required_result_xml([(REQUIRED_METHOD, state)])
    assert run_gate(tmp_path, xml).returncode == 0

    result = run_gate(tmp_path, xml, required_tests=[REQUIRED_METHOD])

    assert result.returncode == 1
    assert "Required test did not pass" in result.stdout
    assert REQUIRED_METHOD in result.stdout


@pytest.mark.parametrize("other_name", ["OtherSuite.OtherTest", REQUIRED_METHOD + "Extra"])
def test_required_method_cannot_be_missing_or_match_longer_method_names(tmp_path, other_name):
    result = run_gate(
        tmp_path,
        required_result_xml([(other_name, "Passed")]),
        required_tests=[REQUIRED_METHOD],
    )

    assert result.returncode == 1
    assert "Required test was not recorded" in result.stdout


@pytest.mark.parametrize("record", ["suite", "name-only"])
def test_required_method_needs_fullname_on_an_actual_test_case(tmp_path, record):
    root = ET.fromstring(required_result_xml([]))
    if record == "suite":
        ET.SubElement(root, "test-suite", fullname=REQUIRED_METHOD, result="Passed")
    else:
        root.find("test-case").set("name", REQUIRED_METHOD)
    result = run_gate(
        tmp_path, ET.tostring(root, encoding="unicode"), required_tests=[REQUIRED_METHOD]
    )

    assert result.returncode == 1
    assert "Required test was not recorded" in result.stdout


def test_failed_required_method_is_named_even_when_suite_already_failed(tmp_path):
    result = run_gate(
        tmp_path,
        required_result_xml([(REQUIRED_METHOD, "Failed")]),
        required_tests=[REQUIRED_METHOD],
    )

    assert result.returncode == 1
    assert "Required test did not pass" in result.stdout


@pytest.mark.parametrize("states", [["Skipped", "Explicit"], ["Passed", "Skipped"]])
def test_every_parameterized_required_case_must_pass(tmp_path, states):
    result = run_gate(
        tmp_path,
        required_result_xml(
            [(f"{REQUIRED_METHOD}({index})", state) for index, state in enumerate(states)]
        ),
        required_tests=[REQUIRED_METHOD],
    )

    assert result.returncode == 1
    assert "Required test did not pass" in result.stdout


def test_repeated_requirements_accept_exact_and_all_parameterized_passes(tmp_path):
    second_method = "Suite.SecondFixture.SecondMethod"
    result = run_gate(
        tmp_path,
        required_result_xml(
            [
                (REQUIRED_METHOD + "(1)", "Passed"),
                (REQUIRED_METHOD + "(2)", "Passed"),
                (second_method, "Passed"),
            ]
        ),
        required_tests=[REQUIRED_METHOD, second_method],
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_repeated_requirements_cannot_drop_an_earlier_missing_method(tmp_path):
    result = run_gate(
        tmp_path,
        required_result_xml([]),
        required_tests=[REQUIRED_METHOD, "OtherSuite.OtherTest"],
    )

    assert result.returncode == 1
    assert "Required test was not recorded" in result.stdout


@pytest.mark.parametrize("name", ["", " ", "Method", "Suite..Method", "Suite.Method()", "Suite.*"])
def test_invalid_required_method_name_fails(tmp_path, name):
    result = run_gate(tmp_path, PASSING, required_tests=[name])

    assert result.returncode == 1
    assert "Invalid required test method" in result.stdout


def test_required_case_diagnostics_escape_test_controlled_names_and_results(tmp_path):
    malicious_name = REQUIRED_METHOD + "(Suite:Name,Percent%\n::error::injected)"
    malicious_result = "Skipped%\n::warning::injected"
    result = run_gate(
        tmp_path,
        required_result_xml([(malicious_name, malicious_result)]),
        required_tests=[REQUIRED_METHOD],
    )

    assert result.returncode == 1
    assert "Suite%3AName%2CPercent%25%0A%3A%3Aerror%3A%3Ainjected" in result.stdout
    assert "Skipped%25%0A::warning::injected" in result.stdout
    assert "\n::error::injected" not in result.stdout
    assert "\n::warning::injected" not in result.stdout


def test_invalid_required_name_diagnostic_cannot_inject_workflow_command(tmp_path):
    result = run_gate(tmp_path, PASSING, required_tests=["Suite.Bad%\n::warning::injected"])

    assert result.returncode == 1
    assert "Bad%25%0A::warning::injected" in result.stdout
    assert "\n::warning::injected" not in result.stdout
