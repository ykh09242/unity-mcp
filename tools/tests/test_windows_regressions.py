"""The Windows regression gate cannot certify missing or skipped execution."""

from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml

from tools.check_windows_regressions import CASES, check_report


def test_windows_selection_includes_proactor_cleanup_and_replacement_retry_boundaries():
    assert (
        CASES[
            "Server/tests/test_cli_doctor.py::test_live_server_releases_accepted_socket_when_windows_shutdown_resets"
        ]
        == 1
    )
    assert (
        CASES[
            "Server/tests/test_play_scenario_player_assurance.py::test_snapshot_reader_retries_only_bounded_replacement_errors"
        ]
        == 4
    )
    assert (
        CASES[
            "Server/tests/test_play_scenario_player_assurance.py::test_snapshot_retry_stops_at_elapsed_deadline"
        ]
        == 3
    )
    assert sum(CASES.values()) == 46


@pytest.mark.parametrize(
    "name,count",
    [
        ("test_bundle_launches_cmd_shim_and_preserves_metacharacter_paths", 1),
        ("test_cmd_fixture_has_an_observable_injection_negative_control", 1),
        ("test_cmd_shim_rejects_impossible_path_characters", 3),
        ("test_cmd_shim_failure_preserves_exit_code_and_existing_bundle", 1),
    ],
)
def test_windows_selection_requires_real_packaging_command_boundaries(name, count):
    # Given the real Windows packaging cases and their parametrized execution counts.
    node = f"tools/tests/test_generate_mcpb_windows.py::{name}"
    # When the hermetic gate selects its mandatory inventory.
    # Then every packaging boundary must execute, with no missing or skipped cases.
    assert CASES.get(node) == count


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
