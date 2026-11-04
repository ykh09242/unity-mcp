from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ("python-tests.yml", "compile-check.yml", "unity-tests.yml", "unity-player-tests.yml")


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_checkout_and_reusable_call_selects_the_same_candidate(name):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request_target" not in triggers
    for job in workflow["jobs"].values():
        if job.get("uses", "").startswith("./.github/workflows/"):
            assert job["with"]["ref"] == "${{ inputs.ref || github.sha }}"
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["ref"] == "${{ inputs.ref || github.sha }}"
                assert step["with"]["persist-credentials"] is False


@pytest.mark.parametrize("name", WORKFLOWS)
def test_candidate_is_attested_after_each_checkout(name):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    for job in workflow["jobs"].values():
        steps = job.get("steps", [])
        for index, step in enumerate(steps):
            if step.get("uses", "").startswith("actions/checkout@"):
                evidence = next(
                    item
                    for item in steps[index + 1 :]
                    if item.get("name") == "Record checked candidate"
                )
                assert evidence["env"]["CANDIDATE_REF"] == "${{ inputs.ref || github.sha }}"
                assert "git rev-parse HEAD" in evidence["run"]
                assert "GITHUB_STEP_SUMMARY" in evidence["run"]
                assert 'test "$actual" = "$CANDIDATE_REF"' in evidence["run"]


@pytest.mark.parametrize(
    "event,explicit,sha,expected",
    [
        ("pull_request", "", "merge-candidate", "merge-candidate"),
        ("push", "", "push-candidate", "push-candidate"),
        ("workflow_dispatch", "", "dispatch-candidate", "dispatch-candidate"),
        ("workflow_call", "reviewed-ref", "caller-candidate", "reviewed-ref"),
    ],
)
def test_event_candidate_expression_keeps_explicit_ref_and_trigger_sha(
    event, explicit, sha, expected
):
    # Interpret the deliberately small common expression; PR head is never an operand.
    for name in WORKFLOWS:
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("actions/checkout@"):
                    expression = step["with"].get("ref", "${{ github.sha }}")
                    values = {"inputs.ref": explicit, "github.sha": sha}
                    assert (
                        next(
                            values[operand.strip()]
                            for operand in expression[3:-2].split("||")
                            if values[operand.strip()]
                        )
                        == expected
                    )


@pytest.mark.parametrize(
    "requested,success", [("actual", True), ("0" * 40, False), ("reviewed-branch", True)]
)
def test_candidate_attestation_checks_resolved_sha_and_records_actual_commit(
    tmp_path, requested, success
):
    import os
    import shutil
    import subprocess

    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("bash unavailable")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    workflow = yaml.safe_load((ROOT / ".github/workflows/compile-check.yml").read_text())
    step = next(
        item
        for item in workflow["jobs"]["compile"]["steps"]
        if item.get("name") == "Record checked candidate"
    )
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        [bash, "-c", step["run"]],
        cwd=ROOT,
        env={
            **os.environ,
            "CANDIDATE_REF": actual if requested == "actual" else requested,
            "GITHUB_STEP_SUMMARY": summary.as_posix(),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stderr
    if success:
        assert actual in summary.read_text()
    else:
        assert not summary.exists()
