"""Fork syncs run checks without invoking upstream publishing identities."""

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_ONLY = "github.repository == 'CoplayDev/unity-mcp'"


def workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("job", ["update_unity_beta_version", "publish_pypi_prerelease"])
def test_beta_release_side_effects_are_upstream_only(job: str) -> None:
    condition = workflow("beta-release.yml")["jobs"][job]["if"]
    assert condition == f"{UPSTREAM_ONLY} && github.actor != 'github-actions[bot]'"


def test_forks_keep_python_and_unity_validation() -> None:
    jobs = workflow("beta-release.yml")["jobs"]
    for name in ("python_tests", "unity_tests"):
        assert jobs[name]["if"] == "github.actor != 'github-actions[bot]'"
        assert jobs[name]["uses"].startswith("./.github/workflows/")


def test_pages_setup_upload_and_deployment_are_upstream_only() -> None:
    jobs = workflow("docs-deploy.yml")["jobs"]
    condition = f"{UPSTREAM_ONLY} && github.event_name == 'push' && github.ref == 'refs/heads/beta'"
    assert jobs["deploy"]["if"] == condition
    steps = {step["name"]: step for step in jobs["build"]["steps"]}
    for name in ("Setup Pages", "Upload Pages artifact"):
        assert steps[name]["if"] == condition
    assert "if" not in steps["Build"]
