"""Public verification evidence cannot borrow results from another build or execution."""

import json

from click.testing import CliRunner
import pytest

from cli.main import cli
from cli.utils.verification_change import compare_runs, read_run


def make_capture(
    directory,
    index,
    state="passed",
    *,
    platform="WindowsPlayer",
    manifest=None,
    base_manifest=None,
    base_source=None,
):
    directory.mkdir(parents=True, exist_ok=True)
    source, job, build = f"{index:064x}", f"job-{index}", f"build-{index}"
    payload = f"{index + 200:064x}"
    status = "succeeded" if state == "passed" else "failed"
    stage = {
        "name": "standalone_player",
        "state": state,
        "evidence": {
            "job_id": job,
            "build_id": build,
            "payload_hash": payload,
            "build_source_revision": source,
            "status": status,
            "scenario": "doors",
            "exit_code": 0 if state == "passed" else 1,
            "finalization_state": "completed",
        },
    }
    report = {
        "schema": "verification_report@1",
        "state": state,
        "native_settled": True,
        "run_id": f"run-{index}",
        "project_scope": "game",
        "installation": {"unity_version": "6000.0.69f1"},
        "source_token": source,
        "comparison": {
            "source_revision": source,
            "manifest_sha256": manifest or f"{index + 400:064x}",
            "baseline_manifest_sha256": base_manifest,
            "baseline_source_revision": base_source,
        },
        "children": [{"kind": "standalone_player", "job_id": job, "settled": True}],
        "executed_checks": [{"kind": "scenario", "id": "doors", "job_id": job, "status": status}],
        "stages": [stage, {"name": "final_source", "state": "passed", "evidence": {}}],
    }
    native = {
        "job_id": job,
        "status": status,
        "exit_code": 0 if state == "passed" else 1,
        "execution_environment": "player",
        "phase": "finished",
        "repeat_count": 1,
        "runner_resources_released": True,
        "finalization_state": "completed",
        "scenario": {"name": "doors"},
        "reproduction": {
            "build_id": build,
            "build_source_revision": source,
            "payload_hash": payload,
            "definition_hash": "a" * 64,
            "payload_verification": "launcher_admission",
            "unity_version": "6000.0.69f1",
            "package_version": "2.5.0",
            "environment": {
                "operating_system": "one",
                "platform": platform,
                "architecture": "x64",
                "scripting_backend": "mono",
                "build_mode": "release",
                "runtime_version": "1",
            },
        },
    }
    if state == "failed":
        native["failure"] = {
            "code": "condition_unmet",
            "target": "fixture.ready",
            "expected": "true",
            "actual": "false",
        }
    a, b = directory / f"verification-{index}.json", directory / f"native-{index}.json"
    a.write_text(json.dumps({"success": True, "data": report}), encoding="utf-8")
    b.write_text(json.dumps(native), encoding="utf-8")
    return a, b


@pytest.mark.parametrize(
    ("old", "new", "label", "code"),
    [
        ("passed", "failed", "new_failure", 1),
        ("failed", "failed", "existing_failure", 0),
        ("failed", "passed", "repaired", 0),
        ("passed", "passed", "unchanged_pass", 0),
    ],
)
def test_pr_classifies_bound_native_results_through_cli(tmp_path, old, new, label, code):
    a = make_capture(tmp_path, 1, old)
    left = read_run(*a)
    b = make_capture(tmp_path, 2, new, base_manifest=left.manifest, base_source=left.source)
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "verification-evidence",
            "compare",
            "--baseline",
            *(str(p) for p in a),
            "--candidate",
            *(str(p) for p in b),
        ],
    )
    assert result.exit_code == code, result.output
    data = json.loads(result.output)
    assert next(r for r in data["checks"] if r["kind"] == "scenario")["classification"] == label


def test_pr_incomplete_for_unexecuted_check_and_wrong_target(tmp_path):
    a = read_run(*make_capture(tmp_path, 1))
    b = make_capture(tmp_path, 2, base_manifest=a.manifest, base_source=a.source)
    raw = json.loads(b[0].read_text())
    raw["data"]["additional_checks_outstanding"] = [{"kind": "test", "id": "navigation"}]
    b[0].write_text(json.dumps(raw))
    compared = compare_runs(a, read_run(*b))
    assert compared["state"] == "incomplete"
    assert compared["checks"][-1]["classification"] == "not_run"
    wrong = read_run(
        *make_capture(
            tmp_path, 3, platform="Android", base_manifest=a.manifest, base_source=a.source
        )
    )
    assert compare_runs(a, wrong)["different_conditions"] == ["platform"]


def test_pr_rejects_substituted_native_artifact_or_wrong_baseline(tmp_path):
    first = make_capture(tmp_path, 1)
    second = make_capture(tmp_path, 2)
    with pytest.raises(ValueError, match="identities"):
        read_run(first[0], second[1])
    with pytest.raises(ValueError, match="capture this baseline"):
        compare_runs(read_run(*first), read_run(*second))
    with pytest.raises(ValueError, match="Reused"):
        compare_runs(read_run(*first), read_run(*first))
