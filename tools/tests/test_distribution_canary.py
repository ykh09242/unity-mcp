"""Execute the scheduled canary's source selection against committed fixture history."""

import json
import os
import re
from pathlib import Path
import subprocess
import sys

import yaml
import pytest

from tests.test_check_source_pin_drift import commit, history, set_pin
from tests.test_update_source_pins import put


ROOT = Path(__file__).resolve().parents[2]


def selection(repo: Path, temporary: Path) -> subprocess.CompletedProcess[str]:
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
    expansion = job["strategy"]["matrix"]["include"]
    rows = json.loads(re.search(r"\|\| '([^']+)'", expansion)[1])
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
def test_pin_only_pr_selects_one_selected_distribution_canary(path):
    from tests.test_ci_trigger_policy import selected

    assert selected("distribution-canary.yml", "pull_request", "beta", path, "synchronize")
    assert selected("distribution-canary.yml", "push", "beta", path)
    assert not selected("distribution-canary.yml", "push", "main", path)
    config = yaml.safe_load((ROOT / ".github/workflows/distribution-canary.yml").read_text())
    matrix = config["jobs"]["canary"]["strategy"]["matrix"]
    assert "github.event_name == 'pull_request'" in matrix["include"]
    rows = json.loads(re.search(r"&& '([^']+)'", matrix["include"])[1])
    assert rows == [{"os": "ubuntu-26.04", "python-version": "3.11"}]
