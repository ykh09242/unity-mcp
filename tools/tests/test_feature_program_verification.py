"""Meaningful source, observation and compatibility checks on isolated programs."""

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests.test_feature_program import HEAD, fp, make_small_program  # noqa: E402
import feature_program_verification as verification  # noqa: E402
from tools.tests.test_feature_program_delivery import fixture as provider_fixture  # noqa: E402


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


@pytest.mark.parametrize("outcome", ["failed", "unknown", "canceled", "blocked"])
def test_reuse_rejects_newer_adverse_outcome_across_superseding_requests(fixture, outcome):
    # Given the reviewer's exact chronology: original pass, newer same-lane adverse fact.
    assert fixture.claim() == 0
    record(fixture, request(fixture))
    origin = record(fixture, observation(fixture))
    second = record(fixture, request(fixture, "request-2", supersedes="request-1"))
    later = observation(
        fixture,
        "newer-adverse",
        request_id="request-2",
        request_version=second["request_version"],
        result=outcome,
    )
    if outcome in {"canceled", "blocked"}:
        later["kind"] = outcome
        for name in ("result", "method", "environment", "input_before", "input_after"):
            later.pop(name)
        if outcome == "canceled":
            later["next_trigger"] = "Fresh verification request"
        else:
            later["blocker"] = {
                "reason": "Actual fixture blocker",
                "resolver_id": "qa",
                "next_trigger": "Resolve fixture blocker",
            }
    record(fixture, later)
    third = record(fixture, request(fixture, "request-3", supersedes="request-2"))
    target = {
        "input": observation(
            fixture, request_id="request-3", request_version=third["request_version"]
        ),
        "context": third["context"],
        "request_id": "request-3",
    }
    # When reuse tries to select the much older original pass after supersession.
    assessment = verification.assess_reuse(fixture.program, fixture.program.load(), origin, target)
    # Then no unchanged digest can revive success across relevant newer adverse evidence.
    assert assessment["status"] == "recheck_required"


@pytest.mark.parametrize("phase", ["request", "started", "blocked", "failed"])
def test_completion_honors_required_current_lanes_without_voluntary_refs(fixture, phase):
    # Given actual submitted work and a current future review/QA lane.
    assert fixture.claim() == 0
    fixture.submit()
    record(fixture, request(fixture, actor_id="leader"))
    if phase != "request":
        value = observation(
            fixture,
            "gate-observation",
            kind="finished" if phase == "failed" else phase,
            result="failed",
        )
        if phase in {"started", "blocked"}:
            for name in ("result", "input_after"):
                value.pop(name)
            if phase == "blocked":
                for name in ("method", "environment", "input_before"):
                    value.pop(name)
                value["blocker"] = {
                    "reason": "Synthetic required QA blocker",
                    "resolver_id": "qa",
                    "next_trigger": "Run required QA",
                }
        record(fixture, value)
    # When acceptance is assessed independently of voluntary receipt refs.
    with pytest.raises(fp.ProgramError):
        verification.validate_completion(fixture.program, fixture.program.load(), "S01")


def test_completion_accepts_current_finished_lane_and_preserves_legacy(fixture):
    # Given a submitted task without optional verification metadata.
    assert fixture.claim() == 0
    fixture.submit()
    state = fixture.program.load()
    verification.validate_completion(fixture.program, state, "S01")
    record(fixture, request(fixture, actor_id="leader"))
    record(fixture, observation(fixture))
    # When every adopted required lane has current actual passing observations.
    verification.validate_completion(fixture.program, fixture.program.load(), "S01")
    # Then assessment grants no task transition itself.
    assert fixture.program.load()["tasks"][0]["status"] == "review"


def provider_pair(fixture, *, different_head=False):
    capture, expected = provider_fixture()
    if not different_head:
        expected["pr"]["head_sha"] = HEAD
        capture["pr"]["head_sha"] = HEAD
        capture["test_merge"]["head_sha"] = HEAD
        capture["stages"][0]["observations"][0]["head_sha"] = HEAD
    context_path = fixture.write(
        "evidence/_verification/attempt-1/provider-expected.json", expected
    )
    provider_path = fixture.write(
        "evidence/_verification/attempt-1/provider-captured.json", {"execution": capture}
    )
    provider_ref = {
        "path": provider_path,
        "sha256": fp.sha(fixture.program.path(provider_path).read_bytes()),
    }
    return context_path, provider_ref, expected


def test_submitted_task_exposes_actual_early_provider_assessment_without_delivery(fixture):
    # Given actual submitted work with a frozen expected CI plan and captured run.
    assert fixture.claim() == 0
    fixture.submit()
    context_path, provider_ref, _ = provider_pair(fixture)
    record(
        fixture,
        request(fixture, actor_id="leader", lane="pr_ci", provider_context_path=context_path),
    )
    record(fixture, observation(fixture, provider_ref=provider_ref))
    # When the ordinary verification projection is prepared before reviewed completion.
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then real staged provider facts are visible without inventing delivery or authentication.
    assert lane["provider_execution"]["status"] == "passed"
    assert lane["provider_execution"]["stages"][0]["current"] is True
    assert lane["provider_execution"]["remote_verified_by_tool"] is False
    assert fixture.program.load()["tasks"][0]["status"] == "review"


def test_early_provider_no_latest_capture_does_not_revive_older_pass(fixture):
    # Given a passing provider capture then a later canceled current observation.
    assert fixture.claim() == 0
    context_path, provider_ref, _ = provider_pair(fixture)
    record(fixture, request(fixture, lane="pr_ci", provider_context_path=context_path))
    record(fixture, observation(fixture, provider_ref=provider_ref))
    canceled = observation(
        fixture, "cancel-provider", kind="canceled", next_trigger="New CI capture"
    )
    for name in ("result", "method", "environment", "input_before", "input_after"):
        canceled.pop(name)
    record(fixture, canceled)
    # When the latest operational event lacks provider capture.
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then prior green provider data is not selected as current.
    assert lane["provider_execution"]["status"] != "passed"
    assert lane["provider_execution"]["stages"] == []


@pytest.mark.parametrize(
    "fault", ["head", "scope", "snapshot", "missing_evidence", "missing_mapping", "malformed"]
)
def test_distinct_provider_head_requires_exact_frozen_source_equivalence(fixture, fault):
    # Given distinct recorder/provider heads with a correctly observed task snapshot.
    assert fixture.claim() == 0
    context_path, _, expected = provider_pair(fixture, different_head=True)
    snapshot = fixture.program.source_snapshot(["src/S01.py"])
    mapping = {
        "schema_version": 1,
        "provider_head": expected["pr"]["head_sha"],
        "scope": ["src/S01.py"],
        "provider_snapshot": snapshot,
        "local_snapshot": snapshot,
        "evidence": [fixture.evidence("S01", label="blob-comparison")],
    }
    match fault:
        case "head":
            mapping["provider_head"] = "f" * 40
        case "scope":
            mapping["scope"] = ["src/S02.py"]
        case "snapshot":
            mapping["provider_snapshot"] = {"src/S01.py": "f" * 64}
        case "missing_evidence":
            mapping["evidence"] = []
        case "malformed":
            mapping["unexpected"] = True
        case "missing_mapping":
            pass
    mapping_path = fixture.write("evidence/_verification/attempt-1/source-mapping.json", mapping)
    value = request(fixture, lane="pr_ci", provider_context_path=context_path)
    if fault != "missing_mapping":
        value["provider_source_mapping_path"] = mapping_path
    name = fixture.write("evidence/_verification/attempt-1/request-mapping.json", value)
    # When malformed or absent explicit equivalence is prepared.
    with pytest.raises(fp.ProgramError):
        verification.prepare_record(
            fixture.program, fixture.program.load(), name, observed_head=HEAD
        )


def test_distinct_provider_head_retains_exact_identity_with_valid_mapping(fixture):
    # Given actual distinct provider identity and an explicit frozen file comparison.
    assert fixture.claim() == 0
    context_path, provider_ref, expected = provider_pair(fixture, different_head=True)
    snapshot = fixture.program.source_snapshot(["src/S01.py"])
    mapping_path = fixture.write(
        "evidence/_verification/attempt-1/source-equivalence.json",
        {
            "schema_version": 1,
            "provider_head": expected["pr"]["head_sha"],
            "scope": ["src/S01.py"],
            "provider_snapshot": snapshot,
            "local_snapshot": snapshot,
            "evidence": [fixture.evidence("S01", label="exact-blob-comparison")],
        },
    )
    event = record(
        fixture,
        request(
            fixture,
            lane="pr_ci",
            provider_context_path=context_path,
            provider_source_mapping_path=mapping_path,
        ),
    )
    record(fixture, observation(fixture, provider_ref=provider_ref))
    # When source equivalence is assessed without rewriting remote identity to local HEAD.
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then both identities remain distinct and the original expected provider run is assessed.
    assert event["context"]["observed_head"] == HEAD
    assert (
        lane["provider_execution"]["stages"][0]["source"]["head_sha"]
        == expected["pr"]["head_sha"]
        != HEAD
    )
    assert lane["provider_execution"]["status"] == "passed"


def test_native_actor_identity_is_distinct_from_portable_event_ids(fixture):
    # Given the actual established native owner naming contract accepted by claim.
    owner, verifier = "/root/throughput_state", "/root/throughput_code_review"
    assert fixture.claim(owner=owner) == 0
    event = record(fixture, request(fixture, actor_id=owner, verifier_id=verifier))
    blocked = observation(
        fixture,
        "native-blocker",
        kind="blocked",
        actor_id=verifier,
        blocker={
            "reason": "Native reviewer resource",
            "resolver_id": owner,
            "next_trigger": "Native review resumes",
        },
    )
    for name in ("result", "method", "environment", "input_before", "input_after"):
        blocked.pop(name)
    # When the exact native verifier and resolver are recorded without aliases.
    record(fixture, blocked)
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then actor strings survive while request/event IDs remain portable.
    assert event["input"]["actor_id"] == owner
    assert lane["verifier_id"] == verifier and lane["resolver_id"] == owner


@pytest.mark.parametrize("fault", ["missing_execution", "malformed_expected", "null_reference"])
def test_early_provider_explicit_malformed_data_is_not_legacy_absence(fixture, fault):
    # Given an explicitly adopted early provider plan.
    assert fixture.claim() == 0
    context_path, provider_ref, _ = provider_pair(fixture)
    if fault == "malformed_expected":
        fixture.write(context_path, {"unexpected": True})
        value = request(fixture, lane="pr_ci", provider_context_path=context_path)
    else:
        record(fixture, request(fixture, lane="pr_ci", provider_context_path=context_path))
        if fault == "missing_execution":
            fixture.write(provider_ref["path"], {})
            provider_ref["sha256"] = fp.sha(fixture.program.path(provider_ref["path"]).read_bytes())
        value = observation(
            fixture, provider_ref=None if fault == "null_reference" else provider_ref
        )
    name = fixture.write("evidence/_verification/attempt-1/malformed-provider-input.json", value)
    # When explicit malformed context/capture is prepared.
    with pytest.raises(fp.ProgramError):
        verification.prepare_record(
            fixture.program, fixture.program.load(), name, observed_head=HEAD
        )


def test_early_provider_source_change_cannot_display_current_green(fixture):
    # Given passing provider observations on an exact expected current source.
    assert fixture.claim() == 0
    context_path, provider_ref, _ = provider_pair(fixture)
    record(fixture, request(fixture, lane="pr_ci", provider_context_path=context_path))
    record(fixture, observation(fixture, provider_ref=provider_ref))
    (fixture.program.repo / "src/S01.py").write_text("source_changed = True\n", encoding="utf-8")
    # When the current source no longer matches the frozen request.
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then recorded provider success remains historical and cannot show current green.
    assert lane["applicability"] == "stale"
    assert lane["provider_execution"]["current"] is False
    assert lane["provider_execution"]["status"] != "passed"


def test_selected_provider_missing_capture_is_nonpassing_but_submit_can_continue(fixture):
    # Given future CI pending while local implementation is ready for ordinary submission.
    assert fixture.claim() == 0
    context_path, _, _ = provider_pair(fixture)
    record(fixture, request(fixture, lane="pr_ci", provider_context_path=context_path))
    record(fixture, observation(fixture))
    # When a caller tries to use unobserved CI as a passing selected check.
    draft = verification.submission_inputs(
        fixture.program, fixture.program.load(), submission(fixture)
    )
    # Then it remains nonpassing, but an ordinary independent implementation receipt may submit.
    assert draft["receipt"]["acceptance_complete"] is False
    assert fixture.run("submit", "S01", "--owner", "worker", "--receipt", fixture.receipt()) == 0


@pytest.mark.parametrize("adopted", [False, True])
def test_publication_guard_allows_new_planned_task_without_existing_identity(fixture, adopted):
    # Given canonical existing tasks, optionally with adopted verification history.
    if adopted:
        assert fixture.claim() == 0
        record(fixture, request(fixture))
    state = fixture.program.load()
    future = deepcopy(state)
    new_task = deepcopy(future["tasks"][1])
    new_task.update({"id": "new-planned", "candidate_paths": ["src/new-planned.py"]})
    future["tasks"].append(new_task)
    # When a reviewed admission/split proposal reaches the shared publication guard.
    guard = verification.publication_guard(fixture.program, future)
    # Then newly planned identity is not looked up as an existing completion transition.
    assert guard is None
    assert fixture.program.path("backlog.json").read_bytes() == fp.canonical(state)


def test_inherited_registry_capacity_survives_actual_submission_publication(fixture):
    # Given valid registry bytes above receipt-oriented one-MiB bounds but below eight MiB.
    assert fixture.claim() == 0
    state = fixture.program.load()
    state["runtime_observation"] = "x" * (1024 * 1024 + 4096)
    fixture.program.save(state)
    receipt = fixture.receipt()
    state = fixture.program.load()
    fixture.program.submit(state, fp.argparse.Namespace(id="S01", owner="worker", receipt=receipt))
    # When the actual host publishes the prepared ordinary submission through all guards.
    fixture.program.save(state)
    # Then the existing bounded registry capacity and ordinary review transition remain intact.
    current = fixture.program.load()
    assert current["runtime_observation"] == state["runtime_observation"]
    assert fixture.program.task(current, "S01")["status"] == "review"


def test_registry_publication_guards_reject_one_byte_over_inherited_limit(fixture):
    # Given a syntactically valid registry one byte over the inherited eight-MiB capacity.
    state = fixture.program.load()
    encoded = fp.canonical(state)
    oversized = encoded + b" " * (8 * 1024 * 1024 + 1 - len(encoded))
    fixture.program.path("backlog.json").write_bytes(oversized)
    # When the actual provider publication guard reads the original registry.
    with pytest.raises(fp.ProgramError, match="bounded read"):
        fp.authoring.delivery_publication_guard(fixture.program, state)
    # Then bounded rejection leaves the original bytes unchanged rather than bypassing the read.
    assert fixture.program.path("backlog.json").read_bytes() == oversized


def manifest_request(fixture):
    import feature_program_inputs as inputs

    manifest = {
        "schema_version": 1,
        "checks": ["scoped_tests"],
        "groups": {
            "source": [{"path": "src/S01.py", "kind": "file", "required": True}],
            "test": [],
            "config": [],
        },
        "artifact_refs": [],
        "completeness": {
            "status": "complete",
            "reason": "Fixture complete scope",
            "unknowns": [],
            "evidence_refs": [],
        },
    }
    environment = {"facts": {"runtime": "fixture-runtime"}, "unknowns": []}
    method = {"procedure": "Fixture procedure", "execution_kind": "manual", "version": "1"}
    captured = inputs.capture_inputs(
        fixture.program, manifest, environment=environment, method=method
    )
    return request(
        fixture,
        check_ids=["scoped_tests"],
        input_manifest=manifest,
        environment_identity=environment,
        method_identity=method,
    ), captured


def test_manifest_check_identity_survives_unrelated_owned_change_but_task_context_is_stale(fixture):
    # Given a complete scoped check with a wider owned write boundary.
    assert fixture.claim() == 0
    state = fixture.program.load()
    state["tasks"][0]["attempts"][0]["scope"] = ["src/S01.py", "src/S02.py"]
    fixture.program.path("backlog.json").write_bytes(fp.canonical(state))
    value, captured = manifest_request(fixture)
    record(fixture, value)
    before_path = fixture.write("evidence/S01/attempt-1/before.json", captured)
    capture_ref = {
        "path": before_path,
        "sha256": fp.sha(fixture.program.path(before_path).read_bytes()),
    }
    record(
        fixture,
        observation(
            fixture,
            method=captured["method"],
            input_before=captured["input_digest"],
            input_after=captured["input_digest"],
            input_capture_before=capture_ref,
            input_capture_after=capture_ref,
        ),
    )
    (fixture.program.repo / "src/S02.py").write_bytes(b"unrelated-owned-change")
    # When current facts are assessed.
    lane = verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"][
        "lanes"
    ]["items"][0]
    # Then input precision never disguises changed submitted/owned task bytes.
    assert lane["applicability"] == "stale"
    assert lane["input_summary"]["status"] == "unchanged"


def test_manifest_observation_requires_actual_complete_before_after_capture_refs(fixture):
    # Given a complete declared request but only a copied digest claim.
    assert fixture.claim() == 0
    value, captured = manifest_request(fixture)
    event = record(fixture, value)
    latest = record(
        fixture,
        observation(
            fixture,
            method=captured["method"],
            input_before=captured["input_digest"],
            input_after=captured["input_digest"],
        ),
    )
    # When claimed pass is assessed for ordinary acceptance.
    problems = verification._observation_problems(latest, event)
    # Then missing actual execution captures remain nonpassing.
    assert problems
    assert (
        verification.submission_inputs(
            fixture.program, fixture.program.load(), submission(fixture)
        )["receipt"]["acceptance_complete"]
        is False
    )


def test_full_projection_bounds_once_after_complete_assessment(fixture):
    # Given three distinct adopted required lanes.
    assert fixture.claim() == 0
    for index in range(3):
        record(fixture, request(fixture, f"request-{index}", lane=f"lane-{index}"))
    # When the host obtains full facts and separately bounds for display.
    full = verification.projection(fixture.program, fixture.program.load(), limit=None)
    bounded = verification.bound_projection(full, limit=1)
    # Then queue consumers see the whole assessed set and display counts remain truthful.
    assert len(full["tasks"]["S01"]["lanes"]["items"]) == 3
    assert bounded["tasks"]["S01"]["lanes"]["shown"] == 1
    assert bounded["tasks"]["S01"]["lanes"]["omitted"] == 2
    assert all(
        "input_complete" in lane and "resource" in lane
        for lane in full["tasks"]["S01"]["lanes"]["items"]
    )


def test_explicit_manifest_scope_cannot_omit_declared_shared_dependency(fixture):
    # Given a manifest declaring an additional test dependency.
    assert fixture.claim() == 0
    value, _ = manifest_request(fixture)
    value["input_manifest"]["groups"]["test"] = [
        {"path": "shared.py", "kind": "file", "required": True}
    ]
    # When a request falsely retains the owned-only public scope.
    with pytest.raises(fp.ProgramError):
        record(fixture, value)
    # Then the record is rejected rather than silently trimming shared inputs.


def test_nonpassing_manifest_drift_is_retained_truthfully(fixture):
    # Given actual before capture and a source edit observed before the after capture.
    import feature_program_inputs as inputs

    assert fixture.claim() == 0
    value, before = manifest_request(fixture)
    record(fixture, value)
    (fixture.program.repo / "src/S01.py").write_bytes(b"actual-drift")
    after = inputs.capture_inputs(
        fixture.program,
        before["manifest"],
        environment=before["environment"],
        method=before["method"],
    )
    refs = []
    for label, captured in (("before", before), ("after", after)):
        name = fixture.write(f"evidence/S01/attempt-1/{label}-drift.json", captured)
        refs.append({"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())})
    # When the actual unequal captured inputs accompany a nonpassing observation.
    event = record(
        fixture,
        observation(
            fixture,
            result="unknown",
            method=before["method"],
            input_before=before["input_digest"],
            input_after=after["input_digest"],
            input_capture_before=refs[0],
            input_capture_after=refs[1],
        ),
    )
    # Then drift remains immutable recorded evidence rather than a lost or rebound result.
    assert event["input"]["input_before"] != event["input"]["input_after"]
    assert event["input"]["result"] == "unknown"
    assert (
        verification.projection(fixture.program, fixture.program.load())["tasks"]["S01"]["lanes"][
            "items"
        ][0]["applicability"]
        == "stale"
    )


def test_categorized_capture_reference_cannot_escape_target_attempt(fixture):
    # Given an otherwise complete captured request and an original capture outside task prefix.
    assert fixture.claim() == 0
    value, captured = manifest_request(fixture)
    record(fixture, value)
    name = fixture.write("evidence/_verification/attempt-1/foreign-capture.json", captured)
    ref = {"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())}
    # When a passing observation tries to use the foreign path instead of an exact task-local attachment.
    with pytest.raises(fp.ProgramError):
        record(
            fixture,
            observation(
                fixture,
                method=captured["method"],
                input_before=captured["input_digest"],
                input_after=captured["input_digest"],
                input_capture_before=ref,
                input_capture_after=ref,
            ),
        )
    # Then normal task-attempt namespace guard remains enforced.


def shared_run_fixture(fixture):
    # Synthetic actual-fact shape for guards; no product process execution is claimed.
    assert fixture.claim() == 0
    assert fixture.claim("S02", owner="worker-two") == 0
    value, captured = manifest_request(fixture)
    origin = record(fixture, value)
    target = record(
        fixture,
        {
            **value,
            "id": "target-request",
            "task_id": "S02",
            "actor_id": "worker-two",
            "expected_revision": fp.phases.revision(fixture.program.load()),
            "evidence_destination": "evidence/S02/attempt-1/",
        },
    )
    name = fixture.write("evidence/S01/attempt-1/run-inputs.json", captured)
    ref = {"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())}
    output = fixture.evidence("S01", label="run-output")
    output_ref = {
        "path": output,
        "sha256": fp.sha(fixture.program.path(output).read_bytes()),
        "bytes_seen": 1,
        "bytes_retained": 1,
        "truncated": False,
    }
    run = {
        "schema_version": 1,
        "producer": "feature_program_execution",
        "run_id": "fixture-run",
        "task_id": "S01",
        "attempt": 1,
        "request_id": origin["request_id"],
        "request_version": origin["request_version"],
        "actor_id": "qa",
        "request_receipt": origin["receipt"],
        "request_context_digest": fp.digest(origin["context"]),
        "input_capture_before": ref,
        "input_capture_after": ref,
        "method": captured["method"],
        "environment": captured["environment"],
        "process": {"outcome": "exited", "exit_code": 0, "started_at": None, "ended_at": None},
        "comparison": {"status": "unchanged"},
        "outputs": {"stdout": output_ref, "stderr": output_ref},
        "artifacts": {"requested": [], "captured": [], "missing": []},
        "completeness": {"status": "complete", "reasons": []},
        "publication": {"status": "pending"},
        "next_action": "Explicit per-task inspection",
    }
    run_name = fixture.write("evidence/S01/attempt-1/actual-run-shape.json", run)
    run_ref = {"path": run_name, "sha256": fp.sha(fixture.program.path(run_name).read_bytes())}
    return captured, target, run_ref


def test_explicit_compatible_task_attachment_is_advisory_and_task_guarded(fixture):
    # Given one frozen actual-fact capture and separately authorized compatible task requests.
    _, target, run_ref = shared_run_fixture(fixture)
    before = deepcopy(fixture.program.load())
    # When exact target applicability is inspected without copies or publication.
    result = verification.attachment_bindings(fixture.program, before, ["S02"], run_ref)
    # Then the candidate keeps target authority/request identity but grants no task transition.
    assert result["items"][0]["status"] == "attachment_candidate"
    assert result["items"][0]["request_id"] == target["request_id"]
    assert result["items"][0]["authority_checked"] is True
    assert fixture.program.load() == before


@pytest.mark.parametrize("change", ["authority", "source", "adverse"])
def test_shared_run_cannot_bypass_target_authority_source_or_latest_adverse(fixture, change):
    # Given exact compatible captures and a distinct current target task.
    _, target, run_ref = shared_run_fixture(fixture)
    kwargs = {}
    match change:
        case "authority":
            kwargs["actor_id"] = "unrelated"
        case "source":
            (fixture.program.repo / "src/S02.py").write_bytes(b"changed-owned-target")
        case "adverse":
            captured = target["context"]["input_capture"]
            path = fixture.write("evidence/S02/attempt-1/target-capture.json", captured)
            ref = {"path": path, "sha256": fp.sha(fixture.program.path(path).read_bytes())}
            record(
                fixture,
                observation(
                    fixture,
                    "target-failed",
                    task_id="S02",
                    request_id=target["request_id"],
                    result="failed",
                    method=captured["method"],
                    input_before=captured["input_digest"],
                    input_after=captured["input_digest"],
                    input_capture_before=ref,
                    input_capture_after=ref,
                    artifacts=[fixture.evidence("S02", label="adverse")],
                ),
            )
    # When the single prior raw capture is offered to that target.
    result = verification.attachment_bindings(
        fixture.program, fixture.program.load(), ["S02"], run_ref, **kwargs
    )
    # Then exact input equality cannot silently authorize that task's acceptance.
    assert result["items"][0]["status"] == "not_assessed"


def test_primary_execution_origin_cannot_be_rebound_to_other_context(fixture):
    # Given a retained run with exact input refs but an altered original request context.
    captured, _, run_ref = shared_run_fixture(fixture)
    run = fp.read_json(fixture.program.path(run_ref["path"]))
    run["request_context_digest"] = "f" * 64
    name = fixture.write("evidence/S01/attempt-1/rebound-run.json", run)
    ref = {"path": name, "sha256": fp.sha(fixture.program.path(name).read_bytes())}
    # When the otherwise passing raw result is offered to the original task.
    with pytest.raises(fp.ProgramError):
        record(
            fixture,
            observation(
                fixture,
                method=captured["method"],
                input_before=captured["input_digest"],
                input_after=captured["input_digest"],
                input_capture_before=run["input_capture_before"],
                input_capture_after=run["input_capture_after"],
                run_capture_ref=ref,
                artifacts=[name],
            ),
        )
    # Then exact run-origin receipt/context identity is independently required.
