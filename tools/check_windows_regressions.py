"""Run the hermetic Windows ownership, termination and file-sharing regressions.

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
    "tools/tests/test_native_preference_process.py::test_controller_process_contract": 22,
    "tools/tests/test_process_observation.py::test_owned_windows_sampler_captures_and_closes_on_every_path": 4,
    "tools/tests/test_play_scenario_player_e2e.py::test_actual_owned_timeout_reaps_process": 1,
    "tools/tests/test_play_scenario_player_e2e.py::test_keyboard_interrupt_reaps_actual_owned_native_descendant": 1,
    "tools/tests/test_play_scenario_player_e2e.py::test_owned_stdout_pipe_is_closed_after_actual_process": 3,
    "Server/tests/test_play_scenario_player_assurance.py::test_windows_reader_allows_atomic_replacement_while_open": 1,
}


def check_report(path: Path) -> int:
    """Require an executed, successful result for every selected regression family."""
    cases = list(ElementTree.parse(path).iter("testcase"))
    if not cases or any(
        case.find(kind) is not None for case in cases for kind in ("skipped", "failure", "error")
    ):
        raise ValueError("Windows regressions must execute without skips, failures or errors")
    counts = Counter(case.attrib["name"].split("[", 1)[0] for case in cases)
    expected = {node.split("::")[1]: count for node, count in CASES.items()}
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
            str(path) for path in (ROOT, ROOT / "tools", ROOT / "Server/src")
        ),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
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
