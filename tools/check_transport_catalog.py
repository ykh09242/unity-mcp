"""Run the focused transport and catalog regressions with the Server dev environment."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final
from xml.etree import ElementTree

ROOT: Final = Path(__file__).resolve().parents[1]
CASES: Final = (
    "tests/http/test_inventory_reconnect.py::test_inventory_does_not_wait_for_disconnected_persisted_selection",
    "tests/http/test_inventory_reconnect.py::test_read_instances_checks_authority_and_ignores_stale_project_and_principal",
    "tests/test_remote_tool_membership.py::test_resource_and_listing_requests_do_not_capture_tool_identity",
    "tests/test_workflow_discovery.py::test_internal_catalog_requires_the_scoped_method_and_server",
    "tests/test_workflow_discovery.py::test_real_sdk_lists_external_catalog_with_unity_routing",
    "tests/test_workflow_discovery.py::test_search_and_normalized_resource_branch_stay_within_the_original_request",
    "tests/test_workflow_discovery.py::test_resource_catalog_keeps_per_call_project_selection",
    "tests/test_workflow_discovery.py::test_transport_tool_is_discoverable_from_its_actual_wire_schema",
)


def main() -> int:
    """Run the exact suite in its own pytest process and preserve its exit status."""
    with TemporaryDirectory(prefix="unity-mcp-transport-catalog-") as directory:
        output = Path(directory)
        report = output / "results.xml"
        env = {
            **os.environ,
            "PYTEST_ADDOPTS": "",
            "UNITY_MCP_RUN_HTTP_TESTS": "1",
            "UNITY_MCP_DISABLE_TELEMETRY": "1",
            "UNITY_MCP_LOG_DIR": str(output / "logs"),
        }
        result = subprocess.run(
            [
                sys.executable,
                "-W",
                "error",
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(output / "pytest"),
                "--junitxml",
                str(report),
                *CASES,
            ],
            cwd=ROOT / "Server",
            env=env,
            check=False,
        )
        if result.returncode:
            return result.returncode
        cases = list(ElementTree.parse(report).iter("testcase"))
        if not cases or any(case.find("skipped") is not None for case in cases):
            print("Transport catalog regressions must execute without skips", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
