"""Execute the scheduled canary's source selection against committed fixture history."""

import json
import os
from pathlib import Path
import subprocess
import sys

import yaml
import pytest

from tests.test_check_source_pin_drift import commit, history, set_pin
from tests.test_update_source_pins import put


ROOT = Path(__file__).resolve().parents[2]


def selection(
    repo: Path, temporary: Path, expected_sha: str | None = None
) -> subprocess.CompletedProcess[str]:
    config = yaml.safe_load(
        (ROOT / ".github/workflows/distribution-canary.yml").read_text(encoding="utf-8")
    )
    program = next(
        step["run"] for step in config["jobs"]["canary"]["steps"] if step.get("id") == "source"
    )
    return subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=repo,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "tools"),
            "GITHUB_OUTPUT": str(temporary / "output"),
            "GITHUB_STEP_SUMMARY": str(temporary / "summary"),
            "EXPECTED_SHA": expected_sha
            or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_canary_selects_committed_launcher_pin_and_ignores_dirty_metadata(history, tmp_path):
    repo, pin, checked = history
    put(repo, "MCPForUnity/package.json", '{"mcpServerSource": "https://invalid/beta.zip"}')
    result = selection(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads((repo / ".tmp/distribution-canary/source.json").read_text())
    assert report["checked_commit"] == checked
    assert report["server_commit"] == pin
    assert report["server_source"].endswith(f"/archive/{pin}.zip#subdirectory=Server")
    assert f"server_commit={pin}\n" in (tmp_path / "output").read_text()
    assert report["server_source"] in (tmp_path / "summary").read_text()


def test_canary_rejects_mutable_committed_source_before_install(history, tmp_path):
    repo, _, _ = history
    set_pin(repo, "beta")
    commit(repo)
    result = selection(repo, tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / "output").exists()
    assert not (repo / ".tmp/distribution-canary/source.json").exists()


def test_canary_schedules_supported_platform_floor_and_separate_evidence():
    # Given the shipped archive's scheduled validation configuration.
    config = yaml.safe_load(
        (ROOT / ".github/workflows/distribution-canary.yml").read_text(encoding="utf-8")
    )
    job = config["jobs"]["canary"]

    # When GitHub expands the matrix, every supported platform must check Python 3.11.
    rows = job["strategy"]["matrix"]["include"]
    observed = {(row["os"], row["python-version"]) for row in rows}
    assert {
        ("ubuntu-26.04", "3.11"),
        ("windows-latest", "3.11"),
        ("macos-26-intel", "3.11"),
        ("ubuntu-26.04", "3.14"),
    } <= observed
    assert job["runs-on"] == "${{ matrix.os }}"
    setup = next(step for step in job["steps"] if step.get("name") == "Install uv")
    assert setup["with"]["python-version"] == "${{ matrix.python-version }}"
    upload = next(
        step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    # Then every row retains distinct evidence instead of colliding in one artifact.
    names = {
        upload["with"]["name"]
        .replace("${{ matrix.os }}", os_name)
        .replace("${{ matrix.python-version }}", version)
        for os_name, version in observed
    }
    assert len(names) == len(observed)


@pytest.mark.parametrize(
    "path", ["MCPForUnity/package.json", "manifest.json", "release-source-inputs.json"]
)
def test_pin_change_selects_full_selected_distribution_platform_matrix(path):
    from tests.test_ci_trigger_policy import selected

    assert selected("distribution-canary.yml", "pull_request", "beta", path, "synchronize")
    assert selected("distribution-canary.yml", "push", "beta", path)
    assert not selected("distribution-canary.yml", "push", "main", path)
    config = yaml.safe_load((ROOT / ".github/workflows/distribution-canary.yml").read_text())
    matrix = config["jobs"]["canary"]["strategy"]["matrix"]
    rows = matrix["include"]
    assert {(row["os"], row["python-version"]) for row in rows} == {
        ("ubuntu-26.04", "3.11"),
        ("windows-latest", "3.11"),
        ("macos-26-intel", "3.11"),
        ("ubuntu-26.04", "3.14"),
        ("ubuntu-26.04", "3.15"),
    }
    assert len(rows) == 5
    steps = config["jobs"]["canary"]["steps"]
    behavior = next(step for step in steps if step.get("name", "").startswith("Exercise installed"))
    assert "if" not in behavior
    assert "tools/check_server_archive.py" in behavior["run"]
    assert "tools/check_server_startup.py" in behavior["run"]


def test_canary_rejects_another_checkout_before_install_or_evidence(history, tmp_path):
    repo, _, _ = history
    result = selection(repo, tmp_path, expected_sha="f" * 40)
    assert result.returncode != 0
    assert "exact requested SHA" in result.stderr
    assert not (tmp_path / "output").exists()
    assert not (repo / ".tmp/distribution-canary/source.json").exists()


@pytest.mark.parametrize(
    "reusable,event,ref,licensed,expected",
    [
        (False, "push", "refs/heads/beta", "false", True),
        (False, "push", "refs/heads/beta", "", True),
        (False, "push", "refs/heads/beta", "true", False),
        (True, "push", "refs/heads/beta", "true", True),
        (True, "push", "refs/heads/beta", "false", True),
        (True, "workflow_dispatch", "refs/heads/beta", "true", True),
        (False, "pull_request", "refs/pull/1/merge", "true", True),
        (False, "workflow_dispatch", "refs/heads/beta", "true", True),
        (False, "schedule", "refs/heads/beta", "true", True),
        (False, "push", "refs/heads/other", "true", True),
    ],
)
def test_canary_deduplicates_only_direct_licensed_beta_pushes(
    reusable, event, ref, licensed, expected
):
    from tests.test_ci_trigger_policy import workflow

    config = workflow("distribution-canary.yml")
    triggers = config.get("on", config.get(True))
    marker = triggers["workflow_call"]["inputs"]["qualification_run"]
    assert marker["type"] == "boolean" and marker["default"] is False
    expression = config["jobs"]["canary"]["if"]
    for key, value in {
        "inputs.qualification_run": reusable,
        "github.event_name": event,
        "github.ref": ref,
        "vars.UNITY_RUN_LICENSED_TESTS": licensed,
    }.items():
        expression = expression.replace(key, repr(value))
    assert eval(expression.replace("||", " or "), {"__builtins__": {}}, {}) is expected
    for event_name in ("pull_request", "workflow_dispatch", "schedule", "push"):
        assert event_name in triggers
    qualification = workflow("release-qualification.yml")["jobs"]["distribution"]
    assert qualification["with"]["qualification_run"] is True
    assert qualification["with"]["ref"] == "${{ github.sha }}"
    assert qualification["uses"] == "./.github/workflows/distribution-canary.yml"
    assert "vars.UNITY_RUN_LICENSED_TESTS == 'true'" in qualification["if"]
    assert "github.event_name == 'workflow_dispatch'" in qualification["if"]


def test_direct_canary_push_inputs_are_covered_by_qualification():
    from tests.test_ci_trigger_policy import selected, workflow

    config = workflow("distribution-canary.yml")
    for path in config.get("on", config.get(True))["push"]["paths"]:
        assert selected("release-qualification.yml", "push", "beta", path), path
    for job in config["jobs"].values():
        assert not job.get("continue-on-error")
        assert not job["strategy"]["fail-fast"]
        assert all(not step.get("continue-on-error") for step in job["steps"])
