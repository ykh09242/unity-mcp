"""Run the manifest's Windows-only VFX methods in the isolated CI project."""

import argparse
import json
from pathlib import Path

from check_unity_test_results import check_results
from native_test_inputs import _execution_filter
from player_e2e_process import Invocation, execute


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools/unity-optional-tests.json"


def windows_methods(manifest: dict) -> list[str]:
    suites = [suite for suite in manifest["suites"] if suite["id"] == "vfx-graph"]
    if manifest["schemaVersion"] != 1 or len(suites) != 1:
        raise ValueError("Expected exactly one vfx-graph suite in optional test schema 1")
    suite = suites[0]
    methods = suite.get("requiredTestsWindows")
    if not isinstance(methods, list) or not methods:
        raise ValueError("VFX requires a nonempty Windows test inventory")
    for name in methods:
        if (
            not isinstance(name, str)
            or name.rpartition(".")[0] != suite["filter"]
            or not all(part.isidentifier() for part in name.split("."))
        ):
            raise ValueError("Required Windows method must belong to the VFX fixture")
    if len(set(methods)) != len(methods) or set(methods).intersection(suite["requiredTests"]):
        raise ValueError("Duplicate required Windows VFX method")
    return methods


def run(editor: Path, project: Path, evidence: Path) -> int:
    methods = windows_methods(json.loads(MANIFEST.read_text(encoding="utf-8")))
    evidence = evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    results = evidence / "results.xml"
    command = [
        str(editor.resolve()),
        "-batchmode",
        "-projectPath",
        str(project.resolve()),
        "-runTests",
        "-testPlatform",
        "EditMode",
        "-testFilter",
        _execution_filter(methods),
        "-testResults",
        str(results),
        "-logFile",
        "-",
    ]
    completed = execute(Invocation(command, evidence, timeout=40 * 60))
    success = completed["actual_exit_code"] == 0 and not completed["forced_termination"]
    return check_results(results, "success" if success else "failure", methods)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, required=True)
    args = parser.parse_args()
    return run(args.editor, ROOT / ".unity-ci/windows-vfx/project", ROOT / ".unity-ci/vfx-evidence")


if __name__ == "__main__":
    raise SystemExit(main())
