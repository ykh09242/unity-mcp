"""A retained build transition needs independent, comparable and ordered native trials."""

import json

import pytest
from cli.main import cli
from cli.utils.verification_history import inspect_history
from click.testing import CliRunner
from tests.test_verification_change_evidence import make_capture, with_editor_check


def history(tmp_path, outcomes):
    builds = []
    for i, state in enumerate(outcomes, 1):
        pairs = []
        if state:
            report, native = make_capture(tmp_path, i, state)
            pairs = [{"verification": report.name, "native": native.name}]
        builds.append({"build_id": f"build-{i}", "source_revision": f"{i:064x}", "runs": pairs})
    manifest = tmp_path / "history.json"
    manifest.write_text(json.dumps({"schema": "verification_build_history@1", "builds": builds}))
    return manifest


@pytest.mark.parametrize(
    ("states", "expected", "window"),
    [
        (["passed", "passed", "failed", "failed"], "first_failure", ["build-3"]),
        (["passed", None, "failed"], "candidate_range", ["build-2", "build-3"]),
        (["passed", "failed", "passed"], "unstable", ["build-2"]),
        (["failed", "failed"], "missing_passing_baseline", ["build-1"]),
        (["passed", None], "incomplete", []),
    ],
)
def test_only_complete_monotone_histories_get_a_first_build(tmp_path, states, expected, window):
    data = inspect_history(history(tmp_path, states), "scenario", "doors")
    assert data["state"] == expected
    assert data["candidate_builds"] == window


def test_history_rejects_a_build_mismatch_and_outside_paths(tmp_path):
    path = history(tmp_path, ["passed", "failed"])
    original = json.loads(path.read_text())
    original["builds"][1]["runs"] = original["builds"][0]["runs"]
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="Archive build"):
        inspect_history(path, "scenario", "doors")
    original["builds"][0]["runs"][0]["native"] = "../foreign.json"
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="inside the selected history"):
        inspect_history(path, "scenario", "doors")


def first_failure(path, kind="scenario", name="doors"):
    return CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "--format",
            "json",
            "verification-evidence",
            "first-failure",
            str(path),
            "--kind",
            kind,
            "--id",
            name,
        ],
    )


@pytest.mark.parametrize(
    "mutation,code,state",
    [
        ("none", 1, "first_failure"),
        ("all_pass", 0, "no_observed_failure"),
        ("missing", 2, "incomplete"),
        ("different_timeout", 2, "incomplete"),
        ("malformed", 2, "incomplete"),
    ],
)
def test_first_failure_through_registered_cli(tmp_path, mutation, code, state):
    path = history(tmp_path, ["passed", "passed" if mutation == "all_pass" else "failed"])
    native = tmp_path / "native-2.json"
    if mutation == "missing":
        native.rename(tmp_path / "unavailable.json")
    elif mutation == "different_timeout":
        data = json.loads(native.read_text())
        data["timeout_seconds"] += 1
        native.write_text(json.dumps(data))
    elif mutation == "malformed":
        doc = json.loads((tmp_path / "verification-2.json").read_text())
        doc["data"]["stages"][0]["state"] = {}
        (tmp_path / "verification-2.json").write_text(json.dumps(doc))
    result = first_failure(path)
    assert result.exit_code == code, result.output
    report = json.loads(result.output)
    assert report["state"] == state
    if mutation == "missing":
        assert report["unverified_builds"] == ["build-2"]
    elif mutation == "none":
        assert report["first_observed_failing_build"] == "build-2"
    elif mutation == "malformed":
        assert "error" in report


@pytest.mark.parametrize("mismatch", ["definition", "execution", "none"])
def test_named_profile_history_uses_exact_execution_and_content(tmp_path, mismatch):
    path = history(tmp_path, ["passed", "failed"])
    for index, state in enumerate(["passed", "failed"], 1):
        capture = tmp_path / f"verification-{index}.json", tmp_path / f"native-{index}.json"
        doc = with_editor_check(
            capture,
            "profile",
            state=state,
            digest=("f" if index == 2 and mismatch == "definition" else "b") * 64,
        )
        if index == 2 and mismatch == "execution":
            doc["executed_checks"].pop()
            capture[0].write_text(json.dumps(doc))
    result = first_failure(path, "profile", "broader")
    data = json.loads(result.output)
    assert result.exit_code == (1 if mismatch == "none" else 2)
    assert data["state"] == ("first_failure" if mismatch == "none" else "incomplete")
    assert data["timeline"][0]["trials"][0]["check_job_ids"] == ["profile-run-1"]


@pytest.mark.parametrize("mutation", ["payload", "manifest", "none"])
def test_repeat_build_requires_same_inputs_and_independent_trial(tmp_path, mutation):
    path = history(tmp_path, ["passed", "failed"])
    doc = json.loads(path.read_text())
    report_path = tmp_path / "verification-2.json"
    native_path = tmp_path / "native-2.json"
    report = json.loads(report_path.read_text())["data"]
    native = json.loads(native_path.read_text())
    job = "repeat-job"
    report["run_id"] = "repeat-run"
    report["children"][0]["job_id"] = report["executed_checks"][0]["job_id"] = job
    report["stages"][0]["evidence"]["job_id"] = native["job_id"] = job
    if mutation == "payload":
        report["stages"][0]["evidence"]["payload_hash"] = native["reproduction"]["payload_hash"] = (
            "f" * 64
        )
    if mutation == "manifest":
        report["comparison"]["manifest_sha256"] = "f" * 64
    (tmp_path / "repeat-verification.json").write_text(json.dumps(report))
    (tmp_path / "repeat-native.json").write_text(json.dumps(native))
    doc["builds"][1]["runs"].append(
        {"verification": "repeat-verification.json", "native": "repeat-native.json"}
    )
    path.write_text(json.dumps(doc))
    result = first_failure(path)
    data = json.loads(result.output)
    assert result.exit_code == (1 if mutation == "none" else 2)
    if mutation != "none":
        assert "payload or manifest" in data["error"]
    else:
        assert data["state"] == "first_failure"
