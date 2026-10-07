"""CI tokens and reusable secrets stay within their documented job boundaries."""

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
UNITY_SECRETS = {"UNITY_LICENSE", "UNITY_EMAIL", "UNITY_PASSWORD", "UNITY_SERIAL"}
PUSH_JOBS = {
    ("beta-release.yml", "update_unity_beta_version"),
    ("release.yml", "bump"),
    ("release.yml", "sync_beta"),
    ("sync-releases.yml", "sync"),
}


def workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_workflow_defaults_are_explicit_and_read_only(path: Path) -> None:
    config = workflow(path.name)
    expected = {} if path.name == "github-repo-stats.yml" else {"contents": "read"}
    assert config["permissions"] == expected


def test_write_permissions_are_limited_to_jobs_that_need_them() -> None:
    expected = {
        ("beta-release.yml", "update_unity_beta_version"): {"contents", "pull-requests"},
        ("beta-release.yml", "publish_pypi_prerelease"): {"id-token"},
        ("release.yml", "bump"): {"contents", "pull-requests"},
        ("release.yml", "sync_beta"): {"contents", "pull-requests"},
        ("release.yml", "sync_release_notes"): {"actions"},
        ("release.yml", "publish_pypi"): {"id-token"},
        ("release.yml", "publish_mcpb"): {"contents"},
        ("sync-releases.yml", "sync"): {"contents", "pull-requests"},
        ("claude-nl-suite.yml", "nl-suite"): {"checks", "id-token"},
        ("docs-deploy.yml", "deploy"): {"pages", "id-token"},
    }
    actual = {}
    for path in WORKFLOWS:
        for name, job in workflow(path.name)["jobs"].items():
            writes = {
                scope for scope, access in job.get("permissions", {}).items() if access == "write"
            }
            if writes:
                actual[path.name, name] = writes
    assert actual == expected


def test_upstream_release_note_writers_remain_disabled_in_forks() -> None:
    for name, job in (("sync-releases.yml", "sync"), ("release.yml", "sync_release_notes")):
        assert workflow(name)["jobs"][job]["if"] == "github.repository == 'CoplayDev/unity-mcp'"


def test_checkout_persists_credentials_only_for_git_push_jobs() -> None:
    persisted = set()
    for path in WORKFLOWS:
        for name, job in workflow(path.name)["jobs"].items():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("actions/checkout@"):
                    identity = path.name, name
                    expected = identity in PUSH_JOBS
                    assert step["with"]["persist-credentials"] is expected, identity
                    if expected:
                        persisted.add(identity)
    assert persisted == PUSH_JOBS


@pytest.mark.parametrize("name", ["unity-tests.yml", "e2e-bridge.yml"])
def test_license_preflight_needs_no_github_token(name: str) -> None:
    job = workflow(name)["jobs"]["license"]
    assert job["permissions"] == {}
    assert all("uses" not in step for step in job["steps"])
    assert "GITHUB_TOKEN" not in str(job)


def test_pages_build_is_read_only_and_only_deploy_can_write() -> None:
    jobs = workflow("docs-deploy.yml")["jobs"]
    assert jobs["build"]["permissions"] == {"contents": "read", "pages": "read"}
    assert jobs["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    setup = next(step for step in jobs["build"]["steps"] if step.get("name") == "Setup Pages")
    assert setup["with"]["enablement"] is False


@pytest.mark.parametrize("name", ["beta-release.yml", "release.yml", "fork-beta-tools.yml"])
def test_reusable_unity_validation_receives_only_license_secrets(name: str) -> None:
    caller = workflow(name)
    job = caller["jobs"]["unity_tests"]
    assert job["secrets"] == {secret: "${{ secrets." + secret + " }}" for secret in UNITY_SECRETS}
    callee = workflow("unity-tests.yml")
    triggers = callee.get("on", callee.get(True))
    declarations = triggers["workflow_call"]["secrets"]
    assert set(declarations) == UNITY_SECRETS
    assert all(declaration["required"] is False for declaration in declarations.values())
    # A reusable workflow cannot grant itself permissions withheld by its caller.
    for callee_job in callee["jobs"].values():
        for scope, access in callee_job.get("permissions", callee["permissions"]).items():
            assert caller["permissions"][scope] == access == "read"
