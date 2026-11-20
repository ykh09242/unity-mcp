"""Public verification evidence cannot borrow results from another build or execution."""

import json

import pytest
from cli.main import cli
from cli.utils.verification_change import compare_runs, read_run
from click.testing import CliRunner
from tests.verification_artifact_fixtures import make_capture


def with_editor_check(capture, kind="test", *, state="passed", digest="b" * 64):
    path, _ = capture
    report = json.loads(path.read_text())["data"]
    name = {"test": "Doors.Opens", "profile": "broader", "scenario": "fallback"}[kind]
    job = f"{kind}-{report['run_id']}"
    status = "succeeded" if state == "passed" else "failed"
    identity = {"kind": kind, "id": name}
    report["comparison"]["selected_checks"]["checks"].append(identity)
    child = {
        "check_kind": kind,
        "check_id": name,
        "kind": "editor_scenario" if kind == "scenario" else "tests",
        "definition_hash": digest,
        "job_id": job,
        "settled": True,
        "dispatch": "acknowledged",
    }
    data = {
        "target": "editor",
        "definition_hash": digest,
        "candidate_source_revision": report["source_token"],
        "source_token": f"editor-{report['run_id']}",
        "status": status,
    }
    witness = {**identity, "definition_hash": digest, "job_id": job, "status": status}
    if kind == "scenario":
        child.update(name=name, status=status)
        data["runner_resources_released"] = True
    else:
        data.update(mode="EditMode", child_job_ids=[job], summary={"total": 1, state: 1})
        if kind == "profile":
            witness["child_job_ids"] = [job]
        else:
            data["test_result"] = {"job_id": job, "full_name": name, "state": state.title()}
    row = {**identity, "state": state, "definition_hash": digest, "job_id": job, "evidence": data}
    report["children"].append(child)
    report["executed_checks"].append(witness)
    report["selected_check_results"] = [row]
    if state == "failed":
        report["state"] = state
    path.write_text(json.dumps(report))
    return report


@pytest.mark.parametrize("kind", ["test", "profile", "scenario"])
def test_editor_check_requires_its_own_witness(tmp_path, kind):
    capture = make_capture(tmp_path, 1)
    report = with_editor_check(capture, kind)
    row = report["selected_check_results"][0]
    key = (kind, row["id"])
    assert read_run(*capture).checks[key]["state"] == "passed"
    report["executed_checks"].pop()
    capture[0].write_text(json.dumps(report))
    assert read_run(*capture).checks[key]["state"] == "incomplete"


@pytest.mark.parametrize(
    "mutation",
    [
        "player_job",
        "wrong_owner",
        "wrong_kind",
        "refused",
        "unsettled",
        "wrong_source",
        "wrong_definition",
        "other_leaf",
        "missing_leaf",
        "console_failure",
    ],
)
def test_unwitnessed_named_test_cannot_claim_the_native_result(tmp_path, mutation):
    capture = make_capture(tmp_path, 1)
    report = with_editor_check(capture)
    row = report["selected_check_results"][0]
    child = report["children"][-1]
    evidence = row["evidence"]
    if mutation == "player_job":
        report["children"].pop()
        job = report["children"][0]["job_id"]
        row["job_id"] = report["executed_checks"][-1]["job_id"] = job
        evidence["child_job_ids"] = [job]
        evidence["test_result"]["job_id"] = job
    elif mutation == "wrong_owner":
        child["check_id"] = "Other.Test"
    elif mutation == "wrong_kind":
        child["kind"] = "analysis"
    elif mutation == "refused":
        child["dispatch"] = "refused"
    elif mutation == "unsettled":
        child["settled"] = False
    elif mutation == "wrong_source":
        evidence["candidate_source_revision"] = "0" * 64
    elif mutation == "wrong_definition":
        report["executed_checks"][-1]["definition_hash"] = "f" * 64
    elif mutation == "other_leaf":
        evidence["test_result"]["full_name"] = "Doors.Closes"
    elif mutation == "missing_leaf":
        del evidence["test_result"]
    else:
        row["state"] = "failed"
    capture[0].write_text(json.dumps(report))
    assert read_run(*capture).checks[("test", "Doors.Opens")]["state"] == "incomplete"


def test_unmapped_change_is_visible_even_without_selected_execution(tmp_path):
    before = read_run(*make_capture(tmp_path, 1))
    capture = make_capture(tmp_path, 2, base_manifest=before.manifest, base_source=before.source)
    report = json.loads(capture[0].read_text())["data"]
    report["comparison"]["selected_checks"]["outstanding"] = ["unmapped_affected_paths"]
    capture[0].write_text(json.dumps(report))
    result = compare_runs(before, read_run(*capture))
    assert result["coverage_complete"] is False
    assert result["outstanding_selection_reasons"] == ["unmapped_affected_paths"]
    assert result["state"] == "incomplete"


def test_incomplete_editor_coverage_preserves_independent_player_regression(tmp_path):
    a = make_capture(tmp_path, 1)
    report = with_editor_check(a)
    report["state"] = "incomplete"
    report["selected_check_results"][0]["state"] = "incomplete"
    a[0].write_text(json.dumps(report))
    before = read_run(*a)
    b = make_capture(
        tmp_path, 2, "failed", base_manifest=before.manifest, base_source=before.source
    )
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
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["state"] == "regressed"
    assert data["coverage_complete"] is False
    assert next(r for r in data["checks"] if r["id"] == "doors")["classification"] == "new_failure"


def test_distinct_player_failure_components_are_classified_as_changed(tmp_path):
    a = make_capture(tmp_path, 1, "failed")
    before = read_run(*a)
    b = make_capture(
        tmp_path, 2, "failed", base_manifest=before.manifest, base_source=before.source
    )
    for capture, component in zip((a, b), ("Gate", "Latch"), strict=True):
        native = json.loads(capture[1].read_text())
        native["failure"]["component"] = component
        capture[1].write_text(json.dumps(native))
    result = compare_runs(read_run(*a), read_run(*b))
    assert (
        next(r for r in result["checks"] if r["kind"] == "scenario")["classification"]
        == "changed_failure"
    )


@pytest.mark.parametrize(
    "blocked", ["stale", "missing_final", "current_mismatch", "partial_chunk", "progress_error"]
)
def test_stale_or_impaired_run_cannot_establish_change(tmp_path, blocked):
    before = read_run(*make_capture(tmp_path, 1))
    capture = make_capture(
        tmp_path, 2, "failed", base_manifest=before.manifest, base_source=before.source
    )
    report = json.loads(capture[0].read_text())["data"]
    if blocked == "stale":
        report["state"] = "stale"
    elif blocked == "missing_final":
        report["stages"].pop()
    elif blocked == "current_mismatch":
        report["comparison"]["live_matches_candidate"] = False
    elif blocked == "partial_chunk":
        report["latest_input_inventory"] = {"candidate": {"complete": False}}
    else:
        native = json.loads(capture[1].read_text())
        native["progress_error"] = "artifact_write_failed"
        capture[1].write_text(json.dumps(native))
    capture[0].write_text(json.dumps(report))
    assert compare_runs(before, read_run(*capture))["state"] == "incomplete"


def test_mismatched_observation_types_do_not_compare_equal(tmp_path):
    captures = [make_capture(tmp_path, 1)]
    before = read_run(*captures[0])
    captures.append(
        make_capture(tmp_path, 2, base_manifest=before.manifest, base_source=before.source)
    )
    for capture, value in zip(captures, [True, 1], strict=True):
        report = json.loads(capture[0].read_text())["data"]
        report["stages"].append(
            {
                "name": "observed_state",
                "state": "passed",
                "evidence": {"expected": {"open": value}, "observed": {"open": value}},
            }
        )
        capture[0].write_text(json.dumps(report))
    assert compare_runs(*(read_run(*pair) for pair in captures))["different_conditions"] == [
        "expected_state"
    ]
    report["stages"][-1]["evidence"]["observed"] = {"open": True}
    captures[-1][0].write_text(json.dumps(report))
    with pytest.raises(ValueError, match="contradicts"):
        read_run(*captures[-1])


@pytest.mark.parametrize("legacy", [False, True])
def test_missing_marker_preserves_independent_native_regression(tmp_path, legacy):
    from cli.utils.verification_history import inspect_history
    from services.verification.player_candidate import StateCheck
    from services.verification.player_runner import _state_result

    baseline = make_capture(tmp_path, 1)
    before = read_run(*baseline)
    candidate = make_capture(
        tmp_path, 2, "failed", base_manifest=before.manifest, base_source=before.source
    )
    check = StateCheck(log_marker="STATE ", expected={"open": True})
    builds = []
    for pair, log in ((baseline, 'STATE {"open":true}'), (candidate, "no marker")):
        (tmp_path / "player.log").write_text(log, encoding="utf-8")
        state, evidence = _state_result(tmp_path, check)
        assert evidence["expected"] == check.expected
        if legacy and state == "incomplete":
            del evidence["expected"]
        report = json.loads(pair[0].read_text())["data"]
        report["stages"].append({"name": "observed_state", "state": state, "evidence": evidence})
        pair[0].write_text(json.dumps(report))
        builds.append(
            {
                "build_id": report["stages"][0]["evidence"]["build_id"],
                "source_revision": report["source_token"],
                "runs": [{"verification": pair[0].name, "native": pair[1].name}],
            }
        )
    result = compare_runs(read_run(*baseline), read_run(*candidate))
    assert result["state"] == "regressed" and result["coverage_complete"] is False
    assert {row["kind"]: row["classification"] for row in result["checks"]} == {
        "observation": "incomplete",
        "scenario": "new_failure",
        "stage": "unchanged_pass",
    }
    history = tmp_path / "history.json"
    history.write_text(json.dumps({"schema": "verification_build_history@1", "builds": builds}))
    assert inspect_history(history, "scenario", "doors")["state"] == "first_failure"
    assert inspect_history(history, "observation", "doors")["state"] == "incomplete"


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
