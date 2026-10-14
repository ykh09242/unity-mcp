"""Meaningful source, observation and compatibility checks on isolated programs."""

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests.test_feature_program import HEAD, fp, make_small_program  # noqa: E402
import feature_program_verification as verification  # noqa: E402


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fp, "git", lambda _root, arguments: "" if arguments[0] == "status" else HEAD
    )
    return make_small_program(tmp_path / "repo", "verification-fixture", ("S01", "S02"))


def request(fixture, identifier="request-1", **fields):
    state = fixture.program.load()
    return {
        "schema_version": 1,
        "operation": "verification-record",
        "id": identifier,
        "kind": "request",
        "task_id": "S01",
        "actor_id": "worker",
        "expected_revision": fp.phases.revision(state),
        "reason": "Synthetic assigned verification",
        "lane": "tests",
        "verifier_id": "qa",
        "check_ids": ["acceptance", "scoped_tests", "format_lint"],
        "input_scope": ["src/S01.py"],
        "input_complete": True,
        "environment": "synthetic-env-1",
        "evidence_destination": "evidence/S01/attempt-1/",
        "next_trigger": "Run synthetic checks",
        **fields,
    }


def record(fixture, value):
    name = fixture.write(f"evidence/_verification/attempt-1/{value['id']}.json", value)
    state = fixture.program.load()
    prepared = verification.prepare_record(fixture.program, state, name, observed_head=HEAD)
    if prepared.before_publish is not None:
        prepared.before_publish()
    fixture.program.path("backlog.json").write_bytes(fp.canonical(prepared.state))
    return prepared.state["verification"]["records"][-1]


def observation(fixture, identifier="finished-1", **fields):
    state = fixture.program.load()
    input_digest = fp.digest(fixture.program.source_snapshot(["src/S01.py"]))
    return {
        "schema_version": 1,
        "operation": "verification-record",
        "id": identifier,
        "kind": "finished",
        "task_id": "S01",
        "actor_id": "qa",
        "expected_revision": fp.phases.revision(state),
        "reason": "Synthetic observed result, not actual product execution",
        "request_id": "request-1",
        "request_version": 1,
        "result": "passed",
        "observed_at": "2026-10-14T10:00:05+00:00",
        "clock_id": "fixture-clock",
        "method": {"command": "synthetic check", "execution_kind": "test"},
        "environment": "synthetic-env-1",
        "input_before": input_digest,
        "input_after": input_digest,
        "artifacts": [fixture.evidence("S01", label=identifier)],
        **fields,
    }


def submission(fixture, **fields):
    log = fixture.evidence("S01", label="acceptance")
    return {
        "task_id": "S01",
        "owner": "worker",
        "observation_ids": ["finished-1"],
        "summary": "Synthetic result",
        "next_action": "Independent review",
        "acceptance_complete": True,
        "acceptance_evidence": [
            {"criterion": "Complete original scope", "status": "passed", "evidence": [log]}
        ],
        "limitations": [],
        **fields,
    }


def test_packet_keeps_exact_original_and_no_future_submission(fixture):
    # Given a claimed task without a submission.
    assert fixture.claim() == 0
    state = fixture.program.load()
    # When the packet resolves current facts without executing anything.
    result = verification.packet(fixture.program, state, "S01", observed_head=HEAD)
    # Then original acceptance and the actual null submission are retained.
    assert result["context"]["attempt"] == 1
    assert result["context"]["submission"] is None
    assert result["original"]["text"] == state["tasks"][0]["source"]["text"]
    assert result["acceptance_complete"] is False


def test_request_records_assignment_without_changing_task_lifecycle(fixture):
    # Given active work and an explicit assignment.
    assert fixture.claim() == 0
    before = deepcopy(fixture.program.load()["tasks"])
    # When the detached change is recorded by its current owner.
    event = record(fixture, request(fixture))
    # Then source identity is automatic and task lifecycle is unchanged.
    assert event["context"]["source_digest"] == fp.digest(
        fixture.program.source_snapshot(["src/S01.py"])
    )
    assert fixture.program.load()["tasks"] == before
    assert (
        verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"]["lanes"][
            "items"
        ][0]["phase"]
        == "requested"
    )


def test_matching_observations_prepare_ordinary_consumable_receipt(fixture):
    # Given matching real fixture artifact bytes for a source-bound check claim.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    state = fixture.program.load()
    # When ordinary execution input is prepared without recopying hashes.
    result = verification.submission_inputs(fixture.program, state, submission(fixture))
    name = fixture.write("evidence/S01/attempt-1/execution-prepared.json", result["receipt"])
    # Then the real existing submit transition consumes it, not a new lifecycle.
    assert result["status"] == "not_assessed"
    assert result["missing_fields"] == []
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", name) == 0
    assert fixture.program.load()["tasks"][0]["status"] == "review"


@pytest.mark.parametrize("result", ["failed", "not_run", "unknown", "not_applicable"])
def test_nonpassing_observation_cannot_prepare_passing_required_check(fixture, result):
    # Given a required check with a nonpassing recorded outcome.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    fields = {"result": result}
    if result == "not_applicable":
        fields["policy_evidence"] = [fixture.evidence("S01", label="policy")]
    record(fixture, observation(fixture, **fields))
    # When the ordinary execution input is prepared.
    prepared = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    # Then acceptance is incomplete and the required check cannot pass.
    assert prepared["receipt"]["acceptance_complete"] is False
    assert all(check["status"] != "passed" for check in prepared["receipt"]["checks"])


def test_legacy_absence_and_explicit_malformed_extension_are_distinct(fixture):
    # Given ordinary legacy state without verification history.
    state = fixture.program.load()
    assert verification.projection(fixture.program, state)["status"] == "absent"
    # When malformed optional state is explicitly supplied.
    state["verification"] = None
    # Then it cannot silently downgrade to legacy.
    with pytest.raises(fp.ProgramError):
        verification.validate(state)


@pytest.mark.parametrize("drift", ["owned", "test", "config", "directory_member", "missing"])
def test_changed_verification_inputs_require_recheck_and_nonpassing_draft(fixture, drift):
    # Given a complete check input closure including shared tests/config and membership.
    assert fixture.claim() == 0
    repo = fixture.program.repo
    (repo / "check_inputs").mkdir()
    (repo / "check_inputs/test.py").write_text("assert True\n", encoding="utf-8")
    (repo / "check_inputs/config.json").write_text("{}\n", encoding="utf-8")
    record(fixture, request(fixture, input_scope=["src/S01.py", "check_inputs"]))
    digest = fp.digest(fixture.program.source_snapshot(["src/S01.py", "check_inputs"]))
    record(fixture, observation(fixture, input_before=digest, input_after=digest))
    match drift:
        case "owned":
            (repo / "src/S01.py").write_text("changed = True\n", encoding="utf-8")
        case "test":
            (repo / "check_inputs/test.py").write_text("assert False\n", encoding="utf-8")
        case "config":
            (repo / "check_inputs/config.json").write_text('{"changed":true}\n', encoding="utf-8")
        case "directory_member":
            (repo / "check_inputs/new.py").write_text("new = True\n", encoding="utf-8")
        case "missing":
            (repo / "check_inputs/test.py").unlink()
    # When the same recorded observation is considered against current actual bytes.
    state = fixture.program.load()
    result = verification.submission_inputs(fixture.program, state, submission(fixture))
    lane = verification.projection(fixture.program, state)["tasks"]["S01"]["lanes"]["items"][0]
    # Then it remains historically passed but cannot support the changed submission.
    assert lane["result"] == "passed" and lane["applicability"] == "stale"
    assert result["receipt"]["acceptance_complete"] is False


def test_newer_failed_result_never_revives_previous_success(fixture):
    # Given a successful observation followed by a later observed failure.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    record(fixture, observation(fixture, "failed-later", result="failed"))
    # When an author explicitly selects the earlier success.
    result = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    # Then newer failure remains visible and old evidence is not accepted.
    assert result["receipt"]["acceptance_complete"] is False
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    assert lane["result"] == "failed"


@pytest.mark.parametrize("kind", ["request", "finished"])
def test_unauthorized_actor_cannot_record_assignment_or_observation(fixture, kind):
    # Given a currently owned task and an unrelated actor.
    assert fixture.claim() == 0
    if kind == "finished":
        record(fixture, request(fixture))
    value = request(fixture) if kind == "request" else observation(fixture)
    value["actor_id"] = "unassigned"
    name = fixture.write("evidence/_verification/attempt-1/unauthorized.json", value)
    before = fixture.program.path("backlog.json").read_bytes()
    # When that actor tries detached preparation.
    with pytest.raises(fp.ProgramError):
        verification.prepare_record(
            fixture.program, fixture.program.load(), name, observed_head=HEAD
        )
    # Then canonical bytes remain unchanged.
    assert fixture.program.path("backlog.json").read_bytes() == before


def test_prepublication_source_drift_rejects_detached_record(fixture):
    # Given a prepared request for current source.
    assert fixture.claim() == 0
    name = fixture.write("evidence/_verification/attempt-1/prepared.json", request(fixture))
    prepared = verification.prepare_record(
        fixture.program, fixture.program.load(), name, observed_head=HEAD
    )
    # When the owned source changes before publication.
    (fixture.program.repo / "src/S01.py").write_text("changed = True\n", encoding="utf-8")
    # Then the actual returned final guard detects it.
    with pytest.raises(fp.ProgramError):
        prepared.before_publish()


@pytest.mark.parametrize(
    "fault",
    ["request_version", "task", "input_drift", "artifact_path", "exit_code", "missing_method"],
)
def test_malformed_or_incompatible_finished_observation_is_rejected(fixture, fault):
    # Given an exact current verification request.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    value = observation(fixture)
    match fault:
        case "request_version":
            value["request_version"] = True
        case "task":
            value["task_id"] = "S02"
        case "input_drift":
            value["input_after"] = "b" * 64
        case "artifact_path":
            value["artifacts"] = [fixture.evidence("S02")]
        case "exit_code":
            value["exit_code"] = 1
        case "missing_method":
            value.pop("method")
    path = fixture.write("evidence/_verification/attempt-1/invalid.json", value)
    # When malformed input crosses the real preparation boundary.
    with pytest.raises(fp.ProgramError):
        verification.prepare_record(
            fixture.program, fixture.program.load(), path, observed_head=HEAD
        )
    # Then no additional observation was published.
    assert len(fixture.program.load()["verification"]["records"]) == 1


@pytest.mark.parametrize(
    "clock,end,seconds",
    [
        ("fixture-clock", "2026-10-14T10:00:05+00:00", 5),
        ("other-clock", "2026-10-14T10:00:05+00:00", None),
    ],
)
def test_waiting_uses_only_compatible_observed_endpoints(fixture, clock, end, seconds):
    # Given an actually observed request start, never inferred from publication time.
    assert fixture.claim() == 0
    record(
        fixture, request(fixture, observed_at="2026-10-14T10:00:00+00:00", clock_id="fixture-clock")
    )
    record(fixture, observation(fixture, observed_at=end, clock_id=clock))
    # When the shared dashboard projection computes the reported interval.
    waiting = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]["waiting"]
    # Then mixed clocks never produce a numeric duration.
    assert waiting["seconds"] == seconds


def test_reversed_started_finished_interval_is_rejected(fixture):
    # Given an actual started boundary.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    value = observation(fixture, "started", kind="started", observed_at="2026-10-14T10:00:10+00:00")
    for name in ("input_after", "result"):
        value.pop(name)
    record(fixture, value)
    finished = observation(fixture)
    path = fixture.write("evidence/_verification/attempt-1/reversed.json", finished)
    # When a finished record reports an earlier endpoint on the same clock.
    with pytest.raises(fp.ProgramError):
        verification.prepare_record(
            fixture.program, fixture.program.load(), path, observed_head=HEAD
        )
    # Then the latest recorded phase remains running.
    assert (
        verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"]["lanes"][
            "items"
        ][0]["phase"]
        == "running"
    )


def test_environment_mismatch_and_incomplete_input_are_nonpassing(fixture):
    # Given a request's explicit environment and incomplete closure.
    assert fixture.claim() == 0
    record(fixture, request(fixture, input_complete=False))
    record(fixture, observation(fixture, environment="different-env"))
    # When the observation is selected for a new execution receipt.
    result = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    # Then neither incomplete inputs nor changed environment can silently pass.
    assert result["receipt"]["acceptance_complete"] is False


def test_context_tampering_is_rejected_by_historical_validation(fixture):
    # Given a retained immutable request input.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    state = fixture.program.load()
    state["verification"]["records"][0]["context"]["input_scope"] = ["src/S02.py"]
    # When a stored derived binding disagrees with its actual original input.
    with pytest.raises(fp.ProgramError):
        verification.verify_history(fixture.program, state, None)


def test_request_context_does_not_change_after_unrelated_note(fixture):
    # Given frozen verification facts followed by unrelated authored progress.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    assert (
        fixture.run(
            "note",
            "S01",
            "--owner",
            "worker",
            "--reason",
            "Unrelated context",
            "--next-action",
            "Keep checking",
        )
        == 0
    )
    # When the same input/result is prepared at the newer registry revision.
    result = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    # Then stable source and obligation identity survive routine unrelated progress.
    assert result["receipt"]["acceptance_complete"] is True


@pytest.mark.parametrize(
    "change,expected",
    [
        ("none", "reuse_candidate"),
        ("environment", "recheck_required"),
        ("method", "recheck_required"),
        ("closure", "not_assessed"),
        ("ci", "recheck_required"),
    ],
)
def test_reuse_is_conservative_and_never_waives_ci(fixture, change, expected):
    # Given passing frozen local evidence and a newly explicit same-input request.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    origin = record(fixture, observation(fixture))
    new_fields = {"supersedes": "request-1"}
    if change == "closure":
        new_fields["input_complete"] = False
    if change == "ci":
        new_fields.pop("supersedes")
        new_fields["lane"] = "pr_ci"
    new_request = record(fixture, request(fixture, "request-2", **new_fields))
    target_input = observation(
        fixture,
        "new-result",
        request_id="request-2",
        request_version=new_request["request_version"],
    )
    if change == "environment":
        target_input["environment"] = "new-environment"
    if change == "method":
        target_input["method"]["command"] = "changed check"
    target = {"input": target_input, "context": new_request["context"], "request_id": "request-2"}
    # When the pure assessment compares exact inputs rather than a Git commit alone.
    result = verification.assess_reuse(fixture.program, fixture.program.load(), origin, target)
    # Then matching complete inputs are only advisory and changed/ambient CI is protected.
    assert result["status"] == expected
    assert result["advisory"] is True


def test_resolution_preserves_finding_and_does_not_accept_task(fixture):
    # Given an actual structured finding on exact source and a separate correction observation.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    finding = observation(
        fixture,
        "finding-1",
        kind="finding",
        statement="Synthetic defect",
        affected_paths=["src/S01.py"],
        check_ids=["scoped_tests"],
        criteria=[],
        next_action="Fix synthetic result",
    )
    for name in ("result", "method", "environment", "input_before", "input_after"):
        finding.pop(name)
    record(fixture, finding)
    record(fixture, observation(fixture))
    resolution = observation(
        fixture,
        "resolution-1",
        kind="resolution",
        finding_id="finding-1",
        observation_id="finished-1",
        next_action="Request exact independent review",
    )
    for name in ("result", "method", "environment", "input_before", "input_after"):
        resolution.pop(name)
    # When the exact resolution is recorded as a fact.
    record(fixture, resolution)
    projection = verification.projection(fixture.program, fixture.program.load())
    # Then the finding remains recorded and task acceptance stays in the old lifecycle.
    item = projection["tasks"]["S01"]["findings"]["items"][0]
    assert item["status"] == "recorded"
    assert item["resolutions"][0]["id"] == "resolution-1"
    assert fixture.program.load()["tasks"][0]["status"] == "active"


def test_projection_artifact_unavailable_preserves_recorded_result(fixture):
    # Given historical passing facts whose actual retained output later disappears.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    event = record(fixture, observation(fixture))
    state = fixture.program.load()
    fixture.program.path(event["input"]["artifacts"][0]).unlink()
    # When read-only display prepares the optional projection.
    projection = verification.projection(fixture.program, state)
    # Then it reports unavailable evidence instead of inventing a current pass or losing history.
    lane = projection["tasks"]["S01"]["lanes"]["items"][0]
    assert lane["result"] == "passed" and lane["applicability"] == "unavailable"
    assert projection["integrity"]["unavailable_total"] == 1


def test_historical_receipt_reliance_survives_later_source_changes(fixture):
    # Given an ordinary submitted receipt with exact immutable verification refs.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    prepared = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    path = fixture.write("evidence/S01/attempt-1/ordinary.json", prepared["receipt"])
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", path) == 0
    state = fixture.program.load()
    (fixture.program.repo / "src/S01.py").write_text("later_change = True\n", encoding="utf-8")
    # When historical integrity is checked without replaying today's acceptance.
    verification.verify_history(fixture.program, state, None)
    # Then the retained receipt remains historically valid while current applicability is stale.
    assert (
        verification.projection(fixture.program, state)["tasks"]["S01"]["lanes"]["items"][0][
            "applicability"
        ]
        == "stale"
    )


def test_blocked_lane_exposes_explicit_resolver_trigger_without_work_inference(fixture):
    # Given a verifier's observed blocker with an exact next trigger.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    value = observation(
        fixture,
        "blocked-1",
        kind="blocked",
        blocker={
            "reason": "Synthetic resource busy",
            "resolver_id": "qa",
            "next_trigger": "Resource released",
        },
        observed_at=None,
        clock_id=None,
    )
    for name in ("result", "method", "environment", "input_before", "input_after"):
        value.pop(name)
    record(fixture, value)
    # When the shared lane projection is read.
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then recorded blocking is actionable but no measured elapsed execution is invented.
    assert lane["phase"] == "blocked"
    assert lane["resolver_id"] == "qa" and lane["next_trigger"] == "Resource released"
    assert lane["waiting"]["seconds"] is None


@pytest.mark.parametrize("change", ["remove_extension", "remove_record", "edit_record"])
def test_save_guard_rejects_removing_or_rewriting_recorded_history(fixture, change):
    # Given an already canonical verification record.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    future = deepcopy(fixture.program.load())
    match change:
        case "remove_extension":
            future.pop("verification")
        case "remove_record":
            future["verification"]["records"] = []
        case "edit_record":
            future["verification"]["records"][0]["input"]["reason"] = "Rewritten history"
    # When the same publication guard used by the host assesses it.
    with pytest.raises(fp.ProgramError):
        verification.publication_guard(fixture.program, future)
    # Then the actual retained canonical history is still present.
    assert len(fixture.program.load()["verification"]["records"]) == 1


def test_explicit_incomplete_observation_reliance_cannot_bypass_draft(fixture):
    # Given a caller-reported pass with missing actual executed input identity.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    event = record(fixture, observation(fixture, input_before=None, input_after=None))
    receipt = fp.read_json(fixture.program.path(fixture.receipt()))
    receipt["verification_refs"] = [{"id": "finished-1", "receipt": event["receipt"]}]
    # When someone tries an explicitly hand-authored passing receipt instead of preparation.
    with pytest.raises(fp.ProgramError):
        verification.validate_receipt_refs(fixture.program, fixture.program.load(), receipt, "S01")


def test_reuse_cannot_resurrect_origin_with_a_newer_failure(fixture):
    # Given a formerly passing origin followed by failure on that same request.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    origin = record(fixture, observation(fixture))
    record(fixture, observation(fixture, "failed-after-origin", result="failed"))
    next_request = record(fixture, request(fixture, "request-2", supersedes="request-1"))
    target = {
        "input": observation(fixture, request_id="request-2", request_version=2),
        "context": next_request["context"],
        "request_id": "request-2",
    }
    # When explicit reuse assesses the old result despite a matching input digest.
    result = verification.assess_reuse(fixture.program, fixture.program.load(), origin, target)
    # Then it requires recheck rather than restoring the old pass.
    assert result["status"] == "recheck_required"


def test_native_check_draft_rejects_compile_observation(fixture):
    # Given an explicitly required native check and a compilation-only observation.
    assert fixture.claim() == 0
    state = fixture.program.load()
    state["tasks"][0]["required_checks"].append("native_editor")
    fixture.program.path("backlog.json").write_bytes(fp.canonical(state))
    record(fixture, request(fixture, check_ids=["native_editor"]))
    record(
        fixture,
        observation(fixture, method={"command": "compile only", "execution_kind": "compile"}),
    )
    # When execution input is prepared from that claim.
    result = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    # Then native obligation remains nonpassing.
    assert (
        next(row for row in result["receipt"]["checks"] if row["id"] == "native_editor")["status"]
        != "passed"
    )


def test_historical_source_digest_cannot_be_rebound_silently(fixture):
    # Given a source binding retained in exact audit history.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    state = fixture.program.load()
    state["verification"]["records"][0]["context"]["source_digest"] = "f" * 64
    # When someone rewrites resolved source identity but leaves original audit evidence.
    with pytest.raises(fp.ProgramError):
        verification.validate(state)


def test_projection_rejects_essential_encoded_output_overflow(fixture, monkeypatch):
    # Given a valid projection that cannot fit the explicit essential envelope bound.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    monkeypatch.setattr(verification, "OUTPUT_LIMIT", 100)
    # When projection would silently exceed its encoded byte budget.
    with pytest.raises(fp.ProgramError, match="projection exceeds"):
        verification.projection(fixture.program, fixture.program.load())


def test_ambient_environment_unknown_prohibits_reuse(fixture):
    # Given an origin without observed runtime environment identity.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    origin = record(fixture, observation(fixture, environment=None))
    new_request = record(fixture, request(fixture, "request-2", supersedes="request-1"))
    target = {
        "input": observation(fixture, environment=None),
        "context": new_request["context"],
        "request_id": "request-2",
    }
    # When reuse is considered against identical source bytes.
    result = verification.assess_reuse(fixture.program, fixture.program.load(), origin, target)
    # Then missing runtime facts remain unassessed.
    assert result["status"] == "not_assessed"


def test_exact_observation_carried_by_submission_remains_applicable(fixture):
    # Given verified evidence explicitly carried by the actual current execution receipt.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    result = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    path = fixture.write("evidence/S01/attempt-1/carried.json", result["receipt"])
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", path) == 0
    # When normal submit advances null submission into actual round one.
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then only that exact frozen carried evidence remains relevant to review.
    assert lane["applicability"] == "current"


def test_rework_removes_carried_exception_without_reviving_old_success(fixture):
    # Given a carried observation and an exact rejected review.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    result = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    path = fixture.write("evidence/S01/attempt-1/rejected.json", result["receipt"])
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", path) == 0
    review = fixture.review(accepted=False)
    assert fixture.run("rework", "S01", "--review", review) == 0
    # When rejected work returns to active with the old immutable round retained.
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then the old observation cannot be reused as current merely by identical bytes.
    assert lane["applicability"] == "stale"


def test_missing_authored_acceptance_artifact_prepares_nonpassing_draft(fixture):
    # Given actual matching check evidence but an unobserved acceptance artifact.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    record(fixture, observation(fixture))
    request_value = submission(fixture)
    request_value["acceptance_evidence"][0]["evidence"] = ["evidence/S01/attempt-1/missing.txt"]
    # When the helper prepares ordinary execution input.
    result = verification.submission_inputs(fixture.program, fixture.program.load(), request_value)
    # Then authored passed text cannot conceal absent criterion proof.
    assert result["receipt"]["acceptance_complete"] is False


def test_failed_observation_is_distinct_from_not_run_in_prepared_receipt(fixture):
    # Given an actually recorded failed check.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    record(fixture, observation(fixture, result="failed"))
    # When the ordinary execution receipt is prepared.
    result = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    # Then failed outcome is retained distinctly while acceptance remains incomplete.
    assert all(row["status"] == "failed" for row in result["receipt"]["checks"])
    assert result["receipt"]["acceptance_complete"] is False


def test_preparation_never_executes_recorded_commands_or_acquires_lock(fixture, monkeypatch):
    # Given current owned work and an inert command description containing shell syntax.
    assert fixture.claim() == 0
    record(fixture, request(fixture))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Unexpected execution or publication")

    monkeypatch.setattr(fp, "git", forbidden)
    monkeypatch.setattr(fp, "atomic_write", forbidden)
    monkeypatch.setattr(fp.Program, "lock", forbidden)
    # When read-only preparations consume the recorded description.
    event = record(
        fixture,
        observation(
            fixture, method={"command": "synthetic $(never-execute)", "execution_kind": "test"}
        ),
    )
    result = verification.packet(fixture.program, fixture.program.load(), "S01")
    # Then metadata is preserved without trusted execution or acceptance.
    assert event["input"]["method"]["command"] == "synthetic $(never-execute)"
    assert result["status"] == "not_assessed"
