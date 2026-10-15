"""Deterministic behavioral coverage for retained timing facts."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import feature_program_timing as timing  # noqa: E402


def endpoint(identifier, kind, seconds, *, dispatch="send-1", **changes):
    return {
        "id": identifier,
        "kind": kind,
        "dispatch_attempt_id": dispatch,
        "observed_at": f"2026-10-15T00:00:{seconds:02d}+00:00",
        "clock_id": "observed-clock",
        "timestamp_source": "native_tool_observation",
        "boundary": kind,
        "reference": {"path": f"evidence/{identifier}.json", "sha256": "a" * 64},
        **changes,
    }


def subject(task="A", attempt=1, round_number=1):
    return {
        "program_id": "timing-fixture",
        "task_id": task,
        "attempt": attempt,
        "submission_round": round_number,
        "receipt_sha256": "b" * 64,
        "source_digest": "c" * 64,
    }


def handoff(*observations, task="A", attempt=1, round_number=1, **changes):
    return {
        "id": f"handoff-{task}-{attempt}-{round_number}:1",
        "handoff_id": f"handoff-{task}-{attempt}-{round_number}",
        "assignment_generation": 1,
        "task_id": task,
        "attempt": attempt,
        "submission_round": round_number,
        "subject": subject(task, attempt, round_number),
        "currentness": "current",
        "canonical_terminal": False,
        "phase": "ready",
        "reviewer_id": "reviewer",
        "next_actor_id": "reviewer",
        "next_trigger": "Actual review",
        "reasons": [],
        "observations": list(observations),
        "observations_total": len(observations),
        "reference": {
            "path": "backlog.json",
            "pointer": "/review_handoffs/records/0",
            "revision": "volatile",
        },
        **changes,
    }


def state(*tasks):
    return {
        "program_id": "timing-fixture",
        "tasks": [
            {
                "id": task,
                "status": "review",
                "owner": "worker",
                "attempts": [
                    {
                        "number": 1,
                        "rounds": [
                            {
                                "round": 1,
                                "source_digest": "c" * 64,
                                "receipt": {"path": "evidence/receipt.json", "sha256": "b" * 64},
                                "review": None,
                            }
                        ],
                    }
                ],
            }
            for task in (tasks or ("A",))
        ],
        "events": [],
    }


def project(*rows, registry=None, limit=50, verification=None, captures=None):
    return timing.project(
        registry or state(),
        handoff_facts={
            "schema_version": 1,
            "rows": {"items": list(rows), "total": len(rows), "shown": len(rows), "omitted": 0},
        },
        verification_facts={"schema_version": 1, "records": verification or []},
        capture_facts=captures or [],
        source_binding={"program_id": "timing-fixture"},
        max_rows=limit,
    )


def interval(result, kind, *, handoff_id=None):
    return next(
        row
        for row in result["intervals"]["items"]
        if row["kind"] == kind and (handoff_id is None or row["handoff_id"] == handoff_id)
    )


def timeline(**changes):
    return handoff(
        endpoint("submit", "submitted", 0),
        endpoint("sent", "dispatch_sent", 3),
        endpoint("ack", "reviewer_acknowledged", 5),
        endpoint("start", "review_started", 8),
        endpoint("finish", "terminal", 20, outcome="completed"),
        **changes,
    )


def test_direct_and_leg_durations_use_independent_observed_endpoints():
    # Given a complete observed native timeline.
    row = timeline(canonical_terminal=True, phase="terminal")
    # When timing projects the retained facts.
    result = project(row)
    # Then each metric uses its own endpoints without summing overlapping spans.
    expected = {
        "submission_to_dispatch": 3,
        "dispatch_to_ack": 2,
        "ack_to_start": 3,
        "submission_to_start": 8,
        "start_to_terminal": 12,
        "submission_to_terminal": 20,
    }
    assert {kind: interval(result, kind)["closed_seconds"] for kind in expected} == expected


def test_direct_wait_survives_missing_dispatch_and_ack():
    # Given direct submission/start observations without intermediate milestones.
    row = handoff(endpoint("submit", "submitted", 0), endpoint("start", "review_started", 8))
    # When the retained timeline is projected.
    result = project(row)
    # Then direct wait is measured but missing legs remain unknown, never zero.
    assert interval(result, "submission_to_start")["closed_seconds"] == 8
    assert interval(result, "dispatch_to_ack")["closed_seconds"] is None


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"clock_id": "other"}, "mixed_clocks"),
        ({"observed_at": "2026-10-15T00:00:08"}, "naive_timestamp"),
        ({"observed_at": "2026-10-14T23:59:59+00:00"}, "inverted_endpoints"),
        ({"observed_at": None}, "missing_observed_at"),
    ],
)
def test_incompatible_endpoints_remain_unknown(change, reason):
    # Given one unusable actual start endpoint.
    row = handoff(
        endpoint("submit", "submitted", 0), endpoint("start", "review_started", 8, **change)
    )
    # When timing is projected.
    measured = interval(project(row), "submission_to_start")
    # Then the interval cannot claim a finite duration.
    assert measured["closed_seconds"] is None
    assert reason in measured["reasons"]


def test_equal_instants_with_different_timezone_offsets_are_true_zero():
    # Given two real equal instants written with different offsets.
    row = handoff(
        endpoint("submit", "submitted", 0),
        endpoint("start", "review_started", 8, observed_at="2026-10-15T09:00:00+09:00"),
    )
    # When the direct interval is computed.
    measured = interval(project(row), "submission_to_start")
    # Then true zero is retained from actual endpoints.
    assert measured["closed_seconds"] == 0
    assert measured["state"] == "closed"


def test_canonical_terminal_without_finish_time_does_not_look_running():
    # Given canonical completion but no actual terminal observation.
    row = handoff(
        endpoint("submit", "submitted", 0),
        endpoint("start", "review_started", 8),
        canonical_terminal=True,
        phase="terminal",
    )
    # When the actual span is projected.
    measured = interval(project(row), "start_to_terminal")
    # Then canonical completion does not invent finish time or running activity.
    assert measured["state"] == "canonical_terminal_timing_unknown"
    assert measured["closed_seconds"] is None
    assert measured["age"] is None


def test_open_interval_identity_survives_close_and_unrelated_registry_changes():
    # Given retained start and then an actual terminal observation.
    opened = handoff(endpoint("submit", "submitted", 0), endpoint("start", "review_started", 8))
    closed = deepcopy(opened)
    closed["observations"].append(endpoint("finish", "terminal", 20))
    closed["observations_total"] += 1
    original = state()
    changed = deepcopy(original)
    changed.update({"generated_at": "different", "notes": ["unrelated"]})
    # When both lifecycle states and the unchanged relevant facts are projected.
    first, second, unchanged = (
        project(opened, registry=original),
        project(closed),
        project(opened, registry=changed),
    )
    # Then close preserves identity, while unrelated inputs preserve all timing bytes.
    assert (
        interval(first, "start_to_terminal")["interval_id"]
        == interval(second, "start_to_terminal")["interval_id"]
    )
    assert first == unchanged
    assert original == state()


def test_complete_statistics_and_unknown_denominators_precede_bounds():
    # Given more relevant rows than the display limit, including unavailable observations.
    names = [f"T{number:03d}" for number in range(60)]
    rows = [timeline(task=name) for name in names]
    rows[-1]["observations"] = []
    rows[-1]["observations_total"] = 0
    # When display bounds differ for the same complete population.
    small, full = (
        project(*rows, registry=state(*names), limit=1),
        project(*rows, registry=state(*names), limit=1000),
    )
    # Then summaries retain the full denominator and omitted uncertainty.
    assert small["cohorts"] == full["cohorts"]
    summary = next(
        item
        for item in small["cohorts"][0]["summaries"]
        if item["kind"] == "submission_to_start" and item["outcome"] == "all_observed"
    )
    assert summary["relevant_total"] == 60
    assert summary["closed_valid"] == 59
    assert summary["unknown"] == 1
    assert small["intervals"]["omitted"] == small["intervals"]["total"] - 1


def test_capture_reuse_counts_one_original_process_sample():
    # Given the same original sealed process attached through several receipts.
    capture = {
        "run_id": "run-1",
        "task_id": "A",
        "attempt": 1,
        "request_id": "request-1",
        "process": {
            "started_at": "2026-10-15T00:00:00Z",
            "ended_at": "2026-10-15T00:00:04Z",
            "clock_id": "runner-utc",
            "elapsed_seconds": 4.25,
            "outcome": "exited",
            "exit_code": 0,
        },
    }
    fact = {"capture": capture, "reference": {"path": "evidence/capture.json", "sha256": "d" * 64}}
    # When all original references are projected.
    result = project(captures=[fact, deepcopy(fact), deepcopy(fact)])
    # Then the original process is counted once with its finite monotonic duration.
    processes = [row for row in result["intervals"]["items"] if row["kind"] == "command_process"]
    assert len(processes) == 1
    assert processes[0]["closed_seconds"] == 4.25


def test_legacy_missing_native_history_remains_unknown_and_never_zero():
    # Given a retained canonical round without native handoff history.
    registry = state()
    # When timing projects only those legacy facts.
    result = project(registry=registry)
    # Then native review wait is unavailable while the original registry stays intact.
    assert interval(result, "submission_to_start")["closed_seconds"] is None
    assert result["coverage"]["unknown_total"] > 0
    assert json.loads(json.dumps(registry)) == state()


def verification_event(identifier, kind, seconds, *, request="request-1", **changes):
    value = endpoint(
        identifier,
        kind,
        seconds,
        dispatch=None,
        task_id="A",
        lane="tests",
        verifier_id="qa",
        next_trigger="Observe actual execution",
        **changes,
    )
    return {
        "request_id": request,
        "request_version": 1,
        "input": value,
        "context": {
            "program_id": "timing-fixture",
            "task_id": "A",
            "attempt": 1,
            "submission": None,
        },
        "receipt": value["reference"],
    }


def test_dispatch_retry_retains_failed_leg_without_duplicate_submission_samples():
    # Given a failed trial followed by an explicitly distinct successful dispatch.
    row = handoff(
        endpoint("submit", "submitted", 0, dispatch=None),
        endpoint("failed", "dispatch_failed", 2, dispatch="first"),
        endpoint("sent", "dispatch_sent", 3, dispatch="retry"),
        endpoint("ack", "reviewer_acknowledged", 5, dispatch="retry"),
        endpoint("start", "review_started", 8, dispatch="retry"),
        phase="started",
    )
    # When complete retry history is projected.
    result = project(row)
    # Then distinct trials remain but the direct submission wait has one sample.
    direct = [
        item for item in result["intervals"]["items"] if item["kind"] == "submission_to_start"
    ]
    dispatches = [
        item for item in result["intervals"]["items"] if item["kind"] == "submission_to_dispatch"
    ]
    assert len(direct) == 1 and direct[0]["closed_seconds"] == 8
    assert {item["closed_seconds"] for item in dispatches} == {2, 3}
    assert {item["outcome"] for item in dispatches} == {"failed", "unknown"}
    assert result["current"]["items"][0]["waiting_since"]["observed_at"].endswith("08+00:00")


def test_ack_from_another_dispatch_is_never_paired_with_previous_sent():
    # Given retained milestones carrying different explicit native dispatch identities.
    row = handoff(
        endpoint("submit", "submitted", 0, dispatch=None),
        endpoint("sent", "dispatch_sent", 3, dispatch="first"),
        endpoint("ack", "reviewer_acknowledged", 5, dispatch="other"),
        endpoint("start", "review_started", 8, dispatch="other"),
    )
    # When dispatch legs are assessed.
    result = project(row)
    # Then no cross-dispatch latency is invented while the actual direct wait survives.
    legs = [item for item in result["intervals"]["items"] if item["kind"] == "dispatch_to_ack"]
    assert all(item["closed_seconds"] is None for item in legs)
    assert interval(result, "submission_to_start")["closed_seconds"] == 8


def test_new_round_cannot_reuse_old_start_and_superseded_run_stays_historical():
    # Given an outstanding old review and a newly submitted current round.
    registry = state()
    registry["tasks"][0]["attempts"][0]["rounds"].append(
        {"round": 2, "receipt": {"sha256": "e" * 64}, "source_digest": "f" * 64}
    )
    old = handoff(
        endpoint("submit-old", "submitted", 0),
        endpoint("start-old", "review_started", 8),
        currentness="stale",
        phase="started",
    )
    new = handoff(endpoint("submit-new", "submitted", 20), round_number=2)
    # When both exact rounds are retained.
    result = project(old, new, registry=registry)
    # Then the new round stays unstarted and the old run is not synthetically canceled.
    fresh = interval(result, "submission_to_start", handoff_id=new["handoff_id"])
    previous = interval(result, "start_to_terminal", handoff_id=old["handoff_id"])
    assert fresh["closed_seconds"] is None and fresh["state"] == "open"
    assert previous["state"] == "open" and previous["applicability"] == "stale"
    assert [item["round"] for item in result["current"]["items"]] == [2]


def test_retained_as_of_age_is_fixed_and_missing_age_stays_unavailable():
    # Given one existing compatible retained observation boundary.
    row = handoff(
        endpoint("submit", "submitted", 0, dispatch=None),
        as_of={
            "observed_at": "2026-10-15T00:00:10Z",
            "clock_id": "observed-clock",
            "reference": {"path": "evidence/as-of.json", "sha256": "1" * 64},
        },
    )
    # When the same facts are projected twice without any clock input.
    first, second, absent = (
        project(row),
        project(deepcopy(row)),
        project(handoff(endpoint("submit", "submitted", 0))),
    )
    # Then age is the fixed recorded observation, and no absent age becomes zero.
    assert first == second
    assert first["current"]["items"][0]["age"]["seconds"] == 10
    assert absent["current"]["items"][0]["age"] is None


def test_endpoint_beyond_explicit_retained_as_of_is_unknown():
    # Given an endpoint later than its explicitly supplied compatible observation envelope.
    row = handoff(
        endpoint("submit", "submitted", 0),
        endpoint("start", "review_started", 8),
        as_of={
            "observed_at": "2026-10-15T00:00:05Z",
            "clock_id": "observed-clock",
            "reference": {"path": "evidence/as-of.json", "sha256": "1" * 64},
        },
    )
    # When no browser or server clock can replace the retained envelope.
    measured = interval(project(row), "submission_to_start")
    # Then the future endpoint is not presented as a reliable duration.
    assert measured["closed_seconds"] is None
    assert "future_endpoint" in measured["reasons"]


def test_repeated_block_cycles_preserve_gross_wait_and_actual_resolver():
    # Given repeated block notices, two resolutions, then actual work start/finish.
    blocker = {
        "reason": "Native device unavailable",
        "resolver_id": "operator",
        "next_trigger": "Device ready",
    }
    records = [
        verification_event("request-1", "request", 0),
        verification_event("block-1", "blocked", 1, blocker=blocker),
        verification_event("notice", "blocked", 2, blocker=blocker),
        verification_event("resolve-1", "resolution", 3),
        verification_event("block-2", "blocked", 4, blocker=blocker),
        verification_event("resolve-2", "resolution", 6),
        verification_event("start-v", "started", 8),
        verification_event("finish-v", "finished", 20, result="passed"),
    ]
    # When the complete observed verification history is projected.
    result = project(verification=records)
    # Then blocks are distinct, repeat notices are not duplicate spans, and gross wait includes them.
    blocks = [item for item in result["intervals"]["items"] if item["kind"] == "blocked_span"]
    assert [item["closed_seconds"] for item in blocks] == [2, 2]
    assert all(
        item["resolver_id"] == "operator" and item["next_trigger"] == "Device ready"
        for item in blocks
    )
    assert interval(result, "verification_wait")["closed_seconds"] == 8
    assert interval(result, "verification_observed_span")["closed_seconds"] == 12


def test_superseding_request_does_not_erase_old_running_span():
    # Given an old request with an actual start and a newer explicitly superseding request.
    records = [
        verification_event("request-1", "request", 0),
        verification_event("start", "started", 3),
        verification_event("request-2", "request", 8, request="request-2", supersedes="request-1"),
    ]
    # When current applicability and retained observations are projected.
    result = project(verification=records)
    # Then the old running observation remains historical and outstanding, with no inferred terminal.
    old = interval(result, "verification_observed_span")
    assert old["request_id"] == "request-1"
    assert old["applicability"] == "historical" and old["state"] == "open"


def test_referenced_unavailable_capture_counts_unknown_instead_of_disappearing():
    # Given a finished verification referring to an original capture the host cannot supply.
    record = verification_event(
        "finish",
        "finished",
        8,
        result="passed",
        run_capture_ref={"path": "evidence/capture.json", "sha256": "d" * 64},
    )
    # When projection has no raw capture bytes or fabricated process endpoint.
    result = project(verification=[verification_event("request-1", "request", 0), record])
    # Then its missing process remains in the relevant denominator.
    measured = interval(result, "command_process")
    assert measured["closed_seconds"] is None
    assert measured["reasons"] == ["capture_unavailable"]


def test_recorded_history_is_qualified_and_ambiguous_submissions_stay_unknown():
    # Given exact canonical submission/verdict identities, with no native observations.
    registry = state()
    round_item = registry["tasks"][0]["attempts"][0]["rounds"][0]
    round_item["review"] = {"path": "evidence/review.json", "sha256": "e" * 64}
    registry["events"] = [
        {
            "id": "A",
            "action": "submit",
            "receipt": round_item["receipt"],
            "source_digest": "c" * 64,
            "timestamp": "2026-10-15T00:00:00Z",
        },
        {
            "id": "A",
            "action": "complete",
            "review": round_item["review"],
            "source_digest": "c" * 64,
            "timestamp": "2026-10-15T00:00:08Z",
        },
    ]
    # When only the recorded-history interval is available.
    result = project(registry=registry)
    # Then it cannot be promoted to actual native review wait.
    recorded = interval(result, "recorded_history_span")
    assert recorded["closed_seconds"] == 8
    assert recorded["start"]["timestamp_source"] == "recorded_history"
    assert interval(result, "submission_to_start")["closed_seconds"] is None


def test_current_objective_cohort_uses_exact_membership_before_bounds():
    # Given two retained tasks but one member of the accepted current objective.
    registry = state("A", "B")
    registry["objective"] = {"id": "objective-1", "task_ids": ["B"], "membership_revision": 2}
    # When complete facts are projected with a small row bound.
    result = project(timeline(task="A"), timeline(task="B"), registry=registry, limit=1)
    # Then objective statistics count only the explicit member, not the displayed slice.
    objective = next(item for item in result["cohorts"] if item["id"] == "current_objective")
    summary = next(
        item
        for item in objective["summaries"]
        if item["kind"] == "submission_to_start" and item["outcome"] == "all_observed"
    )
    assert objective["task_total"] == 1 and objective["membership_revision"] == 2
    assert summary["relevant_total"] == 1 and summary["median_seconds"] == 8


def test_clipped_assessment_is_rejected_instead_of_changing_full_counts():
    # Given a caller accidentally supplying presentation-bound handoff rows.
    facts = {"schema_version": 1, "rows": {"items": [], "total": 60, "shown": 0, "omitted": 60}}
    # When the pure producer receives incomplete facts.
    with pytest.raises(timing.TimingError, match="complete handoff"):
        timing.project(
            state(),
            handoff_facts=facts,
            verification_facts={"schema_version": 1, "records": []},
            capture_facts=[],
            source_binding={"program_id": "timing-fixture"},
        )
    # Then no apparently complete partial cohort can be emitted.


@pytest.mark.parametrize("version", [True, 0, 2, None])
def test_malformed_adopted_schema_does_not_fall_back_to_legacy(version):
    # Given an explicitly supplied unsupported adopted schema.
    facts = {"schema_version": version, "rows": {"items": [], "total": 0, "shown": 0, "omitted": 0}}
    # When the assessment is projected.
    with pytest.raises(timing.TimingError, match="schema version"):
        timing.project(
            state(),
            handoff_facts=facts,
            verification_facts={"schema_version": 1, "records": []},
            capture_facts=[],
            source_binding={"program_id": "timing-fixture"},
        )
    # Then unsupported data never becomes a valid legacy-empty result.


@pytest.mark.parametrize("elapsed", [float("nan"), float("inf"), -1, True, None])
def test_unusable_process_duration_never_becomes_zero_or_a_finite_sample(elapsed):
    # Given a sealed capture whose original elapsed value cannot support measurement.
    capture = {
        "run_id": "run-1",
        "task_id": "A",
        "attempt": 1,
        "process": {
            "started_at": "2026-10-15T00:00:00Z",
            "ended_at": "2026-10-15T00:00:04Z",
            "clock_id": "runner-utc",
            "elapsed_seconds": elapsed,
            "outcome": "exited",
            "exit_code": 0,
        },
    }
    # When its exact original facts are assessed without correction.
    result = project(
        captures=[{"capture": capture, "reference": {"path": "capture", "sha256": "d" * 64}}]
    )
    # Then unavailable process evidence is explicit rather than a false successful sample.
    assert interval(result, "command_process")["closed_seconds"] is None


def test_recorded_block_history_does_not_become_observed_block_time():
    # Given exact recorded block/resolution lineage with repeated block notice.
    registry = state()
    registry["events"] = [
        {"id": "A", "action": "block", "timestamp": "2026-10-15T00:00:00Z"},
        {"id": "A", "action": "block", "timestamp": "2026-10-15T00:00:02Z"},
    ]
    attempt = registry["tasks"][0]["attempts"][0]
    attempt["blocks"] = [
        {"origin_event_index": 0, "event_index": 0},
        {"origin_event_index": 0, "event_index": 1},
    ]
    attempt["resolutions"] = [
        {
            "binding": {"block": {"event_index": 1}},
            "recorded_at": "2026-10-15T00:00:08Z",
            "actor": "operator",
        }
    ]
    # When recorded provenance is projected separately.
    result = project(registry=registry)
    # Then one qualified history span is retained without creating an actual blocked span.
    recorded = interval(result, "recorded_history_span")
    assert recorded["closed_seconds"] == 8
    assert recorded["boundary_label"] == "recorded_block_to_resolution_history"
    assert not any(row["kind"] == "blocked_span" for row in result["intervals"]["items"])


def test_all_observed_median_keeps_explicit_outcome_splits():
    # Given completed and failed observed reviews with different direct wait durations.
    first = timeline(task="A")
    second = timeline(task="B")
    second["observations"][3]["observed_at"] = "2026-10-15T00:00:04Z"
    second["observations"][-1]["outcome"] = "failed"
    # When complete population summaries are computed.
    result = project(first, second, registry=state("A", "B"))
    # Then the explicit combined population and qualified outcome splits remain distinguishable.
    summaries = {
        row["outcome"]: row
        for row in result["cohorts"][0]["summaries"]
        if row["kind"] == "submission_to_start"
    }
    assert summaries["all_observed"]["median_seconds"] == 6
    assert summaries["completed"]["median_seconds"] == 8
    assert summaries["failed"]["median_seconds"] == 4


def test_projected_locator_revision_does_not_change_timing_identity():
    # Given an immutable observed record referenced through a changing outer snapshot revision.
    first = timeline()
    second = deepcopy(first)
    second["reference"]["revision"] = "new-canonical-revision"
    for observation in second["observations"]:
        observation["reference"] = {
            "path": "backlog.json",
            "pointer": "/fixed/observation",
            "revision": "new",
        }
    for observation in first["observations"]:
        observation["reference"] = {
            "path": "backlog.json",
            "pointer": "/fixed/observation",
            "revision": "old",
        }
    # When no actual retained observation fact changed.
    result, repeated = project(first), project(second)
    # Then timing bytes and identity stay stable rather than inheriting projection revision churn.
    assert result == repeated


from tools.tests import test_feature_program_review_handoffs as handoff_cases  # noqa: E402

submitted = handoff_cases.submitted


def test_real_handoff_projection_feeds_timing_without_manual_dto_reconstruction(submitted):
    # Given a normal submitted fixture and actual handoff-module prepared observations.
    fixture, registry = submitted
    prepared = handoff_cases.intent(fixture, registry)
    registry = prepared["state"]
    actual_submit = {
        "kind": "submitted",
        "subject": prepared["subject"],
        "observed_at": "2026-10-15T01:00:00Z",
        "clock_id": "fixture-clock",
        "timestamp_source": "live_postcondition_observation",
        "boundary": "canonical_submission_observed",
        "reference": prepared["packet_ref"],
    }
    sent = handoff_cases.observation(
        fixture, registry, observed_at="2026-10-15T01:00:03Z", submitted_observation=actual_submit
    )
    registry = handoff_cases.handoffs.prepare_observation(registry, sent)["state"]
    for identifier, kind, second in (
        ("ack", "reviewer_acknowledged", 5),
        ("start", "review_started", 8),
    ):
        actual = handoff_cases.observation(
            fixture,
            registry,
            kind,
            identifier,
            observed_at=f"2026-10-15T01:00:{second:02d}Z",
            actor_id="reviewer",
            timestamp_source="reviewer_observation",
            boundary=kind,
        )
        registry = handoff_cases.handoffs.prepare_observation(registry, actual)["state"]
    full_handoffs = handoff_cases.project(fixture, registry)
    # When the real full handoff projection flows through the accepted pure API.
    result = timing.project(
        registry,
        handoff_facts=full_handoffs,
        verification_facts={"schema_version": 1, "records": []},
        capture_facts=[],
        source_binding={"program_id": registry["program_id"]},
    )
    # Then exact observed native boundaries produce the expected direct and leg durations.
    assert interval(result, "submission_to_start")["closed_seconds"] == 8
    assert interval(result, "dispatch_to_ack")["closed_seconds"] == 2
    assert result["current"]["items"][0]["observation_phase"] == "started"


def test_stale_latest_round_retains_revalidation_context_without_current_activity():
    # Given stale current-round native observations and actual revalidation responsibility.
    row = handoff(
        endpoint("submit", "submitted", 0),
        endpoint("start", "review_started", 8),
        currentness="stale",
        next_actor_id="director",
        next_trigger="Revalidate exact source",
        reasons=["handoff_subject_changed"],
        phase="started",
    )
    # When the latest canonical round is projected.
    result = project(row)
    # Then current context remains visible but stale history cannot claim current waiting activity.
    current = result["current"]["items"][0]
    assert current["applicability"] == "stale" and current["resolver_id"] == "director"
    assert current["waiting_since"] is None and current["age"] is None
    assert interval(result, "start_to_terminal")["state"] == "open"


def test_reassignment_preserves_old_run_and_selects_latest_recorded_assignment():
    # Given a superseded running assignment and a newer explicitly recorded replacement.
    old = handoff(
        endpoint("submit", "submitted", 0),
        endpoint("start", "review_started", 8),
        currentness="stale",
        recorded_sequence=1,
    )
    new = handoff(
        endpoint("submit", "submitted", 0),
        assignment_generation=2,
        recorded_sequence=5,
        id=old["handoff_id"] + ":2",
    )
    # When row input order differs from actual canonical assignment order.
    result = project(new, old)
    # Then current selection uses recorded identity, while old outstanding work remains retained.
    assert [row["id"] for row in result["current"]["items"]] == [new["id"]]
    assert (
        len([row for row in result["intervals"]["items"] if row["kind"] == "start_to_terminal"])
        == 2
    )


def test_inverted_process_wall_endpoints_do_not_override_uncertainty_with_elapsed():
    # Given incompatible observed process order even though a finite elapsed value exists.
    capture = {
        "run_id": "run-1",
        "task_id": "A",
        "attempt": 1,
        "process": {
            "started_at": "2026-10-15T00:00:08Z",
            "ended_at": "2026-10-15T00:00:04Z",
            "clock_id": "runner-utc",
            "elapsed_seconds": 4.25,
            "outcome": "exited",
            "exit_code": 0,
        },
    }
    # When the original capture is projected.
    measured = interval(
        project(
            captures=[
                {
                    "capture": capture,
                    "reference": {"path": "evidence/capture.json", "sha256": "d" * 64},
                }
            ]
        ),
        "command_process",
    )
    # Then the contradiction stays explicit and never becomes a reliable finite duration.
    assert measured["closed_seconds"] is None
    assert "inverted_endpoints" in measured["reasons"]


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "failed", "canceled"])
def test_actual_terminal_outcome_is_retained_without_success_only_statistics(outcome):
    # Given an explicitly attributed terminal observation for the exact native review.
    row = timeline(canonical_terminal=True, phase="terminal")
    row["observations"][-1]["outcome"] = outcome
    # When full observed populations are summarized.
    result = project(row)
    # Then actual failed/rejected/canceled spans remain measured in their own qualified class.
    measured = interval(result, "start_to_terminal")
    assert measured["closed_seconds"] == 12
    assert measured["outcome"] == ("completed" if outcome == "accepted" else outcome)
    summary = next(
        item
        for item in result["cohorts"][0]["summaries"]
        if item["kind"] == "start_to_terminal" and item["outcome"] == "all_observed"
    )
    assert summary["closed_valid"] == 1 and summary["median_seconds"] == 12


def test_mismatched_observation_subject_cannot_cross_submission_identity():
    # Given an observation copied from another round.
    row = timeline()
    row["observations"][3]["subject"] = subject(round_number=2)
    # When exact source identity is checked at the projection boundary.
    with pytest.raises(timing.TimingError, match="subject mismatch"):
        project(row)
    # Then no cross-round duration can be emitted.


@pytest.mark.parametrize("clock", [None, "", " "])
def test_unobserved_clock_never_supports_an_open_age(clock):
    # Given a valid timestamp without an actual declared observer clock.
    row = handoff(endpoint("submit", "submitted", 0, clock_id=clock))
    # When waiting anchors are assessed.
    measured = interval(project(row), "submission_to_start")
    # Then an unknown clock cannot produce a live waiting-age claim.
    assert measured["state"] == "unknown"
    assert measured["age"] is None and "missing_clock" in measured["reasons"]


def test_latest_verification_record_does_not_prove_current_source_applicability():
    # Given a latest request whose current source has not been host-assessed.
    records = [
        verification_event("request-1", "request", 0),
        verification_event("start", "started", 3),
    ]
    # When pure timing receives retained history alone.
    result = project(verification=records)
    # Then actual observed wait is still measured but current-source applicability remains unavailable.
    measured = interval(result, "verification_wait")
    assert measured["closed_seconds"] == 3
    assert measured["applicability"] == "unavailable"


def test_explicit_assessed_currentness_cannot_revive_an_old_attempt():
    # Given a current host assessment accidentally attached to a prior task attempt.
    registry = state()
    registry["tasks"][0]["attempts"].append({"number": 2, "rounds": []})
    records = [
        verification_event("request-1", "request", 0),
        verification_event("start", "started", 3),
    ]
    # When exact attempt identity is compared with the authoritative current task.
    result = timing.project(
        registry,
        handoff_facts={"schema_version": 1, "rows": {"items": [], "total": 0}},
        verification_facts={
            "schema_version": 1,
            "records": records,
            "applicability": {"request-1": "current"},
        },
        capture_facts=[],
        source_binding={"program_id": registry["program_id"]},
    )
    # Then a saved old request stays historical, even if it is the latest request in that lane.
    assert interval(result, "verification_wait")["applicability"] == "historical"
