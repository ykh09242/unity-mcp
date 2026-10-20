"""Linux validation must use the reviewed Ubuntu release consistently."""

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_workflows_use_the_reviewed_ubuntu_image(path):
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job in config["jobs"].values():
        runner = job.get("runs-on", "")
        if isinstance(runner, str) and runner.startswith("ubuntu-"):
            assert runner == "ubuntu-26.04"
        # The only OS matrix is Python's cold/offline bootstrap coverage.
        for runner in job.get("strategy", {}).get("matrix", {}).get("os", []):
            if runner.startswith("ubuntu-"):
                assert runner == "ubuntu-26.04"


def test_bootstrap_keeps_all_three_operating_systems():
    config = yaml.safe_load((WORKFLOWS / "python-tests.yml").read_text(encoding="utf-8"))
    job = config["jobs"]["server_bootstrap"]
    assert job["strategy"]["matrix"]["os"] == ["windows-latest", "ubuntu-26.04", "macos-26-intel"]
    assert job["runs-on"] == "${{ matrix.os }}"
