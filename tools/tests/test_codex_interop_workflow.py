"""Execute the actual receiving-commit fence and inspect reusable beta wiring."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "candidate", ["actual", "beta", "a" * 39, "a" * 41, "other-sha", "wrong-checkout"]
)
def test_actual_checkout_fence_rejects_mutable_or_mismatched_refs(tmp_path, candidate):
    # Given: the real workflow shell and an exact repository checkout.
    steps = workflow("codex-interop.yml")["jobs"]["codex_interop"]["steps"]
    fence = next(step for step in steps if step.get("name") == "Verify exact receiving commit")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    expected = (
        actual
        if candidate == "actual"
        else "0" * 40
        if candidate in {"other-sha", "wrong-checkout"}
        else candidate
    )
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("bash unavailable")
    summary = tmp_path / "summary"
    environment = os.environ.copy()
    environment.update(
        EXPECTED_SHA=expected,
        GITHUB_SHA=expected if candidate == "wrong-checkout" else actual,
        GITHUB_STEP_SUMMARY=summary.as_posix(),
    )
    # When: the actual shell receives the candidate ref.
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", fence["run"]],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    # Then: only the immutable exact checkout can proceed to actual execution.
    assert (result.returncode == 0) is (candidate == "actual"), result.stderr
    if candidate == "actual":
        assert actual in summary.read_text(encoding="utf-8")


def test_reusable_actual_client_is_selected_and_required_only_by_full_beta():
    # Given: parsed production workflows, including their real caller inputs.
    config = workflow("codex-interop.yml")
    triggers = config.get("on", config.get(True))
    jobs = workflow("fork-beta-tools.yml")["jobs"]
    # When: the reusable contract and final dependency graph are inspected.
    call = jobs["codex_interop"]
    steps = config["jobs"]["codex_interop"]["steps"]
    execute = next(
        step
        for step in steps
        if step.get("name") == "Exercise actual Rust client against production CLI"
    )
    gate = jobs["validation"]
    # Then: ordinary entry points survive and full beta requires the actual pinned harness.
    assert {"pull_request", "workflow_dispatch", "workflow_call"} <= set(triggers)
    assert triggers["workflow_call"]["inputs"]["ref"]["required"] is True
    assert triggers["workflow_call"]["inputs"]["ref"]["type"] == "string"
    assert call["uses"] == "./.github/workflows/codex-interop.yml"
    assert call["with"] == {"ref": "${{ github.sha }}"}
    assert call["needs"] == ["changes"]
    assert (
        call["if"]
        == "github.repository != 'CoplayDev/unity-mcp' && needs.changes.outputs.profile == 'full'"
    )
    assert "secrets" not in call and not call.get("continue-on-error", False)
    assert steps[0]["with"]["ref"] == "${{ inputs.ref || github.sha }}"
    assert '--expected-sha "$GITHUB_SHA"' in execute["run"]
    assert "tools/check_codex_interop.py" in execute["run"]
    assert "codex_interop" in gate["needs"]
    check = next(step for step in gate["steps"] if step.get("name") == "Require selected checks")
    assert check["env"]["CODEX_RESULT"] == "${{ needs.codex_interop.result }}"
    assert '--codex "$CODEX_RESULT"' in check["run"]
