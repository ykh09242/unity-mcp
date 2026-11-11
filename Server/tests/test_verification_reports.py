import pytest

from services.verification.reports import (
    ReportStore,
    final_verdict,
    console_delta,
    test_settled as observed_test_settled,
    test_verdict as observed_test_verdict,
)


def native_result():
    """Shape observed from the owned Editor's native get_test_job handler."""
    return {
        "job_id": "a" * 32,
        "status": "succeeded",
        "completion_kind": "completed",
        "result": {"summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0}},
        "native_control": {
            "version": 1,
            "job_id": "a" * 32,
            "framework": "1.6.0",
            "editor_session": "b" * 32,
            "started_uptime": 10.0,
            "deadline_uptime": 310.0,
            "termination": "confirmed",
            "request_state": "none",
            "released": True,
            "final_witness": True,
            "native_invoked": True,
            "native_guid": "6e764d23-6f6c-470a-abe9-1d2db576e3a4",
            "settled": True,
        },
    }


def test_actual_native_success_settles_without_server_wrapper_fields():
    data = native_result()
    assert "native_cleanup_pending" not in data
    assert observed_test_settled(data)
    assert observed_test_verdict(data) == "passed"


@pytest.mark.parametrize(
    "change",
    [
        {"released": False},
        {"released": "true"},
        {"job_id": "b" * 32},
        {"version": 2},
        {"termination": "cleanup_pending"},
        {"final_witness": False},
        {"observation_lost": True},
        {"ambiguous": True},
    ],
)
def test_native_release_requires_matching_confirmed_receipt(change):
    data = native_result()
    data["native_control"].update(change)
    data["native_cleanup_pending"] = False
    assert not observed_test_settled(data)
    assert observed_test_verdict(data) == "incomplete"


def test_terminal_status_without_native_receipt_cannot_release_ownership():
    data = native_result()
    data.pop("native_control")
    data.update(status="completed", native_cleanup_pending=False)
    assert not observed_test_settled(data)


def test_canceled_native_run_with_passing_partial_results_cannot_pass():
    data = native_result()
    data.update(status="cancelled", completion_kind="cancelled")
    assert observed_test_settled(data)
    assert observed_test_verdict(data) == "canceled"


def test_not_started_native_job_releases_only_without_execution_witness():
    data = native_result()
    data.update(status="failed", completion_kind="failed")
    data["native_control"].update(
        termination="not_started", native_invoked=False, final_witness=False, native_guid=None
    )
    assert observed_test_settled(data)
    assert observed_test_verdict(data) == "failed"


def test_required_unknowns_and_source_drift_override_passing_tests():
    assert final_verdict([{"state": "passed"}, {"state": "incomplete"}]) == "incomplete"
    assert final_verdict([{"state": "failed"}, {"state": "stale"}]) == "stale"
    assert final_verdict([{"state": "passed"}]) == "passed"
    assert final_verdict([]) == "incomplete"


def test_zero_matched_tests_is_not_passed_and_native_cleanup_is_required():
    assert (
        observed_test_verdict(
            {"status": "completed", "result": {"summary": {"total": 0, "passed": 0, "failed": 0}}}
        )
        == "incomplete"
    )
    assert (
        observed_test_verdict(
            {
                "status": "completed",
                "result": {"summary": {"total": 1, "passed": 1, "failed": 0}},
                "native_cleanup_pending": True,
            }
        )
        == "incomplete"
    )


def test_console_without_loss_proof_or_generation_is_unknown():
    before = {"generation": "a", "next_cursor": "cursor", "loss_unknown": True}
    after = {"generation": "a", "items": [], "loss_unknown": True, "has_more": False}
    assert console_delta(before, after)["state"] == "incomplete"
    after.update(generation="b", loss_unknown=False)
    assert console_delta(before, after)["state"] == "incomplete"
    assert console_delta(None, None)["state"] == "incomplete"


def test_reports_are_owner_scoped_immutable_and_expired_ids_never_replay():
    now = [0]
    store = ReportStore(clock=lambda: now[0], max_completed=1, ttl=10)
    first = store.create(("u", "p"), {"profile": "first"})
    store.finish(first, ("u", "p"), {"state": "passed"})
    assert store.read(first, ("other", "p")) is None
    value = store.read(first, ("u", "p"))
    value["state"] = "failed"
    assert store.read(first, ("u", "p"))["state"] == "passed"
    active = store.create(("u", "p"), {"state": "running"})
    now[0] = 100
    assert store.read(first, ("u", "p")) is None
    assert store.read(active, ("u", "p"))["state"] == "running"


def test_unsettled_native_work_is_not_evicted_to_admit_more_runs():
    store = ReportStore(max_active_per_scope=1)
    run = store.create(("u", "p"), {})
    store.finish(run, ("u", "p"), {"state": "incomplete"}, settled=False)
    with pytest.raises(ValueError, match="verification_capacity"):
        store.create(("u", "p"), {})
