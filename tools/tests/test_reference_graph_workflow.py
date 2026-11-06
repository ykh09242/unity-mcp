"""Native spool ownership and byte-range leases execute on POSIX and Windows CI."""

from pathlib import Path
import shlex

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_reference_graph_matrix_executes_complete_native_spool_regressions():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/python-tests.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["reference_graph"]
    platforms = job["strategy"]["matrix"]["os"]
    assert all(
        any(platform.startswith(prefix) for platform in platforms)
        for prefix in ("windows-", "ubuntu-", "macos-")
    )
    step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Install and exercise isolated reference graph"
    )
    arguments = shlex.split(step["run"])
    tests = arguments[arguments.index("pytest") + 1 :]
    assert tests.count("tests/test_reference_graph_native_spools.py") == 1
    assert "-k" not in tests and "--ignore" not in tests
    assert not step.get("continue-on-error") and "if" not in step
