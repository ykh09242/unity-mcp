"""Run hermetic Windows ownership, file-sharing and npm command-shim regressions.

Run with the existing Server environment: python tools/check_windows_regressions.py
--output .tmp/windows-hermetic. No Unity installation, license or live Player is used.
"""

import argparse
from collections import Counter
import os
from pathlib import Path
import subprocess
import sys
from typing import Final
from xml.etree import ElementTree


ROOT: Final = Path(__file__).resolve().parents[1]
CASES: Final = {
    # Collect Server helpers before tools tests adjust their standalone import paths.
    "Server/tests/test_cli_doctor.py::test_live_server_releases_accepted_socket_when_windows_shutdown_resets": 1,
    "Server/tests/test_play_scenario_player_assurance.py::test_snapshot_reader_retries_only_bounded_replacement_errors": 4,
    "Server/tests/test_play_scenario_player_assurance.py::test_snapshot_retry_stops_at_elapsed_deadline": 3,
    "Server/tests/test_focus_nudge.py::TestWindowsFocusScripts::test_capture_has_no_native_return_value_in_stdout": 4,
    "Server/tests/test_focus_nudge.py::TestWindowsFocusScripts::test_no_foreground_window": 1,
    "Server/tests/test_focus_nudge.py::TestWindowsFocusScripts::test_restores_by_handle_after_title_changes": 3,
    "Server/tests/test_focus_nudge.py::TestWindowsFocusScripts::test_reports_closed_window_or_denied_activation": 2,
    "Server/tests/test_focus_nudge.py::TestWindowsFocusScripts::test_rejects_title_only_activation": 2,
    "Server/tests/test_focus_nudge.py::TestWindowsFocusScripts::test_exact_project_activation_still_works": 2,
    "Server/tests/test_focus_nudge.py::TestWindowsFocusScripts::test_rejects_successful_native_call_that_did_not_change_foreground": 1,
    "Server/tests/test_cli_followup_context.py::test_platform_shell_preserves_apostrophes_metacharacters_and_spaced_arguments": 1,
    "Server/tests/test_http_security_wrapper.py::test_worker_progress_updates_while_windows_reader_holds_current_snapshot": 1,
    "Server/tests/test_http_security_wrapper.py::test_owned_subprocess_preserves_exit_and_captured_text": 1,
    "Server/tests/test_http_security_wrapper.py::test_owned_subprocess_timeout_preserves_both_output_streams": 2,
    "Server/tests/test_http_security_wrapper.py::test_timeout_terminates_owned_controller_worker_and_grandchild": 2,
    "tools/tests/test_native_preference_process.py::test_controller_process_contract": 22,
    "tools/tests/test_process_observation.py::test_owned_windows_sampler_captures_and_closes_on_every_path": 4,
    "tools/tests/test_play_scenario_player_e2e.py::test_actual_owned_timeout_reaps_process": 1,
    "tools/tests/test_play_scenario_player_e2e.py::test_keyboard_interrupt_reaps_actual_owned_native_descendant": 1,
    "tools/tests/test_play_scenario_player_e2e.py::test_owned_stdout_pipe_is_closed_after_actual_process": 3,
    "Server/tests/test_play_scenario_player_assurance.py::test_windows_reader_allows_atomic_replacement_while_open": 1,
    "tools/tests/test_generate_mcpb_windows.py::test_bundle_launches_cmd_shim_and_preserves_metacharacter_paths": 1,
    "tools/tests/test_generate_mcpb_windows.py::test_cmd_fixture_has_an_observable_injection_negative_control": 1,
    "tools/tests/test_generate_mcpb_windows.py::test_cmd_shim_rejects_impossible_path_characters": 3,
    "tools/tests/test_generate_mcpb_windows.py::test_cmd_shim_failure_preserves_exit_code_and_existing_bundle": 1,
    "tools/tests/test_compile_check.py::test_windows_compiler_layout_uses_bundled_runtime": 1,
}


def check_report(path: Path) -> int:
    """Require an executed, successful result for every selected regression family."""
    cases = list(ElementTree.parse(path).iter("testcase"))
    if not cases or any(
        case.find(kind) is not None for case in cases for kind in ("skipped", "failure", "error")
    ):
        raise ValueError("Windows regressions must execute without skips, failures or errors")
    counts = Counter(case.attrib["name"].split("[", 1)[0] for case in cases)
    expected = {node.rsplit("::", 1)[-1]: count for node, count in CASES.items()}
    if counts != expected:
        raise ValueError(
            f"Windows regression execution count mismatch: {dict(counts)} != {expected}"
        )
    return len(cases)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("This regression gate requires Windows")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = output / "results.xml"
    report.unlink(missing_ok=True)
    environment = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "1",
        "UNITY_MCP_LOG_DIR": str(output / "logs"),
        "PYTHONPATH": os.pathsep.join(
            str(path) for path in (ROOT / "Server", ROOT / "Server/src", ROOT, ROOT / "tools")
        ),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "asyncio_default_fixture_loop_scope=function",
            "--import-mode=importlib",
            *CASES,
            "-q",
            "-W",
            "error",
            "--tb=short",
            "-o",
            f"cache_dir={output / 'cache'}",
            "--basetemp",
            str(output / "pytest"),
            "--junitxml",
            str(report),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        timeout=300,
    )
    if result.returncode:
        return result.returncode
    try:
        count = check_report(report)
    except (OSError, ValueError, ElementTree.ParseError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Windows hermetic regressions: {count} executed; zero skips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
