"""Linux validation must use the reviewed Ubuntu release consistently."""

from pathlib import Path
import json
import re

import pytest
import yaml
import bridge_pair_evidence


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_workflows_use_the_reviewed_ubuntu_image(path, tmp_path, monkeypatch):
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job in config["jobs"].values():
        runner = job.get("runs-on", "")
        if isinstance(runner, str) and runner.startswith("ubuntu-"):
            assert runner == "ubuntu-26.04"
        matrix = job.get("strategy", {}).get("matrix", {})
        if isinstance(matrix, str):
            assert path.name == "e2e-bridge.yml"
            assert matrix == "${{ fromJSON(needs.pairs.outputs.matrix) }}"
            producer = config["jobs"]["pairs"]
            assert producer["outputs"]["matrix"] == "${{ steps.pairs.outputs.matrix }}"
            step = next(item for item in producer["steps"] if item.get("id") == "pairs")
            assert "python3 tools/bridge_pair_evidence.py plan" in step["run"]
            output = tmp_path / "matrix-output"
            monkeypatch.setenv("GITHUB_OUTPUT", str(output))
            assert (
                bridge_pair_evidence.main(
                    [
                        "plan",
                        "--repo",
                        str(ROOT),
                        "--selection",
                        "both",
                        "--report",
                        str(tmp_path / "pairs.json"),
                    ]
                )
                == 0
            )
            key, _, value = output.read_text(encoding="utf-8").strip().partition("=")
            assert key == "matrix"
            matrix = json.loads(value)
            assert {row["mode"] for row in matrix["include"]} == {"candidate", "selected"}
            # Every emitted row inherits this exact runner; no unexamined OS expression.
            assert job["runs-on"] == "ubuntu-26.04"
        included = matrix.get("include", [])
        if isinstance(included, str):
            assert path.name == "distribution-canary.yml"
            branches = re.findall(r"(?:&&|\|\|) '([^']+)'", included)
            assert len(branches) == 2
            included = [row for branch in branches for row in json.loads(branch)]
            assert included and all("os" in row for row in included)
        runners = [*matrix.get("os", []), *(row.get("os", "") for row in included)]
        for runner in runners:
            if runner.startswith("ubuntu-"):
                assert runner == "ubuntu-26.04"


def test_bootstrap_keeps_all_three_operating_systems():
    config = yaml.safe_load((WORKFLOWS / "python-tests.yml").read_text(encoding="utf-8"))
    job = config["jobs"]["server_bootstrap"]
    assert job["strategy"]["matrix"]["os"] == ["windows-latest", "ubuntu-26.04", "macos-26-intel"]
    assert job["runs-on"] == "${{ matrix.os }}"
