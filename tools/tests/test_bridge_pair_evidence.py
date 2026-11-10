"""Bridge plans distinguish candidate coverage from the installed component pair."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

import bridge_pair_evidence as bridge
from tests.test_check_source_pin_drift import commit, history, set_pin
from tests.test_update_source_pins import put


ROOT = Path(__file__).resolve().parents[2]


def test_ordinary_runtime_change_keeps_one_candidate_leg(history):
    repo, pin, base = history
    put(repo, "Server/src/main.py", "# candidate runtime\n")
    candidate = commit(repo)
    pairs = bridge.plan(repo, "auto", base)
    assert len(pairs) == 1
    assert asdict(pairs[0]) == {
        "mode": "candidate",
        "package_commit": candidate,
        "selected_server_commit": pin,
        "tested_server_commit": candidate,
        "server_source": "Server",
    }


def test_pin_metadata_change_adds_selected_archive_leg(history):
    repo, _, base = history
    put(repo, "Server/src/main.py", "# reviewed runtime\n")
    pin = commit(repo)
    set_pin(repo, pin)
    package = commit(repo)
    pairs = bridge.plan(repo, "auto", base)
    assert [pair.mode for pair in pairs] == ["candidate", "selected"]
    selected = pairs[1]
    assert selected.package_commit == package
    assert selected.selected_server_commit == selected.tested_server_commit == pin
    assert selected.server_source == bridge.BASE + f"/archive/{pin}.zip#subdirectory=Server"


@pytest.mark.parametrize("selection,count", [("candidate", 1), ("selected", 1), ("both", 2)])
def test_manual_release_selection_is_bounded(history, selection, count):
    pairs = bridge.plan(history[0], selection)
    assert len(pairs) == count
    assert (pairs[0].mode == "selected") == (selection == "selected")


def test_inconsistent_source_metadata_fails_before_planning(history):
    repo, _, _ = history
    put(repo, "release-source-inputs.json", "{}")
    commit(repo)
    with pytest.raises(bridge.EvidenceError):
        bridge.plan(repo, "selected")


def test_cli_reports_skipped_without_claiming_runtime_success(history, tmp_path):
    output = tmp_path / "outputs"
    summary = tmp_path / "summary"
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(bridge.__file__)),
            "report",
            "--repo",
            str(history[0]),
            "--mode",
            "selected",
            "--outcome",
            "skipped",
            "--report",
            str(report),
            "--summary",
            str(summary),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(report.read_text())
    assert evidence["execution_status"] == "skipped"
    assert evidence["live_bridge_passed"] is False
    assert "skipped" in summary.read_text()
    assert not output.exists()


def test_workflow_installs_selected_archive_without_checkout_source_shadow():
    config = yaml.safe_load((ROOT / ".github/workflows/e2e-bridge.yml").read_text())
    events = config.get("on", config.get(True))
    assert {"MCPForUnity/package.json", "manifest.json", "release-source-inputs.json"} <= set(
        events["pull_request"]["paths"]
    )
    assert events["workflow_dispatch"]["inputs"]["pair_mode"]["options"] == [
        "candidate",
        "selected",
        "both",
    ]
    job = config["jobs"]["e2e-bridge"]
    assert job["strategy"]["max-parallel"] == 1
    assert "needs.pairs.outputs.matrix" in job["strategy"]["matrix"]
    steps = job["steps"]
    install = next(step for step in steps if step.get("name") == "Install MCP server")
    assert "uv pip install -e Server" in install["run"]
    assert 'uv pip install "$SERVER_SOURCE"' in install["run"]
    assert "UNITY_MCP_BRIDGE_SERVER_MODE=installed" in install["run"]
    assert "verify-installed" in install["run"]
    harness = next(step for step in steps if step.get("id") == "harness")
    evidence = next(step for step in steps if step.get("name") == "Record bridge pair outcome")
    assert steps.index(install) < steps.index(harness) < steps.index(evidence)
    assert evidence["if"] == "always()"
    assert "steps.harness.outcome" in evidence["env"]["BRIDGE_OUTCOME"]
    upload = next(step for step in steps if step.get("name") == "Upload E2E report")
    assert "reports/bridge-pair.json" in upload["with"]["path"]


@pytest.mark.parametrize("mode,uses_checkout", [("candidate", True), ("installed", False)])
def test_harness_source_mode_preserves_default_and_allows_installed(
    monkeypatch, mode, uses_checkout
):
    import local_harness

    monkeypatch.setenv("UNITY_MCP_BRIDGE_SERVER_MODE", mode)
    monkeypatch.setattr(
        sys, "path", [entry for entry in sys.path if entry != str(local_harness.SERVER_SRC)]
    )
    local_harness._ensure_src_on_path()
    assert (str(local_harness.SERVER_SRC) in sys.path) is uses_checkout


@pytest.mark.parametrize("mode,expected", [("candidate", "checkout"), ("installed", "installed")])
def test_smoke_child_imports_the_requested_server(mode, expected, tmp_path):
    script = tmp_path / "Server/tests/e2e/bridge_smoke.py"
    script.parent.mkdir(parents=True)
    script.write_bytes((ROOT / "Server/tests/e2e/bridge_smoke.py").read_bytes())
    for location, marker in [
        (tmp_path / "Server/src", "checkout"),
        (tmp_path / "site", "installed"),
    ]:
        put(location, "transport/__init__.py", "")
        put(location, "transport/legacy/__init__.py", "")
        put(
            location,
            "transport/legacy/unity_connection.py",
            f"send_command_with_retry = '{marker}'\n",
        )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy,sys; print(runpy.run_path(sys.argv[1])['send_command_with_retry'])",
            str(script),
        ],
        env={
            **os.environ,
            "UNITY_MCP_BRIDGE_SERVER_MODE": mode,
            "PYTHONPATH": str(tmp_path / "site"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("defect", ["none", "editable", "wrong_archive", "checkout_shadow"])
def test_selected_installation_provenance_rejects_shadow_and_wrong_source(
    history, tmp_path, monkeypatch, defect
):
    pair = bridge.plan(history[0], "selected")[0]
    installed = tmp_path / "site-packages"
    metadata = {
        "url": pair.server_source.split("#")[0],
        "subdirectory": "Server",
        "archive_info": {},
    }
    if defect == "editable":
        metadata["dir_info"] = {"editable": True}
    if defect == "wrong_archive":
        metadata["url"] = bridge.BASE + "/archive/" + "f" * 40 + ".zip"
    module = installed / "transport/legacy/unity_connection.py"
    if defect == "checkout_shadow":
        module = history[0] / "Server/src/transport/legacy/unity_connection.py"
    distribution = SimpleNamespace(
        version="1.0.0",
        read_text=lambda name: json.dumps(metadata),
        locate_file=lambda name: installed,
    )
    monkeypatch.setattr(bridge.importlib.metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(
        bridge.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(module))
    )
    if defect == "none":
        result = bridge.verify_installed(pair)
        assert result.module_path == str(module.resolve())
        assert result.distribution_version == "1.0.0"
    else:
        with pytest.raises(bridge.EvidenceError):
            bridge.verify_installed(pair)


def test_selected_success_without_verified_installation_is_rejected(history, tmp_path):
    assert (
        bridge.main(
            [
                "report",
                "--repo",
                str(history[0]),
                "--mode",
                "selected",
                "--outcome",
                "success",
                "--report",
                str(tmp_path / "evidence.json"),
            ]
        )
        == 2
    )


def test_cli_plan_exports_a_matrix_without_claiming_execution(history, tmp_path, monkeypatch):
    output = tmp_path / "outputs"
    report = tmp_path / "plan.json"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert (
        bridge.main(
            ["plan", "--repo", str(history[0]), "--selection", "both", "--report", str(report)]
        )
        == 0
    )
    evidence = json.loads(report.read_text())
    assert evidence["execution_status"] == "planned"
    key, matrix = output.read_text().strip().split("=", 1)
    assert key == "matrix"
    assert json.loads(matrix)["include"] == evidence["include"]
    assert [row["mode"] for row in evidence["include"]] == ["candidate", "selected"]
