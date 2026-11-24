"""Replay the existing public search contracts for one arm of the replacement trial."""

import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = (
    "Server/tests/test_task_search.py",
    "Server/tests/test_search_route_groups.py",
    "Server/tests/test_task_search_languages.py",
    "Server/tests/test_localized_observation_search.py",
    "tools/tests/test_bundled_discovery_offline.py",
    "tools/tests/test_discovery_read_scope.py",
    "tools/tests/test_universal_discovery_evaluation.py",
)


def receipt(arm, xml_path):
    cases = {}
    for case in ET.parse(xml_path).iter("testcase"):
        module = case.attrib["classname"].rsplit(".", 1)[-1]
        name = case.attrib["name"]
        suite = cases.setdefault(module, {})
        if name in suite:
            raise ValueError(f"Duplicate test case in {module}")
        suite[name] = next(
            (kind for kind in ("error", "failure", "skipped") if case.find(kind) is not None),
            "passed",
        )
    if set(cases) != {Path(path).stem for path in CONTRACTS}:
        raise ValueError("Incomplete compatibility suites")
    names = sorted(f"{module}::{name}" for module, tests in cases.items() for name in tests)
    return {
        "arm": arm,
        "test_ids_sha256": sha256("\n".join(names).encode()).hexdigest(),
        "suites": {
            module: {
                "count": len(tests),
                "unsuccessful": {k: v for k, v in sorted(tests.items()) if v != "passed"},
            }
            for module, tests in sorted(cases.items())
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("shipping", "candidate"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / "Server"), str(ROOT)]
    import pytest

    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:

        class Candidate:
            def pytest_sessionstart(self):
                from services.registry import task_search
                from tools.experiments.evaluate_family_first import candidate_search

                stack.enter_context(
                    patch.object(task_search, "_semantic_catalog", candidate_search)
                )

        xml_path = Path(directory) / "contracts.xml"
        code = pytest.main(
            [
                "-q",
                "--tb=line",
                "--rootdir",
                str(ROOT),
                "--import-mode=importlib",
                "-p",
                "no:cacheprovider",
                "-o",
                "asyncio_default_fixture_loop_scope=function",
                f"--junitxml={xml_path}",
                *(str(ROOT / p) for p in CONTRACTS),
            ],
            plugins=[Candidate()] if args.arm == "candidate" else [],
        )
        if code in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
            result = receipt(args.arm, xml_path)
            args.output.write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
        return code


if __name__ == "__main__":
    raise SystemExit(main())
