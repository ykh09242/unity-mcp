"""Execute the scheduled canary's source selection against committed fixture history."""

import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

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
