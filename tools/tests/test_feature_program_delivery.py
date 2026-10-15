"""Adversarial frozen-provider assessment without live provider or filesystem capabilities."""

from copy import deepcopy

import pytest

from tools import feature_program_delivery as delivery
from tools import feature_program as core


HEAD = "1" * 40
BASE = "2" * 40
TESTED = "3" * 40
BETA = "4" * 40
OTHER = "5" * 40


def test_provider_result_reuse_is_exact_epoch_bound_and_detached(monkeypatch):
    capture, expected = fixture()
    reader = core.ReadBudget(1024)
    context = delivery.ExecutionAssessmentContext(reader)
    calls = []
    original = delivery.validate_execution

    def validate(value):
        calls.append(deepcopy(value))
        return original(value)

    monkeypatch.setattr(delivery, "validate_execution", validate)
    first = context.assess(capture, expected, reader=reader)
    first["status"] = "mutated consumer overlay"
    assert (
        context.assess(deepcopy(capture), deepcopy(expected), reader=reader)["status"] == "passed"
    )
    assert len(calls) == 1
    changed = deepcopy(expected)
    changed["root_actor_id"] = "another-root"
    context.assess(capture, changed, reader=reader)
    current_job(capture)["conclusion"] = "failure"
    assert context.assess(capture, expected, reader=reader)["status"] == "failed"
    assert len(calls) == 3
    with pytest.raises(delivery.DeliveryError, match="epoch differs"):
        context.assess(capture, expected, reader=core.ReadBudget(1024))
    delivery.ExecutionAssessmentContext(reader).assess(capture, expected, reader=reader)
    assert len(calls) == 4


@pytest.mark.parametrize("kind", ["premerge", "cleanup"])
def test_composed_provider_assessment_parses_capture_once(kind, monkeypatch):
    capture, expected = fixture(beta=kind == "cleanup")
    globals()[kind](capture)
    calls = []
    original = delivery.validate_execution

    def validate(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(delivery, "validate_execution", validate)
    result = getattr(delivery, f"assess_{kind}")(capture, expected)
    assert result["status"] == "ready"
    assert len(calls) == 1


def test_shared_job_indexes_keep_each_explicit_step_obligation():
    capture, expected = fixture()
    second = deepcopy(expected["stages"][0]["checks"][0])
    second.update(id="additional", steps=["missing-step"])
    expected["stages"][0]["checks"].append(second)
    capture["stages"][0]["checks"].append(deepcopy(second))
    checks = delivery.assess_execution(capture, expected)["stages"][0]["checks"]
    assert [row["status"] for row in checks] == ["passed", "not_assessed"]


def fixture(*, beta=False):
    """Create an explicit merge-tested PR with actual job/step observations."""
    check = {
        "id": "behavior",
        "job": "dashboard",
        "steps": ["test", "build"],
        "mode": "required",
        "reason": "Required behavior",
        "policy_evidence": [],
        "requires_native": False,
        "requires_output": True,
    }
    plan = {"id": "pr-web", "kind": "pr_ci", "expected_sha": TESTED, "checks": [check]}
    pr = {
        "number": 1,
        "url": "https://github.com/owner/repo/pull/1",
        "head_sha": HEAD,
        "base_ref": "beta",
        "base_sha": BASE,
        "tested_sha": TESTED,
    }
    expected = {
        "repository": "github.com/owner/repo",
        "pr": pr,
        "beta_sha": BETA if beta else None,
        "root_actor_id": "root",
        "stages": [plan],
    }
    job = {
        "job_id": 10,
        "name": "dashboard",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-10-14T12:00:00Z",
        "completed_at": "2026-10-14T12:00:30Z",
        "native": False,
        "steps": [
            {"name": name, "status": "completed", "conclusion": "success", "output_present": True}
            for name in ("test", "build")
        ],
    }
    run = {
        "run_id": 100,
        "run_attempt": 1,
        "event": "pull_request",
        "head_sha": HEAD,
        "checkout_sha": TESTED,
        "status": "completed",
        "conclusion": "success",
        "captured_at": "2026-10-14T12:01:00Z",
        "complete": True,
        "jobs": [job],
    }
    captured_plan = {**deepcopy(plan), "observations": [run]}
    capture = {
        "schema_version": 1,
        "repository": expected["repository"],
        "pr": deepcopy(pr),
        "test_merge": {"head_sha": HEAD, "base_sha": BASE, "merge_sha": TESTED},
        "merge": None,
        "stages": [captured_plan],
        "premerge": None,
        "cleanup": None,
    }
    if beta:
        beta_plan = {**deepcopy(plan), "id": "beta-web", "kind": "beta_ci", "expected_sha": BETA}
        expected["stages"].append(beta_plan)
        beta_run = {
            **deepcopy(run),
            "run_id": 101,
            "event": "push",
            "head_sha": BETA,
            "checkout_sha": BETA,
        }
        beta_run["jobs"][0]["job_id"] = 11
        capture["stages"].append({**deepcopy(beta_plan), "observations": [beta_run]})
        capture["merge"] = {
            "method": "squash",
            "reviewed_head": HEAD,
            "squash_sha": BETA,
            "receiving_sha": BETA,
            "actor_id": "root",
        }
    return capture, expected


def current_run(capture):
    return capture["stages"][0]["observations"][-1]


def current_job(capture):
    return current_run(capture)["jobs"][0]


def premerge(capture):
    capture["premerge"] = {
        "head_sha": HEAD,
        "base_sha": BASE,
        "observed_at": "2026-10-14T12:02:00Z",
        "actor_id": "root",
        "method": "squash",
        "expected_head_guard": HEAD,
        "remote_state_checked": True,
    }


def cleanup(capture):
    capture["cleanup"] = {
        "branch_sha": HEAD,
        "target_sha": BETA,
        "reviewed_head": HEAD,
        "squash_sha": BETA,
        "content_included": True,
        "protected": False,
        "default_branch": False,
        "integration_branch": False,
        "dependent_prs": 0,
        "open_unmerged_pr": False,
        "in_use": False,
        "dirty": False,
        "ignored_artifacts_preserved": True,
        "recovery_verified": True,
        "actor_id": "root",
        "observed_at": "2026-10-14T12:02:00Z",
    }


def test_actual_steps_pass_without_authentication_or_input_mutation():
    # Given a fully captured merge-tested PR.
    capture, expected = fixture()
    before = deepcopy((capture, expected))
    # When its actual execution is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then success is a local consistency assessment, with unchanged frozen inputs.
    assert result["status"] == "passed"
    assert result["remote_verified_by_tool"] is False
    assert (capture, expected) == before
    assert result["stages"][0]["timing"]["duration_seconds"] == 30


@pytest.mark.parametrize("field", ["head_sha", "base_sha", "base_ref", "tested_sha"])
def test_changed_pr_context_never_reuses_green(field):
    # Given the same local scoped code but a changed PR/integration context.
    capture, expected = fixture()
    capture["pr"][field] = "main" if field == "base_ref" else OTHER
    # When a former green capture is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then it fails the expected current context.
    assert result["status"] == "failed"
    assert result["stages"][0]["current"] is False


def test_wrong_repository_fails_even_with_matching_pr_number_and_steps():
    # Given a valid capture from a different repository.
    capture, expected = fixture()
    capture["repository"] = "github.com/other/repo"
    capture["pr"]["url"] = "https://github.com/other/repo/pull/1"
    # When compared with the intended repository.
    result = delivery.assess_execution(capture, expected)
    # Then its success cannot fulfill the intended task.
    assert result["status"] == "failed"


@pytest.mark.parametrize(
    "relation", [None, {"head_sha": HEAD, "base_sha": OTHER, "merge_sha": TESTED}]
)
def test_missing_or_wrong_test_merge_relation_fails(relation):
    # Given execution on a merge revision without its exact parent relationship.
    capture, expected = fixture()
    capture["test_merge"] = relation
    # When it is compared with the frozen PR.
    result = delivery.assess_execution(capture, expected)
    # Then matching run strings alone cannot prove source identity.
    assert result["status"] == "failed"


@pytest.mark.parametrize(
    "field,value", [("head_sha", OTHER), ("checkout_sha", OTHER), ("event", "workflow_dispatch")]
)
def test_old_head_or_wrong_actual_checkout_or_event_fails(field, value):
    # Given a green run against a different revision or event.
    capture, expected = fixture()
    current_run(capture)[field] = value
    # When assessed for PR delivery.
    result = delivery.assess_execution(capture, expected)
    # Then it cannot satisfy the current expected stage.
    assert result["status"] == "failed"


@pytest.mark.parametrize("missing", ["job", "step", "output", "capture"])
def test_green_summary_without_actual_required_execution_never_passes(missing):
    # Given a green summary that omits part of actual required execution.
    capture, expected = fixture()
    match missing:
        case "job":
            current_run(capture)["jobs"] = []
        case "step":
            current_job(capture)["steps"] = []
        case "output":
            current_job(capture)["steps"][0]["output_present"] = False
        case "capture":
            current_run(capture)["complete"] = False
    # When actual required steps/output are assessed.
    result = delivery.assess_execution(capture, expected)
    # Then it remains incomplete.
    assert result["status"] == "not_assessed"


@pytest.mark.parametrize(
    "status,conclusion",
    [("queued", None), ("in_progress", None), ("completed", "canceled"), ("completed", "skipped")],
)
def test_pending_canceled_or_skipped_required_job_never_passes(status, conclusion):
    # Given a required job that has not executed successfully.
    capture, expected = fixture()
    current_job(capture).update(status=status, conclusion=conclusion)
    # When assessed independently of a green run summary.
    result = delivery.assess_execution(capture, expected)
    # Then successful delivery is impossible.
    assert result["status"] != "passed"


def test_failed_required_step_cannot_be_overwritten_by_a_later_skipped_step():
    # Given multiple missing executions including a real test failure.
    capture, expected = fixture()
    steps = current_job(capture)["steps"]
    steps[0]["conclusion"] = "failure"
    steps[1]["conclusion"] = "skipped"
    # When all selected steps are assessed.
    result = delivery.assess_execution(capture, expected)
    # Then failure remains failure instead of being downgraded by array order.
    assert result["status"] == "failed"


def test_canceled_run_never_passes_even_if_old_job_summary_is_green():
    # Given a canceled run whose capture still includes an old green job.
    capture, expected = fixture()
    current_run(capture)["conclusion"] = "cancelled"
    # When the run is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then actual provider cancellation blocks delivery.
    assert result["status"] == "failed"


def test_policy_skipped_native_is_distinct_from_native_execution():
    # Given an optional native suite explicitly disabled by policy.
    capture, expected = fixture()
    for stage in (expected["stages"][0], capture["stages"][0]):
        stage["checks"][0].update(mode="policy_skip", policy_evidence=["license-policy"])
    current_job(capture)["conclusion"] = "skipped"
    # When its applicability is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then the optional skip is retained rather than relabeled native pass.
    assert result["status"] == "passed"
    assert result["stages"][0]["checks"][0]["status"] == "policy_skipped"
    assert result["stages"][0]["checks"][0]["reported_outcome"] == "skipped"


def test_native_required_criterion_cannot_be_satisfied_by_license_skip():
    # Given a native-required obligation with a supplied license exclusion.
    capture, expected = fixture()
    for stage in (expected["stages"][0], capture["stages"][0]):
        stage["checks"][0].update(
            mode="policy_skip", policy_evidence=["license-policy"], requires_native=True
        )
    current_job(capture)["conclusion"] = "skipped"
    # When explicit requirements are assessed.
    result = delivery.assess_execution(capture, expected)
    # Then the criterion still fails.
    assert result["status"] == "failed"


def test_missing_job_is_not_applicable_only_with_matching_external_policy():
    # Given externally selected path nonapplicability and no job.
    capture, expected = fixture()
    for stage in (expected["stages"][0], capture["stages"][0]):
        stage["checks"][0].update(
            mode="not_applicable", policy_evidence=["reviewed-path-selection"]
        )
    current_run(capture)["jobs"] = []
    # When the explicit external plan is compared.
    result = delivery.assess_execution(capture, expected)
    # Then it is an explicit exclusion, with no fabricated execution result.
    assert result["stages"][0]["checks"][0]["status"] == "not_applicable"
    assert result["stages"][0]["checks"][0]["reported_outcome"] is None


def test_capture_cannot_self_waive_required_execution():
    # Given a provider capture that changes a required obligation to a policy exclusion.
    capture, expected = fixture()
    capture["stages"][0]["checks"][0].update(mode="not_applicable", policy_evidence=["asserted"])
    # When compared with the independently frozen required plan.
    result = delivery.assess_execution(capture, expected)
    # Then the changed plan cannot become green.
    assert result["status"] == "failed"


def test_advisory_failed_job_does_not_hide_or_replace_required_execution():
    # Given passing required behavior plus a failed advisory report.
    capture, expected = fixture()
    advisory = {
        **deepcopy(expected["stages"][0]["checks"][0]),
        "id": "timing",
        "job": "report",
        "mode": "advisory",
    }
    expected["stages"][0]["checks"].append(advisory)
    capture["stages"][0]["checks"].append(deepcopy(advisory))
    report = {
        **deepcopy(current_job(capture)),
        "job_id": 12,
        "name": "report",
        "conclusion": "failure",
    }
    current_run(capture)["jobs"].append(report)
    # When obligations are assessed.
    result = delivery.assess_execution(capture, expected)
    # Then required execution passes while the actual advisory failure remains visible.
    assert result["status"] == "passed"
    assert result["stages"][0]["checks"][1]["reported_outcome"] == "failure"


def test_failed_first_attempt_and_successful_retry_preserve_both_job_identities():
    # Given one run ID with a failed attempt and later successful attempt.
    capture, expected = fixture()
    first = current_run(capture)
    second = deepcopy(first)
    first["conclusion"] = "failure"
    first["jobs"][0]["conclusion"] = "failure"
    second.update(run_attempt=2, captured_at="2026-10-14T12:02:00Z")
    second["jobs"][0]["job_id"] = 13
    capture["stages"][0]["observations"].append(second)
    # When the latest execution is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then the retry passes with immutable failure history.
    assert result["status"] == "passed"
    history = result["stages"][0]["history"]
    assert [(row["run_attempt"], row["reported_outcome"], row["job_ids"]) for row in history] == [
        (1, "failure", [10]),
        (2, "success", [13]),
    ]


@pytest.mark.parametrize("conclusion", ["failure", "skipped", None])
def test_later_failed_or_missing_capture_never_revives_old_green(conclusion):
    # Given an old green observation and a later incomplete or failed one.
    capture, expected = fixture()
    latest = deepcopy(current_run(capture))
    latest.update(run_attempt=2, captured_at="2026-10-14T12:02:00Z", conclusion=conclusion)
    latest["jobs"] = [] if conclusion is None else latest["jobs"]
    if latest["jobs"]:
        latest["jobs"][0]["conclusion"] = conclusion
    capture["stages"][0]["observations"].append(latest)
    # When selecting applicable latest execution.
    result = delivery.assess_execution(capture, expected)
    # Then old success remains history only.
    assert result["status"] != "passed"


def test_beta_failure_remains_separate_from_passing_pr_execution():
    # Given a successful PR and failed receiving-beta run.
    capture, expected = fixture(beta=True)
    capture["stages"][1]["observations"][0]["jobs"][0]["conclusion"] = "failure"
    # When staged delivery is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then PR acceptance cannot stand in for beta delivery.
    assert [stage["status"] for stage in result["stages"]] == ["passed", "failed"]
    assert result["status"] == "failed"


@pytest.mark.parametrize(
    "field", ["squash_sha", "receiving_sha", "reviewed_head", "actor_id", "method"]
)
def test_wrong_receiving_mapping_or_nonroot_merge_cannot_pass(field):
    # Given a claimed merge with an unrelated receiving/source/root identity.
    capture, expected = fixture(beta=True)
    merge = dict(capture["merge"])
    merge[field] = "someone" if field == "actor_id" else "merge" if field == "method" else OTHER
    capture["merge"] = merge
    # When delivery is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then exact root squash mapping is required.
    assert result["status"] == "failed"


def test_premerge_is_ready_only_as_advisory_with_current_pr_guard():
    # Given actual passing PR execution and root's recorded current expected-head inspection.
    capture, expected = fixture()
    premerge(capture)
    # When preparation is assessed.
    result = delivery.assess_premerge(capture, expected)
    # Then it is advisory, never authenticated action or merge permission.
    assert result == {
        "status": "ready",
        "reasons": [],
        "advisory": True,
        "remote_verified_by_tool": False,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", OTHER),
        ("base_sha", OTHER),
        ("expected_head_guard", OTHER),
        ("actor_id", "worker"),
        ("remote_state_checked", None),
        ("method", "merge"),
    ],
)
def test_stale_or_unknown_premerge_defers(field, value):
    # Given incomplete or mismatched current premerge facts.
    capture, expected = fixture()
    premerge(capture)
    observation = dict(capture["premerge"])
    observation[field] = value
    capture["premerge"] = observation
    # When assessing readiness.
    result = delivery.assess_premerge(capture, expected)
    # Then no merge readiness is inferred.
    assert result["status"] == "deferred"


def test_cleanup_ready_requires_actual_beta_and_safe_root_facts():
    # Given delivered PR/beta execution and safe explicit branch retirement facts.
    capture, expected = fixture(beta=True)
    cleanup(capture)
    # When cleanup preparation is assessed.
    result = delivery.assess_cleanup(capture, expected)
    # Then it remains advisory and action-free.
    assert result["status"] == "ready"
    assert result["advisory"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("branch_sha", OTHER),
        ("target_sha", OTHER),
        ("content_included", None),
        ("protected", True),
        ("default_branch", True),
        ("integration_branch", True),
        ("dependent_prs", 1),
        ("open_unmerged_pr", True),
        ("in_use", True),
        ("dirty", True),
        ("ignored_artifacts_preserved", False),
        ("recovery_verified", None),
        ("actor_id", "worker"),
    ],
)
def test_advanced_protected_inuse_dirty_or_unknown_cleanup_defers(field, value):
    # Given one unsafe or unknown branch/recovery fact.
    capture, expected = fixture(beta=True)
    cleanup(capture)
    observation = dict(capture["cleanup"])
    observation[field] = value
    capture["cleanup"] = observation
    # When preparing retirement.
    result = delivery.assess_cleanup(capture, expected)
    # Then no automatic deletion eligibility is inferred.
    assert result["status"] == "deferred"


def test_unknown_worktree_use_defers_even_after_successful_beta():
    # Given a delivered branch but incomplete actual worktree usage evidence.
    capture, expected = fixture(beta=True)
    cleanup(capture)
    observation = dict(capture["cleanup"])
    observation["worktree"] = {
        "usage_checked": None,
        "in_use": False,
        "dirty": False,
        "unpushed": False,
        "ignored_artifacts_preserved": True,
        "recovery_verified": True,
    }
    capture["cleanup"] = observation
    # When retirement is assessed.
    result = delivery.assess_cleanup(capture, expected)
    # Then merged/delivered does not prove disuse.
    assert result["status"] == "deferred"


def test_absent_advisory_observations_stay_unknown():
    # Given valid execution but no actual premerge/cleanup inspection.
    capture, expected = fixture()
    # When advisory preparations are requested.
    results = [
        delivery.assess_premerge(capture, expected),
        delivery.assess_cleanup(capture, expected),
    ]
    # Then neither readiness nor cleanup is manufactured.
    assert [result["status"] for result in results] == ["unknown", "unknown"]


def test_missing_timing_is_unknown_and_parallel_jobs_use_elapsed_union():
    # Given two overlapping actual jobs and one missing timing endpoint.
    capture, expected = fixture()
    current_job(capture)["completed_at"] = None
    # When provider timing is projected.
    result = delivery.assess_execution(capture, expected)
    # Then note/run age does not fill missing duration.
    assert result["stages"][0]["timing"]["duration_seconds"] is None


@pytest.mark.parametrize(
    "change", ["version", "unknown", "boolean_id", "duplicate_job", "reverse_time", "naive_time"]
)
def test_explicit_malformed_capture_rejects_instead_of_legacy_fallback(change):
    # Given an explicitly malformed adopted capture.
    capture, expected = fixture()
    match change:
        case "version":
            capture["schema_version"] = True
        case "unknown":
            capture["remote_authenticated"] = True
        case "boolean_id":
            current_run(capture)["run_id"] = True
        case "duplicate_job":
            current_run(capture)["jobs"].append(deepcopy(current_job(capture)))
        case "reverse_time":
            current_job(capture)["completed_at"] = "2026-10-14T11:59:00Z"
        case "naive_time":
            current_job(capture)["started_at"] = "2026-10-14T12:00:00"
    # When the public boundary parses it.
    with pytest.raises(delivery.DeliveryError):
        delivery.assess_execution(capture, expected)
    # Then it did not silently become legacy or passing metadata.


def test_bounded_history_omission_does_not_skip_new_failure_assessment():
    # Given more observation history than the UI projection budget.
    capture, expected = fixture()
    observations = capture["stages"][0]["observations"]
    for number in range(2, 35):
        run = deepcopy(observations[0])
        run["run_attempt"] = number
        run["jobs"][0]["job_id"] = number + 10
        run["captured_at"] = f"2026-10-14T12:{number:02}:00Z"
        observations.append(run)
    observations[-1]["jobs"][0]["conclusion"] = "failure"
    # When bounded history is projected.
    result = delivery.assess_execution(capture, expected)
    # Then all source rows were assessed and omissions are explicit.
    assert result["status"] == "failed"
    stage = result["stages"][0]
    assert (stage["history_total"], stage["history_shown"], stage["history_omitted"]) == (34, 32, 2)
    assert stage["history"][-1]["run_attempt"] == 34


def test_old_attempt_recapture_cannot_replace_later_failed_attempt():
    # Given a failed second attempt followed by recapture of an older green attempt.
    capture, expected = fixture()
    original = deepcopy(current_run(capture))
    second = deepcopy(original)
    second.update(run_attempt=2, captured_at="2026-10-14T12:02:00Z", conclusion="failure")
    second["jobs"][0]["conclusion"] = "failure"
    original["captured_at"] = "2026-10-14T12:03:00Z"
    capture["stages"][0]["observations"].extend([second, original])
    # When parsing the explicitly ordered retry history.
    with pytest.raises(delivery.DeliveryError):
        delivery.assess_execution(capture, expected)
    # Then later capture time alone cannot revive a known older attempt.


def test_omitted_required_check_failure_still_blocks_entire_stage():
    # Given enough selected obligations to omit a failed native obligation from display.
    capture, expected = fixture()
    template = deepcopy(expected["stages"][0]["checks"][0])
    checks = [
        {**deepcopy(template), "id": f"check-{number}", "requires_native": number == 34}
        for number in range(35)
    ]
    expected["stages"][0]["checks"] = deepcopy(checks)
    capture["stages"][0]["checks"] = checks
    # When the reduced projection is generated.
    result = delivery.assess_execution(capture, expected)
    # Then omitted severe results still determine the assessment.
    assert result["status"] == "failed"
    stage = result["stages"][0]
    assert (stage["checks_total"], stage["checks_shown"], stage["checks_omitted"]) == (35, 32, 3)


def test_bounded_run_job_identity_projection_preserves_total():
    # Given many actual matrix jobs inside one immutable observation.
    capture, expected = fixture()
    template = deepcopy(current_job(capture))
    current_run(capture)["jobs"].extend(
        [
            {**deepcopy(template), "job_id": number + 100, "name": f"matrix-{number}"}
            for number in range(40)
        ]
    )
    # When provider history is projected.
    result = delivery.assess_execution(capture, expected)
    # Then job identities are bounded with exact omission counts.
    history = result["stages"][0]["history"][0]
    assert len(history["job_ids"]) == 32
    assert (history["jobs_total"], history["jobs_shown"], history["jobs_omitted"]) == (41, 32, 9)


def test_capture_before_actual_job_completion_is_rejected():
    # Given incompatible capture and actual-execution clocks.
    capture, expected = fixture()
    current_run(capture)["captured_at"] = "2026-10-14T11:59:00Z"
    # When parsing the recorded execution.
    with pytest.raises(delivery.DeliveryError):
        delivery.assess_execution(capture, expected)
    # Then a future job outcome cannot be claimed observed already.


def test_unknown_attempt_remains_unassessed_instead_of_reusing_green():
    # Given an otherwise green capture with no actual run-attempt identity.
    capture, expected = fixture()
    current_run(capture)["run_attempt"] = None
    # When execution is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then unknown retry identity remains unknown.
    assert result["status"] == "not_assessed"
    assert result["stages"][0]["history"][0]["run_attempt"] is None


def test_policy_skip_cannot_satisfy_execution_with_a_still_running_job():
    # Given an excluded job with inconsistent running/skip facts.
    capture, expected = fixture()
    for stage in (expected["stages"][0], capture["stages"][0]):
        stage["checks"][0].update(mode="policy_skip", policy_evidence=["evidence/policy.json"])
    current_job(capture).update(status="in_progress", conclusion="skipped")
    # When an actual policy skip is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then only completed recorded skips count.
    assert result["status"] != "passed"


def test_overlapping_provider_jobs_project_elapsed_envelope_not_summed_work():
    # Given two overlapping jobs with known same-provider timestamps.
    capture, expected = fixture()
    other = {
        **deepcopy(current_job(capture)),
        "job_id": 20,
        "name": "other",
        "started_at": "2026-10-14T12:00:15Z",
        "completed_at": "2026-10-14T12:00:45Z",
    }
    current_run(capture)["jobs"].append(other)
    # When provider wall elapsed is projected.
    result = delivery.assess_execution(capture, expected)
    # Then it is 45 seconds, never 60 seconds of summed work or director idle.
    assert result["stages"][0]["timing"]["duration_seconds"] == 45


@pytest.mark.parametrize("conclusion", [None, "pending", "skipped", "neutral"])
def test_unfinished_or_unassessed_run_summary_never_claims_delivery(conclusion):
    # Given green nested jobs but no actual successful required run conclusion.
    capture, expected = fixture()
    current_run(capture)["conclusion"] = conclusion
    # When whole execution is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then run uncertainty remains visible and nonpassing.
    assert result["status"] != "passed"


def test_missing_advisory_report_is_distinct_without_blocking_required_behavior():
    # Given actual required behavior plus an unavailable advisory report.
    capture, expected = fixture()
    advisory = {
        **deepcopy(expected["stages"][0]["checks"][0]),
        "id": "report",
        "job": "report",
        "mode": "advisory",
    }
    expected["stages"][0]["checks"].append(advisory)
    capture["stages"][0]["checks"].append(deepcopy(advisory))
    # When the explicit required/advisory plan is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then passing required execution and missing advisory output remain separate.
    assert result["status"] == "passed"
    assert result["stages"][0]["checks"][1]["status"] == "advisory"
    assert result["stages"][0]["checks"][1]["reported_outcome"] is None


def test_run_failure_exclusively_from_explicit_advisory_jobs_preserves_required_success():
    # Given actual required execution and an explicitly selected failed advisory report.
    capture, expected = fixture()
    advisory = {
        **deepcopy(expected["stages"][0]["checks"][0]),
        "id": "report",
        "job": "report",
        "mode": "advisory",
    }
    expected["stages"][0]["checks"].append(advisory)
    capture["stages"][0]["checks"].append(deepcopy(advisory))
    report = {
        **deepcopy(current_job(capture)),
        "job_id": 15,
        "name": "report",
        "conclusion": "failure",
    }
    current_run(capture)["jobs"].append(report)
    current_run(capture)["conclusion"] = "failure"
    # When complete required/advisory execution is assessed.
    result = delivery.assess_execution(capture, expected)
    # Then advisory failure stays visible without being substituted for required outcomes.
    assert result["status"] == "passed"
    assert result["stages"][0]["reported_outcome"] == "failure"
    assert result["stages"][0]["checks"][1]["reported_outcome"] == "failure"


def test_unexplained_failed_run_never_self_declares_advisory_exclusion():
    # Given green selected behavior and a failed unselected job.
    capture, expected = fixture()
    other = {
        **deepcopy(current_job(capture)),
        "job_id": 16,
        "name": "unknown",
        "conclusion": "failure",
    }
    current_run(capture)["jobs"].append(other)
    current_run(capture)["conclusion"] = "failure"
    # When the expected plan contains no reviewed advisory exception.
    result = delivery.assess_execution(capture, expected)
    # Then unknown failures still block.
    assert result["status"] == "failed"


def test_public_expected_context_validation_needs_no_provider_and_detaches_input():
    # Given an expected plan before actual provider execution exists.
    _, expected = fixture()
    # When the independent public context boundary parses it.
    parsed = delivery.validate_context(expected)
    parsed["pr"]["base_sha"] = OTHER
    # Then parsing creates no provider result and does not alias authored expectations.
    assert expected["pr"]["base_sha"] == BASE
    assert "observations" not in parsed["stages"][0]


@pytest.mark.parametrize("malformation", ["unknown", "wrong_stage_revision", "missing_policy"])
def test_public_context_entry_rejects_malformed_expectations_without_capture(malformation):
    # Given an explicit malformed independently authored expectation.
    _, expected = fixture()
    match malformation:
        case "unknown":
            expected["remote_verified"] = True
        case "wrong_stage_revision":
            expected["stages"][0]["expected_sha"] = OTHER
        case "missing_policy":
            expected["stages"][0]["checks"][0]["mode"] = "policy_skip"
    # When no actual provider capture is available yet.
    with pytest.raises(delivery.DeliveryError):
        delivery.validate_context(expected)
    # Then request validation cannot invent a capture or bypass existing policy guards.
