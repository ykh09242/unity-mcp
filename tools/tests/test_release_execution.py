"""Originating execution records and action observations use only owned fixture outputs."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_check_release_evidence import (
    add_native,
    bundle as evidence_bundle,
    git,
    native_log,
    native_xml,
)


ROOT = Path(__file__).resolve().parents[2]
PRODUCER = ROOT / "tools/release_execution.py"
PREFIX = "UNITY_MCP_EXECUTION "


@pytest.fixture
def execution(tmp_path, monkeypatch):
    git(tmp_path, "init")
    git(tmp_path, "commit", "--allow-empty", "-m", "owned checkout")
    for key, value in {
        "GITHUB_REPOSITORY": "ykh09242/unity-mcp",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_JOB": "fixture",
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "step-output"),
    }.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def produce(root, *args):
    return subprocess.run(
        [sys.executable, str(PRODUCER), *args],
        cwd=root,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def records(result):
    return [
        json.loads(line[len(PREFIX) :])
        for line in result.stdout.splitlines()
        if line.startswith(PREFIX)
    ]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_wrapped_command_origins_digests_and_preserves_exit(execution, exit_code):
    result = produce(
        execution,
        "run",
        "--kind",
        "python",
        "--step-name",
        "Tests [fixture:one]",
        "--output",
        "results=results.xml",
        "--",
        sys.executable,
        "-c",
        "from pathlib import Path; print('Python 3.14 pytest'); Path('results.xml').write_text('<fixture/>'); "
        f"raise SystemExit({exit_code})",
    )
    assert result.returncode == exit_code, result.stderr
    start, complete = records(result)
    assert start["event"] == "started"
    assert complete["event"] == "completed"
    assert complete["record_type"] == "subprocess-completion"
    assert complete["exit_code"] == exit_code
    assert complete["run_id"] == 123 and complete["run_attempt"] == 2
    assert complete["job_key"] == "fixture"
    assert complete["checkout_sha"] == git(execution, "rev-parse", "HEAD")
    import hashlib

    for pointer in complete["outputs"].values():
        assert (
            hashlib.sha256((execution / pointer["path"]).read_bytes()).hexdigest()
            == pointer["sha256"]
        )


def test_preexisting_result_is_not_relabelled_as_fresh(execution):
    (execution / "results.xml").write_text("old result", encoding="utf-8")
    result = produce(
        execution,
        "run",
        "--kind",
        "python",
        "--step-name",
        "Tests [fixture:one]",
        "--output",
        "results=results.xml",
        "--",
        sys.executable,
        "-c",
        "raise AssertionError('must not run')",
    )
    assert result.returncode != 0
    assert not records(result)
    assert (execution / "results.xml").read_text() == "old result"


def test_native_action_observation_has_distinct_step_and_fresh_exact_outputs(execution):
    reserved = produce(
        execution,
        "reserve",
        "--kind",
        "native-editmode",
        "--step-name",
        "Run native [fixture:main]",
        "--emitter-step-name",
        "Reserve native [fixture:main]",
        "--observer-step-name",
        "Observe native [fixture:main]",
    )
    assert reserved.returncode == 0, reserved.stderr
    (start,) = records(reserved)
    root = execution / start["output_root"]
    assert list(root.iterdir()) == [root / "reservation.json"]
    (root / "editmode-results.xml").write_text("<test-run/>", encoding="utf-8")
    (root / "editmode.log").write_text("actual editor log", encoding="utf-8")
    observed = produce(
        execution,
        "observe",
        "--reservation",
        str(root / "reservation.json"),
        "--outcome",
        "success",
    )
    assert observed.returncode == 0, observed.stderr
    (complete,) = records(observed)
    assert complete["record_type"] == "action-observation"
    assert complete["execution_id"] == start["execution_id"]
    assert complete["step_name"] == "Run native [fixture:main]"
    assert complete["emitter_step_name"] == "Observe native [fixture:main]"
    assert "exit_code" not in complete
    # A skipped runner cannot be transformed into an observed successful execution.
    skipped = produce(
        execution,
        "observe",
        "--reservation",
        str(root / "reservation.json"),
        "--outcome",
        "skipped",
    )
    assert skipped.returncode != 0
    assert not records(skipped)


def test_native_missing_outputs_cannot_emit_completion(execution):
    reserved = produce(
        execution,
        "reserve",
        "--kind",
        "native-playmode",
        "--step-name",
        "Run native [fixture:main]",
        "--emitter-step-name",
        "Reserve native [fixture:main]",
        "--observer-step-name",
        "Observe native [fixture:main]",
    )
    (start,) = records(reserved)
    observed = produce(
        execution,
        "observe",
        "--reservation",
        str(execution / start["output_root"] / "reservation.json"),
        "--outcome",
        "success",
    )
    assert observed.returncode != 0
    assert not records(observed)


def test_wrapped_workflow_records_are_emitted_by_the_named_execution_step():
    import yaml

    count = 0
    for filename in ("python-tests.yml", "compile-check.yml", "unity-tests.yml"):
        config = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))
        for job_key, job in config["jobs"].items():
            for step in job.get("steps", []):
                if "release_execution.py run" not in step.get("run", ""):
                    continue
                count += 1
                assert step["env"]["EVIDENCE_STEP_NAME"] == step["name"]
                assert f"[{job_key}:" in step["name"]
                assert '--step-name "$EVIDENCE_STEP_NAME"' in step["run"]
                assert not step.get("continue-on-error", False)
                if "--kind python" in step["run"]:
                    assert "--output results=test-results.xml" in step["run"]
                elif "--kind package-preparation" in step["run"]:
                    assert "resolved-packages.json" in step["run"]
    assert count == 8


@pytest.mark.parametrize("kind", ["python", "native-editmode", "native-playmode"])
def test_real_producer_records_are_accepted_by_offline_consumer(
    execution, evidence_bundle, monkeypatch, kind
):
    bundle = evidence_bundle
    monkeypatch.setenv("GITHUB_WORKSPACE", str(bundle.repo))
    entry = bundle.ci()
    if kind.startswith("native-"):
        mode = kind.removeprefix("native-")
        platform = "EditMode" if mode == "editmode" else "PlayMode"
        add_native(bundle, entry, platform=platform)
        reserved = produce(
            execution,
            "reserve",
            "--kind",
            kind,
            "--step-name",
            "Execute fixture [fixture:default]",
            "--emitter-step-name",
            "Reserve fixture [fixture:default]",
            "--observer-step-name",
            "Observe fixture [fixture:default]",
        )
        assert reserved.returncode == 0, reserved.stderr
        (start,) = records(reserved)
        directory = execution / start["output_root"]
        (directory / f"{mode}-results.xml").write_text(native_xml(), encoding="utf-8")
        (directory / f"{mode}.log").write_text(native_log(platform), encoding="utf-8")
        observed = produce(
            execution,
            "observe",
            "--reservation",
            str(directory / "reservation.json"),
            "--outcome",
            "success",
        )
        assert observed.returncode == 0, observed.stderr
        (end,) = records(observed)
        body = f"2026-10-16T12:00:00.5Z {PREFIX}{json.dumps(start)}\n"
        body += "2026-10-16T12:00:01Z ##[group]Run game-ci/unity-test-runner@" + "a" * 40 + "\n"
        body += "2026-10-16T12:00:01Z   artifactsPath: " + start["output_root"] + "\n"
        body += f"2026-10-16T12:00:03Z {PREFIX}{json.dumps(end)}\n"
    else:
        produced = produce(
            execution,
            "run",
            "--kind",
            "python",
            "--step-name",
            "Execute fixture [fixture:default]",
            "--output",
            "results=results.xml",
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; print('Python pytest'); "
            'Path(\'results.xml\').write_text(\'<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite>\')',
        )
        assert produced.returncode == 0, produced.stderr
        _, end = records(produced)
        body = "\n".join("2026-10-16T12:00:01Z " + line for line in produced.stdout.splitlines())
    original = (bundle.evidence.parent / entry["files"]["log"]["path"]).read_text()
    checkout = "\n".join(line for line in original.splitlines() if "12:00:00Z " in line)
    entry["kind"] = kind
    entry["execution_id"] = end["execution_id"]
    entry["files"]["log"] = bundle.file("log", checkout + "\n" + body)
    entry["files"]["checkout"] = dict(entry["files"]["log"])
    for role, pointer in end["outputs"].items():
        entry["files"][role] = dict(pointer)
    bundle.index(entry)
    assert bundle.check(bundle.notes(kind)) == []
