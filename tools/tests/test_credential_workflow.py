"""Credential assurance selects its inputs and executes both real synthetic runners."""

import fnmatch
from pathlib import Path
import shlex

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/credential-assurance.yml"


@pytest.mark.parametrize("event", ["pull_request", "push"])
def test_credential_changes_select_assurance_without_unrelated_changes(event: str) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    paths = workflow[True][event]["paths"]
    selected = [
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in (ROOT / "MCPForUnity/Editor/Security/SecureKeyStore").glob("*.cs")
    ]
    selected.extend(
        [
            "tools/tests/fixtures/encrypted_key_store/StoreHarness.cs",
            "tools/tests/fixtures/encrypted_key_store/StoreHarness.csproj",
            "tools/tests/fixtures/encrypted_key_store/RunRegression.ps1",
            "tools/tests/fixtures/key_store_process/LifecycleHarness.cs",
            "tools/tests/fixtures/key_store_process/ProcessHarness.cs",
            "tools/tests/fixtures/key_store_process/ProcessHarness.csproj",
            "tools/tests/fixtures/key_store_process/RunRegression.ps1",
            "tools/tests/test_credential_workflow.py",
            ".github/workflows/credential-assurance.yml",
        ]
    )
    for path in selected:
        assert any(fnmatch.fnmatchcase(path, pattern) for pattern in paths), path
    for path in [
        "Server/src/main.py",
        "website/src/index.ts",
        "MCPForUnity/Editor/Tools/ManageScene.cs",
    ]:
        assert not any(fnmatch.fnmatchcase(path, pattern) for pattern in paths), path
    assert "beta" in workflow[True][event]["branches"]


def test_credential_job_runs_both_isolated_runners_on_every_platform() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in workflow[True]
    job = workflow["jobs"]["conformance"]
    assert "if" not in job and not job.get("continue-on-error", False)
    assert job["runs-on"] == "${{ matrix.os }}"
    assert set(job["strategy"]["matrix"]["os"]) == {
        "windows-latest",
        "ubuntu-26.04",
        "macos-26-intel",
    }
    assert job["strategy"]["fail-fast"] is False
    assert job["defaults"]["run"]["shell"] == "pwsh"
    steps = job["steps"]
    assert (
        next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))[
            "with"
        ]["persist-credentials"]
        is False
    )
    assert (
        next(step for step in steps if step.get("uses", "").startswith("actions/setup-dotnet@"))[
            "with"
        ]["dotnet-version"]
        == "11.0.100"
    )
    commands = []
    for step in steps:
        assert "if" not in step and not step.get("continue-on-error", False)
        assert "env" not in step
        if "run" in step:
            command = shlex.split(step["run"])
            assert command[0] == "&" and command[2] == "-OutputRoot"
            assert len(command) == 4
            assert (ROOT / command[1]).is_file()
            commands.append((command[1], command[3]))
    assert commands == [
        (
            "./tools/tests/fixtures/encrypted_key_store/RunRegression.ps1",
            "${{ runner.temp }}/encrypted-key-store",
        ),
        (
            "./tools/tests/fixtures/key_store_process/RunRegression.ps1",
            "${{ runner.temp }}/key-store-process",
        ),
    ]
