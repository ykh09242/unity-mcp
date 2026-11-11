import pytest

from services.verification.reports import (
    ReportStore,
    final_verdict,
    console_delta,
    test_verdict as observed_test_verdict,
)


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
