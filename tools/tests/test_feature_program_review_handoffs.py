"""Exact-subject handoff scenarios; native observations are synthetic fixtures only."""

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests.test_feature_program import HEAD, fp, make_small_program  # noqa: E402
import feature_program_review_handoffs as handoffs  # noqa: E402


@pytest.fixture
def submitted(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "git", lambda _repo, args: "" if args[0] == "status" else HEAD)
    fixture = make_small_program(tmp_path / "repo", "handoff-fixture", ("S01", "S02"))
    assert fixture.claim() == 0
    fixture.submit()
    state = fixture.program.load()
    state["actors"] = [
        {"id": "reviewer", "role": "reviewer", "parent_id": None},
        {"id": "replacement", "role": "reviewer", "parent_id": None},
    ]
    return fixture, state


def context(fixture, state):
    return fp.verification.packet(fixture.program, state, "S01", observed_head=HEAD)["context"]


def inputs(fixture, state, **changes):
    return {
        "task_id": "S01",
        "actor_id": "leader",
        "context": context(fixture, state),
        "reviewer_id": "reviewer",
        "eligibility": {"status": "eligible"},
        "recorded_at": "2026-10-15T01:00:00+00:00",
        "review_inputs": {},
        **changes,
    }


def intent(fixture, state, **changes):
    result = handoffs.prepare_handoff(state, inputs(fixture, state, **changes))
    fixture.write(result["packet_ref"]["path"], result["packet_document"])
    return result


def observation(fixture, state, kind="dispatch_sent", identifier="sent-1", **changes):
    item = [item for item in state["review_handoffs"]["records"] if item["kind"] == "intent"][-1]
    artifact = fixture.evidence("S01", label=identifier)
    return {
        "schema_version": 1,
        "operation": "review-handoff-record",
        "id": identifier,
        "kind": kind,
        "handoff_id": item["handoff_id"],
        "assignment_generation": item["assignment_generation"],
        "subject": item["subject"],
        "packet_ref": item["packet_ref"],
        "actor_id": "leader",
        "review_inputs": {},
        "context": context(fixture, state),
        "recorded_at": "2026-10-15T01:00:02+00:00",
        "reason": "Synthetic native observation; no actual native execution",
        "evidence_refs": [
            {"path": artifact, "sha256": fp.sha(fixture.program.path(artifact).read_bytes())}
        ],
        "dispatch_attempt_id": "dispatch-1",
        "transport": "followup_task",
        "recipient_status": "completed",
        "contact": "/synthetic/reviewer",
        "observed_at": "2026-10-15T01:00:01+00:00",
        "clock_id": "fixture-clock",
        "timestamp_source": "native_tool_observation",
        "boundary": "native_dispatch_observed",
        **changes,
    }


def project(fixture, state, **changes):
    binding = {"S01": {"context": context(fixture, state), "review_inputs": {}, **changes}}
    return handoffs.project_handoffs(state, binding)


def test_packet_and_intent_are_detached_and_reused_across_unrelated_updates(submitted):
    # Given a real ordinary submitted receipt and unrelated staffing notes.
    fixture, state = submitted
    before = deepcopy(state)
    first = intent(fixture, state)
    changed = first["state"]
    changed["tasks"][1]["next_action"] = "Unrelated preparation"
    # When the same exact submitted subject is prepared again.
    second = handoffs.prepare_handoff(changed, inputs(fixture, changed))
    # Then no extra intent, packet, round or authority is invented.
    assert state == before
    assert second["status"] == "already_recorded"
    assert fp.canonical(first["packet_document"]) == fp.canonical(second["packet_document"])
    assert first["packet_ref"] == second["packet_ref"]
    assert len(second["state"]["review_handoffs"]["records"]) == 1


def test_packet_excludes_volatile_reviewer_clock_and_transaction_revision(submitted):
    # Given one immutable source packet.
    fixture, state = submitted
    first = intent(fixture, state)
    # When the director explicitly replaces the reviewer.
    second = intent(
        fixture, first["state"], reviewer_id="replacement", recorded_at="2026-10-15T02:00:00+00:00"
    )
    # Then packet identity is reusable while the recorded assignment generation changes.
    assert first["packet_document"] == second["packet_document"]
    assert second["delta"]["record"]["assignment_generation"] == 2
    assert second["state"]["review_handoffs"]["records"][0]["reviewer_id"] == "reviewer"


@pytest.mark.parametrize("reviewer", [None, "worker", "leader", "unknown"])
def test_missing_or_conflicting_reviewer_is_not_silently_eligible(submitted, reviewer):
    # Given an unassigned, implementing, directing or unregistered reviewer.
    fixture, state = submitted
    # When an asserted eligibility claim is inspected.
    result = intent(fixture, state, reviewer_id=reviewer)
    # Then the task stays reviewable but native dispatch prerequisites remain explicit.
    assert result["missing_fields"]
    assert result["delta"]["record"]["eligibility"]["status"] == "not_assessed"


@pytest.mark.parametrize("change", ["source", "round", "definition"])
def test_changed_subject_cannot_reuse_previous_packet(submitted, change):
    # Given an existing actual submission packet.
    fixture, state = submitted
    result = intent(fixture, state)
    stale = inputs(fixture, result["state"])
    match change:
        case "source":
            stale["context"]["source_digest"] = "f" * 64
        case "round":
            stale["context"]["submission"]["round"] += 1
        case "definition":
            stale["context"]["definition_digest"] = "e" * 64
    # When stale context is used to create another handoff.
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_handoff(result["state"], stale)
    # Then no old subject is silently rebound.


def test_sent_ack_and_start_are_distinct_actual_facts(submitted):
    # Given a current independent handoff.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state = handoffs.prepare_observation(state, observation(fixture, state))["state"]
    sent = project(fixture, state)["rows"]["items"][0]
    ack = observation(
        fixture,
        state,
        "reviewer_acknowledged",
        "ack-1",
        actor_id="reviewer",
        timestamp_source="reviewer_observation",
        boundary="reviewer_acknowledged",
    )
    state = handoffs.prepare_observation(state, ack)["state"]
    acknowledged = project(fixture, state)["rows"]["items"][0]
    start = observation(
        fixture,
        state,
        "review_started",
        "start-1",
        actor_id="reviewer",
        timestamp_source="reviewer_observation",
        boundary="review_started",
    )
    # When the reviewer actually reports starting the exact packet.
    state = handoffs.prepare_observation(state, start)["state"]
    # Then transport, acknowledgement and start remain separately retained.
    started = project(fixture, state)["rows"]["items"][0]
    assert (sent["phase"], acknowledged["phase"], started["phase"]) == (
        "sent",
        "acknowledged",
        "started",
    )
    assert len(started["observations"]) == 4
    assert started["native_liveness"] == started["runtime_capacity"] == "unknown"


@pytest.mark.parametrize(
    "recipient_status,transport",
    [
        ("idle", "send_message"),
        ("completed", "send_message"),
        ("running", "followup_task"),
    ],
)
def test_wrong_transport_does_not_claim_a_native_wake(submitted, recipient_status, transport):
    # Given a recorded actual recipient status and the wrong native operation.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    actual = observation(fixture, state, recipient_status=recipient_status, transport=transport)
    # When that alleged send outcome is recorded.
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_observation(state, actual)
    # Then send-only delivery cannot masquerade as a completed reviewer wake.


def test_running_reviewer_transport_preserves_actual_contact(submitted):
    # Given an already-running native reviewer.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    # When root records actual send_message delivery rather than a new turn.
    result = handoffs.prepare_observation(
        state, observation(fixture, state, recipient_status="running", transport="send_message")
    )
    # Then exact transport/contact remains attributable, not ACK/start.
    row = project(fixture, result["state"])["rows"]["items"][0]
    assert row["phase"] == "sent"
    assert row["observations"][-1]["transport"] == "send_message"
    assert row["observations"][-1]["contact"] == "/synthetic/reviewer"


def test_unknown_delivery_requires_actual_reconciliation_before_retry(submitted):
    # Given an ambiguous external native delivery.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    unknown = observation(fixture, state, "dispatch_unknown", "unknown-1")
    state = handoffs.prepare_observation(state, unknown)["state"]
    # When a different dispatch is attempted without actual reconciliation.
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_observation(
            state,
            observation(fixture, state, identifier="retry-1", dispatch_attempt_id="dispatch-2"),
        )
    # Then uncertainty is not automatically cleared or resent.
    assert project(fixture, state)["rows"]["items"][0]["phase"] == "delivery_unknown"


def test_reconciliation_retains_original_unknown_and_exact_attempt(submitted):
    # Given an unknown outcome followed by actual runtime reconciliation.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state = handoffs.prepare_observation(
        state, observation(fixture, state, "dispatch_unknown", "unknown-1")
    )["state"]
    # When root records the actually observed delivered outcome for that same attempt.
    state = handoffs.prepare_observation(
        state, observation(fixture, state, identifier="reconciled-1", reconciles="unknown-1")
    )["state"]
    # Then history preserves uncertainty and reconciliation without a second send.
    row = project(fixture, state)["rows"]["items"][0]
    assert row["phase"] == "sent"
    assert [event["kind"] for event in row["observations"]] == [
        "ready_recorded",
        "dispatch_unknown",
        "dispatch_sent",
    ]


def test_exact_observation_retry_is_idempotent_after_registry_progress(submitted):
    # Given a committed exact observation and unrelated registry progress.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    actual = observation(fixture, state)
    state = handoffs.prepare_observation(state, actual)["state"]
    state["tasks"][1]["next_action"] = "Unrelated work"
    # When the same immutable observation is retried.
    result = handoffs.prepare_observation(state, actual)
    # Then no new record or native action is created.
    assert result["status"] == "already_recorded"
    assert len(result["state"]["review_handoffs"]["records"]) == 2


@pytest.mark.parametrize("change", ["actor", "packet", "dispatch", "clock"])
def test_wrong_actor_subject_attempt_and_recorded_clock_cannot_supply_start(submitted, change):
    # Given a sent exact review packet.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state = handoffs.prepare_observation(state, observation(fixture, state))["state"]
    actual = observation(fixture, state, "review_started", "start-1", actor_id="reviewer")
    match change:
        case "actor":
            actual["actor_id"] = "intruder"
        case "packet":
            actual["packet_ref"] = {**actual["packet_ref"], "sha256": "e" * 64}
        case "dispatch":
            actual["dispatch_attempt_id"] = "wrong-dispatch"
        case "clock":
            actual["timestamp_source"] = "preparation_clock"
    # When the inconsistent start is recorded.
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_observation(state, actual)
    # Then actual source/report attribution is not guessed.


def test_history_tampering_and_prefix_rewrite_are_rejected(submitted):
    # Given retained handoff history.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    changed = deepcopy(state)
    changed["review_handoffs"]["records"][0]["reviewer_id"] = "intruder"
    # When malformed retained bytes are inspected.
    with pytest.raises(fp.ProgramError):
        handoffs.validate_append(state, changed)
    # Then display bounds cannot hide integrity failures.


def test_missing_actual_timestamps_stay_unknown_and_projection_is_repeatable(submitted):
    # Given a real retained outcome without actual observed endpoints.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    actual = observation(fixture, state, observed_at=None, clock_id=None)
    state = handoffs.prepare_observation(state, actual)["state"]
    before = deepcopy(state)
    # When the same retained facts are projected twice.
    first, second = project(fixture, state), project(fixture, state)
    # Then no read clock, liveness or elapsed age is manufactured.
    assert first == second and state == before
    assert first["rows"]["items"][0]["observations"][-1]["observed_at"] is None


def test_recorded_verdict_is_terminal_without_fabricating_observed_endpoint(submitted):
    # Given a canonically associated verdict without a native terminal-time observation.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    round_record = state["tasks"][0]["attempts"][-1]["rounds"][-1]
    review = fixture.review()
    round_record["review"] = {
        "path": review,
        "sha256": fp.sha(fixture.program.path(review).read_bytes()),
    }
    # When handoff facts are projected.
    row = project(fixture, state)["rows"]["items"][0]
    # Then canonical terminal association is separate from measured review duration.
    assert row["canonical_terminal"] is True and row["phase"] == "terminal"
    assert not any(event["kind"] == "terminal" for event in row["observations"])


def test_complete_counts_precede_display_bound(submitted):
    # Given distinct explicitly declared broader review input identities.
    fixture, state = submitted
    for index in range(53):
        state = intent(fixture, state, review_inputs={"declared": index})["state"]
    # When the complete current/historical set is bounded once.
    facts = {"S01": {"context": context(fixture, state), "review_inputs": {"declared": 52}}}
    result = handoffs.project_handoffs(state, facts, limit=50)
    # Then omitted records remain in complete currentness counts.
    assert (result["rows"]["total"], result["rows"]["shown"], result["rows"]["omitted"]) == (
        53,
        50,
        3,
    )
    assert result["counts"]["currentness"] == {"stale": 52, "current": 1}


def test_retained_submit_boundary_is_not_rewritten_as_preparation_time(submitted):
    # Given actual successful submit output retained during the next meaningful dispatch record.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    actual = observation(fixture, state)
    actual["submitted_observation"] = {
        "kind": "submitted",
        "subject": actual["subject"],
        "reference": actual["evidence_refs"][0],
        "observed_at": "2026-10-15T01:00:00+00:00",
        "clock_id": "fixture-clock",
        "timestamp_source": "live_postcondition_observation",
        "boundary": "canonical_submission_observed",
    }
    # When actual dispatch publication retains the original result observation.
    state = handoffs.prepare_observation(state, actual)["state"]
    # Then the original live boundary survives without a timestamp-only write.
    row = project(fixture, state)["rows"]["items"][0]
    retained = [event for event in row["observations"] if event["kind"] == "submitted"]
    assert len(retained) == 1 and retained[0]["observed_at"] == "2026-10-15T01:00:00+00:00"
    assert len(state["review_handoffs"]["records"]) == 2


def test_loaded_round_and_submission_keep_same_current_packet_link(submitted):
    # Given a loaded registry whose current round and submission are separate objects.
    fixture, state = submitted
    state = fp.json.loads(fp.json.dumps(state))
    assert (
        state["tasks"][0]["attempts"][-1]["submission"]
        is not state["tasks"][0]["attempts"][-1]["rounds"][-1]
    )
    # When an explicit handoff is adopted.
    result = intent(fixture, state)
    attempt = result["state"]["tasks"][0]["attempts"][-1]
    # Then ordinary round validation sees the same links without changing submitted proof.
    for key in ("handoff_id", "packet_ref"):
        assert attempt["submission"][key] == attempt["rounds"][-1][key]
    assert (
        attempt["submission"]["receipt"]
        == state["tasks"][0]["attempts"][-1]["submission"]["receipt"]
    )


def test_sent_attempt_blocks_duplicate_native_send_and_failed_attempt_allows_explicit_retry(
    submitted,
):
    # Given a sent native attempt with no actual process terminal.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    sent = handoffs.prepare_observation(state, observation(fixture, state))["state"]
    # When another native send is alleged without reconciling the first process.
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_observation(
            sent, observation(fixture, sent, identifier="sent-2", dispatch_attempt_id="dispatch-2")
        )
    # Then only an actual failed delivery permits a separately identified explicit retry.
    failed = handoffs.prepare_observation(
        state, observation(fixture, state, "dispatch_failed", "failed-1")
    )["state"]
    retried = handoffs.prepare_observation(
        failed, observation(fixture, failed, identifier="retry-1", dispatch_attempt_id="dispatch-2")
    )
    assert project(fixture, retried["state"])["rows"]["items"][0]["phase"] == "sent"


def test_actual_cancellation_closes_exact_process_without_accepting_submission(submitted):
    # Given an actually sent review process.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state = handoffs.prepare_observation(state, observation(fixture, state))["state"]
    # When the designated reviewer reports its actual cancellation.
    actual = observation(
        fixture,
        state,
        "terminal",
        "canceled-1",
        actor_id="reviewer",
        terminal_kind="canceled",
        outcome="canceled",
    )
    state = handoffs.prepare_observation(state, actual)["state"]
    row = project(fixture, state)["rows"]["items"][0]
    # Then no canonical verdict is invented, and that process cannot start again.
    assert row["phase"] == "terminal" and row["canonical_terminal"] is False
    assert state["tasks"][0]["status"] == "review"
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_observation(
            state, observation(fixture, state, "review_started", "late-start", actor_id="reviewer")
        )
    retried = handoffs.prepare_observation(
        state, observation(fixture, state, identifier="retry-1", dispatch_attempt_id="dispatch-2")
    )
    assert project(fixture, retried["state"])["rows"]["items"][0]["phase"] == "sent"


@pytest.mark.parametrize("change", ["wrong_attempt", "no_kind", "wrong_actor"])
def test_terminal_cannot_fake_a_different_review_process(submitted, change):
    # Given exact dispatched subject and one actual terminal claim.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state = handoffs.prepare_observation(state, observation(fixture, state))["state"]
    actual = observation(
        fixture, state, "terminal", "finished-1", actor_id="reviewer", terminal_kind="finished"
    )
    if change == "wrong_attempt":
        actual["dispatch_attempt_id"] = "unseen"
    elif change == "no_kind":
        actual.pop("terminal_kind")
    else:
        actual["actor_id"] = "leader"
    # When its attribution/process binding is validated.
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_observation(state, actual)
    # Then ordinary verdicts and actual process completion remain independent.


def test_canonical_verdict_observation_requires_exact_review_receipt(submitted):
    # Given a real typed ordinary review receipt and its exact retained round association.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    review = fixture.review()
    ref = {"path": review, "sha256": fp.sha(fixture.program.path(review).read_bytes())}
    state["tasks"][0]["attempts"][-1]["rounds"][-1]["review"] = ref
    # When the director retains an actual observation of that verdict.
    actual = observation(
        fixture,
        state,
        "terminal",
        "verdict-1",
        terminal_kind="canonical_verdict_observed",
        terminal_ref=ref,
    )
    result = handoffs.prepare_observation(state, actual)
    # Then the exact verdict is checked and participates in host frozen-reference checks.
    assert ref in handoffs.evidence_references(result["state"])
    actual["id"] = "bad-verdict"
    actual["terminal_ref"] = {**ref, "sha256": "b" * 64}
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_observation(state, actual)


@pytest.mark.parametrize("change", ["retired", "terminal", "replaced_subject", "stale_revision"])
def test_fresh_dispatch_rechecks_current_recorded_prerequisites(submitted, change):
    # Given a prepared packet before recorded prerequisites changed.
    fixture, state = submitted
    first = intent(fixture, state)
    state = first["state"]
    actual = observation(fixture, state)
    if change == "retired":
        state["actors"][0]["staffing_status"] = "retired"
    elif change == "terminal":
        review = fixture.review()
        state["tasks"][0]["attempts"][-1]["rounds"][-1]["review"] = {
            "path": review,
            "sha256": fp.sha(fixture.program.path(review).read_bytes()),
        }
    elif change == "replaced_subject":
        state = intent(fixture, state, review_inputs={"extra": "declared"})["state"]
    else:
        actual["expected_revision"] = "0" * 64
    # When the old native dispatch claim is prepared as a new record.
    with pytest.raises(fp.ProgramError):
        handoffs.prepare_observation(state, actual)
    # Then no readiness or transport result is promoted across that change.


def test_superseded_started_review_remains_resource_uncertainty(submitted):
    # Given an actually started review and later explicit reviewer reassignment.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state = handoffs.prepare_observation(state, observation(fixture, state))["state"]
    state = handoffs.prepare_observation(
        state, observation(fixture, state, "review_started", "start-1", actor_id="reviewer")
    )["state"]
    state = intent(fixture, state, reviewer_id="replacement")["state"]
    # When both retained assignments are projected.
    rows = project(fixture, state)["rows"]["items"]
    # Then the historical process has not become idle just because assignment changed.
    assert rows[0]["currentness"] == "stale"
    assert rows[0]["superseded_running_uncertainty"] is True
    assert rows[0]["recorded_sequence"] < rows[1]["recorded_sequence"]
    assert rows[1]["phase"] == "ready"


def test_safe_projection_reasons_do_not_emit_raw_context_errors(submitted):
    # Given stale authoritative context containing a private identity.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    stale = context(fixture, state)
    stale["definition_digest"] = "e" * 64
    # When safe handoff facts are requested.
    row = handoffs.project_handoffs(state, {"S01": {"context": stale}})["rows"]["items"][0]
    # Then only the finite reason code reaches the default web DTO.
    assert row["reasons"] == ["handoff_subject_changed"]
    assert row["currentness"] == "stale"


@pytest.mark.parametrize("change", ["subject_id", "packet_path", "extra_fields"])
def test_rehashed_malformed_retained_records_are_rejected(submitted, change):
    # Given retained records whose semantic corruption is hidden behind a recomputed hash.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    record = state["review_handoffs"]["records"][0]
    if change == "subject_id":
        record["handoff_id"] = "rh-" + "e" * 40
    elif change == "packet_path":
        record["packet_ref"]["path"] = "evidence/S02/attempt-1/foreign.json"
    else:
        record["invented_state"] = "running"
    record["digest"] = fp.digest({k: v for k, v in record.items() if k != "digest"})
    # When complete integrity is checked before rows are bounded.
    with pytest.raises(fp.ProgramError):
        handoffs.project_handoffs(state, {}, limit=0)
    # Then a valid hash alone never supplies historical semantics.


def test_rehashed_ack_without_sent_ancestor_cannot_be_loaded(submitted):
    # Given fabricated activity whose record digest has been recomputed.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state = handoffs.prepare_observation(state, observation(fixture, state))["state"]
    record = state["review_handoffs"]["records"][-1]
    record["kind"] = "reviewer_acknowledged"
    record["actor_id"] = "reviewer"
    record["digest"] = fp.digest({key: value for key, value in record.items() if key != "digest"})
    # When a full-history read occurs.
    with pytest.raises(fp.ProgramError):
        handoffs.validate(state)
    # Then an alleged ACK is rejected without an actual retained sent ancestor.


def test_ordinary_verdict_does_not_close_superseded_native_process(submitted):
    # Given started work superseded by a later reviewer assignment.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state = handoffs.prepare_observation(state, observation(fixture, state))["state"]
    state = handoffs.prepare_observation(
        state, observation(fixture, state, "review_started", "start-1", actor_id="reviewer")
    )["state"]
    old_intent = deepcopy(state["review_handoffs"]["records"][0])
    state = intent(fixture, state, reviewer_id="replacement")["state"]
    review = fixture.review()
    ref = {"path": review, "sha256": fp.sha(fixture.program.path(review).read_bytes())}
    state["tasks"][0]["attempts"][-1]["rounds"][-1]["review"] = ref
    # When the ordinary canonical verdict becomes terminal.
    row = project(fixture, state)["rows"]["items"][0]
    # Then actual resource uncertainty remains until a matching process-terminal observation.
    assert row["canonical_terminal"] is True and row["superseded_running_uncertainty"] is True
    actual = observation(
        fixture,
        state,
        "terminal",
        "old-finished",
        actor_id="reviewer",
        terminal_kind="finished",
        assignment_generation=1,
        subject=old_intent["subject"],
        packet_ref=old_intent["packet_ref"],
    )
    finished = handoffs.prepare_observation(state, actual)["state"]
    assert project(fixture, finished)["rows"]["items"][0]["superseded_running_uncertainty"] is False


def test_projected_readiness_rechecks_recorded_reviewer_retirement(submitted):
    # Given a formerly eligible reviewer who is now explicitly retired.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    state["actors"][0]["staffing_status"] = "retired"
    # When safe current responsibility is projected without any runtime probe.
    row = project(fixture, state)["rows"]["items"][0]
    # Then current dispatch readiness is withdrawn while original assignment stays retained.
    assert row["phase"] == "unassigned" and row["currentness"] == "stale"
    assert row["eligibility"]["status"] == "not_assessed"
    assert row["reasons"] == ["reviewer_eligibility_changed"]
    assert state["review_handoffs"]["records"][0]["eligibility"]["status"] == "eligible"
    assert row["native_liveness"] == "unknown"


def _rechain(records):
    previous = None
    for item in records:
        item["previous_digest"] = previous
        item["digest"] = fp.digest({key: value for key, value in item.items() if key != "digest"})
        previous = item["digest"]


@pytest.mark.parametrize("limit", [0, 50])
def test_full_handoff_bound_preserves_all_history_and_detaches_nested_facts(submitted, limit):
    # Given more assignments than the display bound, all retained against one exact subject.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    first = state["review_handoffs"]["records"][0]
    records = [deepcopy(first) for _ in range(55)]
    for generation, item in enumerate(records, 1):
        item["assignment_generation"] = generation
        item["id"] = item["handoff_id"] + f"-assignment-{generation}"
    _rechain(records)
    state["review_handoffs"]["records"] = records
    full = project(fixture, state)
    original = deepcopy(full)
    # When the already assessed full value is bounded for a separate consumer.
    bounded = handoffs.bound_projection(full, limit=limit)
    # Then all population counts/order/reference metadata agree without shared mutable facts.
    assert bounded == handoffs.project_handoffs(
        state, {"S01": {"context": context(fixture, state), "review_inputs": {}}}, limit=limit
    )
    assert bounded["rows"]["total"] == 55
    assert bounded["rows"]["shown"] == limit
    assert full == original
    bounded["counts"]["phase"]["ready"] = 999
    if limit:
        bounded["rows"]["items"][0]["subject"]["attempt"] = 999
        bounded["rows"]["items"][0]["observations"][0]["subject"]["attempt"] = 999
    assert full == original


def test_handoff_bound_rejects_previously_truncated_population(submitted):
    # Given an assessed envelope whose underlying population has already been clipped.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    partial = handoffs.project_handoffs(state, {}, limit=0)
    # When a consumer tries to treat it as the complete shared facts.
    with pytest.raises(fp.ProgramError):
        handoffs.bound_projection(partial)
    # Then missing historical rows cannot silently become a complete empty population.


@pytest.mark.parametrize("barrier", ["terminal", "reconciliation"])
def test_later_history_cannot_legitimize_an_earlier_native_resend(submitted, barrier):
    # Given a valid exact chronology with a completed or reconciled first dispatch.
    fixture, state = submitted
    state = intent(fixture, state)["state"]
    if barrier == "terminal":
        state = handoffs.prepare_observation(state, observation(fixture, state))["state"]
        state = handoffs.prepare_observation(
            state,
            observation(
                fixture, state, "terminal", "done-1", actor_id="reviewer", terminal_kind="finished"
            ),
        )["state"]
    else:
        state = handoffs.prepare_observation(
            state, observation(fixture, state, "dispatch_unknown", "unknown-1")
        )["state"]
        state = handoffs.prepare_observation(
            state,
            observation(fixture, state, "dispatch_failed", "failed-1", reconciles="unknown-1"),
        )["state"]
    state = handoffs.prepare_observation(
        state, observation(fixture, state, identifier="retry-2", dispatch_attempt_id="dispatch-2")
    )["state"]
    records = state["review_handoffs"]["records"]
    records[-2], records[-1] = records[-1], records[-2]
    _rechain(records)
    # When the barrier now occurs after the attempted resend, despite a valid hash chain.
    with pytest.raises(fp.ProgramError):
        handoffs.project_handoffs(state, {}, limit=0)
    # Then a later terminal or reconciliation never supplies authority for an earlier row.
