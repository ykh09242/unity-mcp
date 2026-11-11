"""Exercise the Windows-only VFX selection and NUnit gate without a licensed Editor."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_windows_vfx_tests as vfx


def inventory():
    suites = json.loads((ROOT / "tools/unity-optional-tests.json").read_text())["suites"]
    return next(suite for suite in suites if suite["id"] == "vfx-graph")


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/unity-windows-vfx.yml").read_text())


def test_inventory_covers_each_windows_file_lock_method():
    suite = inventory()
    fixture = (
        ROOT / "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/VfxGraphIntegrityTests.cs"
    )
    methods = re.findall(
        r'\[Platform\("Win"\)\]\s+public void (\w+)\(', fixture.read_text(encoding="utf-8")
    )
    assert set(vfx.windows_methods({"schemaVersion": 1, "suites": [suite]})) == {
        suite["filter"] + "." + method for method in methods
    }


@pytest.mark.parametrize(
    "case,exit_code,forced,expected",
    [
        ("Passed", 0, False, 0),
        ("Missing", 0, False, 1),
        ("Skipped", 0, False, 1),
        ("Ignored", 0, False, 1),
        ("Failed", 0, False, 1),
        ("Passed", 2, False, 1),
        ("Passed", 0, True, 1),
        ("NoXml", 0, False, 1),
    ],
)
def test_run_requires_each_selected_windows_case_and_successful_editor(
    tmp_path, monkeypatch, case, exit_code, forced, expected
):
    suite = inventory()
    methods = suite["requiredTestsWindows"]
    evidence = tmp_path / "fresh"

    def fake_editor(invocation):
        arguments = invocation.command
        assert arguments[arguments.index("-testPlatform") + 1] == "EditMode"
        expression = arguments[arguments.index("-testFilter") + 1]
        # .NET's strict end-of-input anchor is spelled \Z on Python 3.11.
        patterns = [re.compile(part.replace(r"\z", r"\Z")) for part in expression.split(";")]

        def selected(name):
            return any(pattern.search(name) for pattern in patterns)

        assert all(selected(method) for method in methods)
        assert not any(selected(method) for method in suite["requiredTests"])
        assert not selected(methods[0] + "Other")
        results = Path(arguments[arguments.index("-testResults") + 1])
        assert results.parent == evidence
        if case != "NoXml":
            # Include unrelated passing cases in every result, including missing evidence.
            cases = [(suite["requiredTests"][0], "Passed")]
            cases.extend((method, "Passed") for method in methods[1:])
            if case != "Missing":
                cases.append((methods[0], "Skipped" if case == "Ignored" else case))
            counts = {
                state: sum(result == state for _, result in cases)
                for state in ("Passed", "Skipped", "Failed")
            }
            report = ET.Element(
                "test-run",
                total=str(len(cases)),
                passed=str(counts["Passed"]),
                failed=str(counts["Failed"]),
                skipped=str(counts["Skipped"]),
                inconclusive="0",
                result="Failed" if counts["Failed"] else "Passed",
            )
            for method, result in cases:
                row = ET.SubElement(report, "test-case", fullname=method, result=result)
                if case == "Ignored" and method == methods[0]:
                    row.set("label", "Ignored")
            ET.ElementTree(report).write(results)
        return {"actual_exit_code": exit_code, "forced_termination": forced}

    monkeypatch.setattr(vfx, "execute", fake_editor)
    assert vfx.run(Path("Unity.exe"), tmp_path, evidence) == expected


def test_stale_evidence_rejected_before_editor_launch(tmp_path, monkeypatch):
    (tmp_path / "results.xml").write_text("<test-run/>")
    monkeypatch.setattr(vfx, "execute", lambda _: pytest.fail("launched with stale evidence"))
    with pytest.raises(FileExistsError):
        vfx.run(Path("Unity.exe"), tmp_path, tmp_path)


@pytest.mark.parametrize("replacement", [None, [], ["OtherFixture.WrongMethod"]])
def test_empty_or_wrong_windows_inventory_cannot_fall_back_to_portable_tests(replacement):
    suite = inventory()
    if replacement is None:
        del suite["requiredTestsWindows"]
    else:
        suite["requiredTestsWindows"] = replacement
    with pytest.raises(ValueError):
        vfx.windows_methods({"schemaVersion": 1, "suites": [suite]})


def test_only_manual_or_reusable_beta_receiving_sha_can_enter_licensed_job():
    config = workflow()
    trigger = config.get("on", config.get(True))
    assert set(trigger) == {"workflow_dispatch", "workflow_call"}
    assert trigger["workflow_call"]["inputs"]["ref"]["default"] == ""
    assert trigger["workflow_call"]["secrets"] == {
        "WINDOWS_VFX_UNITY_" + key: {"required": True} for key in ("EMAIL", "PASSWORD", "SERIAL")
    }
    jobs = config["jobs"]
    assert jobs["rejectUntrustedRef"]["if"] == "github.ref != 'refs/heads/beta'"
    licensed = jobs["windowsVfx"]
    assert licensed["if"] == "github.ref == 'refs/heads/beta'"
    assert licensed["environment"] == "windows-vfx"
    assert licensed["runs-on"] == "windows-2022"
    steps = licensed["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {
        "ref": "${{ inputs.ref || github.sha }}",
        "persist-credentials": False,
    }
    secret_steps = [step for step in steps if "secrets." in str(step)]
    assert [step.get("id") for step in secret_steps] == ["activate"]
    assert secret_steps[0]["env"] == {
        "UNITY_" + key: "${{ secrets.WINDOWS_VFX_UNITY_" + key + " }}"
        for key in ("EMAIL", "PASSWORD", "SERIAL")
    }
    release = next(step for step in steps if step.get("id") == "returnLicense")
    assert release["if"] == "always() && steps.activate.outcome != 'skipped'"
    run = next(
        step
        for step in steps
        if step.get("run", "").startswith("python tools/run_windows_vfx_tests.py ")
    )
    assert steps.index(secret_steps[0]) < steps.index(run) < steps.index(release)
    assert not any(run.get(key) for key in ("if", "continue-on-error"))


@pytest.mark.parametrize(
    "ref,event_sha,expected_sha,actual,success",
    [
        ("refs/heads/beta", "a" * 40, "a" * 40, "a" * 40, True),
        ("refs/heads/BETA", "a" * 40, "a" * 40, "a" * 40, False),
        ("refs/tags/beta", "a" * 40, "a" * 40, "a" * 40, False),
        ("refs/heads/beta", "a" * 40, "a" * 40, "b" * 40, False),
        ("refs/heads/beta", "a" * 40, "b" * 40, "b" * 40, False),
        ("refs/heads/beta", "main", "main", "main", False),
    ],
)
def test_checkout_guard_executes_against_exact_event_ref_and_commit(
    tmp_path, ref, event_sha, expected_sha, actual, success
):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is required to exercise the hosted Windows shell")
    steps = workflow()["jobs"]["windowsVfx"]["steps"]
    body = next(step["run"] for step in steps if step.get("id") == "verify")
    script = "function git { $global:LASTEXITCODE = 0; $env:TEST_HEAD }\n" + body
    summary = tmp_path / "summary"
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env={
            **os.environ,
            "TEST_HEAD": actual,
            "GITHUB_REF": ref,
            "GITHUB_SHA": event_sha,
            "EXPECTED_SHA": expected_sha,
            "GITHUB_STEP_SUMMARY": str(summary),
        },
    )
    assert (result.returncode == 0) == success
    assert summary.exists() == success
    if success:
        assert actual in summary.read_text()
