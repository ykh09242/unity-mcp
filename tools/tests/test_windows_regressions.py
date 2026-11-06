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
    assert sum(CASES.values()) == 69


def test_windows_selection_executes_bundled_compiler_runtime():
    # Given: the compiler layout case skips on Linux and needs Git Bash on Windows.
    node = "tools/tests/test_compile_check.py::test_windows_compiler_layout_uses_bundled_runtime"
    # When: the no-skips Windows gate selects its mandatory execution inventory.
    # Then: the real executable-resolution regression must run exactly once.
    assert CASES.get(node) == 1


def test_windows_selection_executes_focus_scripts_shell_roundtrip_and_open_snapshot():
    focus = {
        node.rsplit("::", 1)[-1]: count
        for node, count in CASES.items()
        if "::TestWindowsFocusScripts::" in node
    }
    assert focus == {
        "test_capture_has_no_native_return_value_in_stdout": 4,
        "test_no_foreground_window": 1,
        "test_restores_by_handle_after_title_changes": 3,
        "test_reports_closed_window_or_denied_activation": 2,
        "test_rejects_title_only_activation": 2,
        "test_exact_project_activation_still_works": 2,
        "test_rejects_successful_native_call_that_did_not_change_foreground": 1,
    }
    assert (
        CASES[
            "Server/tests/test_cli_followup_context.py::test_platform_shell_preserves_apostrophes_metacharacters_and_spaced_arguments"
        ]
        == 1
    )
    assert (
        CASES[
            "Server/tests/test_http_security_wrapper.py::test_worker_progress_updates_while_windows_reader_holds_current_snapshot"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "name,count",
    [
        ("test_owned_subprocess_preserves_exit_and_captured_text", 1),
        ("test_owned_subprocess_timeout_preserves_both_output_streams", 2),
        ("test_timeout_terminates_owned_controller_worker_and_grandchild", 2),
    ],
)
def test_windows_selection_requires_owned_http_subprocess_boundaries(name, count):
    # Given the HTTP wrapper's real owned-process cases and all output modes.
    node = f"Server/tests/test_http_security_wrapper.py::{name}"
    # When the Windows gate selects mandatory execution.
    # Then each case must run, including both parametrized timeout variants.
    assert CASES.get(node) == count


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
            ElementTree.SubElement(suite, "testcase", name=f"{node.rsplit('::', 1)[-1]}[{index}]")
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
