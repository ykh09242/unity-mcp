"""Native spool ownership and byte-range leases execute on POSIX and Windows CI."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_reference_graph_matrix_preserves_the_full_test_inventory_in_separate_phases():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/python-tests.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["reference_graph"]
    platforms = job["strategy"]["matrix"]["os"]
    assert all(
        any(platform.startswith(prefix) for platform in platforms)
        for prefix in ("windows-", "ubuntu-", "macos-")
    )
    steps = {step["name"]: step for step in job["steps"]}
    receipt = "Preserve reference graph checkout receipt"
    install = "Install locked reference graph dependencies"
    collection = "Collect reference graph tests"
    core = "Execute reference graph core tests"
    service = "Execute reference graph service tests"
    initialization = "Verify interrupted private-store initialization"
    ordered = [receipt, install, collection, core, service, initialization]
    assert [name for name in steps if name in ordered] == ordered
    assert "github.run_attempt" in steps[receipt]["with"]["name"]
    assert "uv sync" in steps[install]["run"] and "--locked" in steps[install]["run"]
    assert "--collect-only" in steps[collection]["run"]
    assert all("continue-on-error" not in steps[name] for name in ordered if name != receipt)
    assert "if" not in steps[core] and "if" not in steps[initialization]
    assert "steps.graph_collection.outcome == 'success'" in steps[service]["if"]

    expected = {
        "native_spools",
        "store",
        "analysis",
        "semantics",
        "provenance",
        "queries",
        "mutations",
        "runtime",
        "operations",
        "external",
        "external_changes",
        "workspace",
        "policy_admission",
        "reconcile",
        "service",
    }
    files = []
    for name, group in ((core, "GRAPH_CORE_TESTS"), (service, "GRAPH_SERVICE_TESTS")):
        files.extend(job["env"][group].split())
        assert f"${group}" in steps[collection]["run"]
        assert f"${group}" in steps[name]["run"]
        assert '"${files[@]}"' in steps[name]["run"]
    assert sorted(files) == sorted(f"tests/test_reference_graph_{name}.py" for name in expected)
    for name in (collection, core, service, initialization):
        command = steps[name]["run"]
        assert "uv run --no-sync" in command and "python -W error -m pytest" in command
        assert "-k" not in command and "--ignore" not in command
