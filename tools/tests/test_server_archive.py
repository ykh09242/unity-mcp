"""Archive assurance runs public behavior with installed imports and no Editor."""

import os
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "tools/check_server_archive.py"


def test_archive_probe_refuses_editable_worktree_imports(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(PROBE),
            "--forbid-source",
            str(ROOT),
            "--candidate-sha",
            "a" * 40,
        ],
        cwd=tmp_path,
        env={**os.environ, "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "Installed archive required" in result.stderr


def test_archive_workflow_installs_the_checked_candidate_without_lock_or_source_path():
    workflow = yaml.safe_load((ROOT / ".github/workflows/python-tests.yml").read_text())
    job = workflow["jobs"]["installed_archive"]
    assert job.get("continue-on-error", False) is False
    assert job["if"] == "inputs.validation_profile != 'tools-only'"
    steps = job["steps"]
    install = next(
        step
        for step in steps
        if step.get("name") == "Install candidate archive without runtime lock"
    )
    assert "git archive" in install["run"]
    assert '"${revision}:Server"' in install["run"]
    assert "$revision = git rev-parse HEAD" in install["run"]
    assert "ARCHIVE_CANDIDATE_SHA=$revision" in install["run"]
    assert "uv pip install" in install["run"]
    assert "ykh09242-unity-mcp-server @ " in install["run"]
    assert "uv sync" not in install["run"]
    behavior = next(
        step for step in steps if step.get("name") == "Exercise installed HTTP and CLI behavior"
    )
    assert " -I " in behavior["run"]
    assert "--forbid-source" in behavior["run"]
    assert "--candidate-sha $env:ARCHIVE_CANDIDATE_SHA" in behavior["run"]
    assert "check_server_startup.py" in behavior["run"]
    assert "PYTHONPATH" not in job.get("env", {})
    assert "PYTHONPATH" not in behavior.get("env", {})
