"""Retention reserves capacity before copying and preserves unsettled ownership."""

import pytest

from services.script_quality.results import QualityResults, RetentionRefused


def store(**kwargs):
    return QualityResults(
        max_records=2, scope_bytes=1000, total_bytes=1400, reservation_bytes=400, **kwargs
    )


def test_owner_project_and_expiry_are_checked_on_every_read():
    now = [0.0]
    cache = store(clock=lambda: now[0], ttl=10)
    owner = ("user", "project")
    cache.reserve("job", owner)
    cache.complete("job", owner, {"proof": "evidence"}, baseline=True)
    assert cache.read("job", owner, baseline=True)["proof"] == "evidence"
    assert cache.read("job", ("other", "project")) is None
    assert cache.read("job", ("user", "other")) is None
    now[0] = 11
    assert cache.read("job", owner) is None


def test_capacity_refusal_does_not_evict_or_release_active_jobs():
    cache = store()
    owner = ("user", "project")
    cache.reserve("first", owner)
    cache.reserve("second", owner)
    with pytest.raises(RetentionRefused):
        cache.reserve("third", owner)
    assert set(cache.active) == {"first", "second"}
    cache.settle("first", owner)
    cache.reserve("third", owner)
    assert set(cache.active) == {"second", "third"}


def test_completed_records_evict_by_age_without_touching_active_jobs():
    cache = store()
    owner = ("user", "project")
    cache.reserve("active", owner)
    for identity in ("one", "two", "three"):
        cache.reserve(identity, owner)
        cache.complete(identity, owner, {"identity": identity})
    assert cache.read("one", owner) is None
    assert cache.read("three", owner)["identity"] == "three"
    assert "active" in cache.active


def test_shared_budget_spans_principals_and_projects():
    cache = store()
    for index in range(3):
        cache.reserve(str(index), (str(index), "project"))
    with pytest.raises(RetentionRefused):
        cache.reserve("fourth", ("fourth", "project"))
    assert len(cache.active) == 3


def test_oversized_result_does_not_copy_or_discard_its_reservation():
    cache = store()
    owner = ("user", "project")
    cache.reserve("job", owner)
    with pytest.raises(RetentionRefused):
        cache.complete("job", owner, {"value": "x" * 2000})
    assert "job" in cache.active
    assert cache.read("job", owner) is None


def test_returned_payloads_cannot_mutate_retained_evidence():
    cache = store()
    owner = ("user", "project")
    cache.reserve("job", owner)
    cache.complete("job", owner, {"nested": {"value": 1}})
    cache.read("job", owner)["nested"]["value"] = 2
    assert cache.read("job", owner)["nested"]["value"] == 1
    assert cache.read("job", owner, baseline=True) is None
