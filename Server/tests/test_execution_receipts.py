"""Ledger acceptance scenarios; these do not prove transport/Unity integration."""

from __future__ import annotations

from tests.timing import watchdog_timeout

import hashlib
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

import services.execution_receipts as ledger
from services.execution_receipt_identity import (
    MAX_ARGUMENT_BYTES,
    ReceiptCapacity,
    ReceiptConflict,
    ReceiptInvalid,
    ReceiptNotFound,
    ReceiptOptions,
    ReceiptScope,
    ReceiptUnavailable,
    capture_identity,
    owned_json,
)
from services.execution_receipts import (
    ExecutionReceiptStore,
    ReceiptInvocation,
    ReceiptView,
    TTL_SECONDS,
)

PROJECT = "0123456789abcdef"
OTHER_PROJECT = "fedcba9876543210"
KEY = "unit-request-key-001"
GRANTS = frozenset({"read", "edit", "test", "run"})
FLOOR = frozenset({"edit"})


@dataclass
class Clock:
    value: float = 1000.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def opened(tmp_path):
    clock, stores = Clock(), []

    def create(name="receipts"):
        store = ExecutionReceiptStore(
            tmp_path / name, private_root=True, clock=clock, monotonic=clock
        )
        stores.append(store)
        return store

    yield create, clock
    for store in reversed(stores):
        store.close()


def options(store, key=KEY, project=PROJECT, *, retain=False):
    return ReceiptOptions.parse(
        {
            "version": 1,
            "store_id": store.store_id,
            "project_hash": project,
            "retain_result": retain,
            **({"idempotency_key": key} if key is not None else {}),
        }
    )


def identity(opts, arguments=None, *, surface="mcp-tool", context=None, version=None):
    return capture_identity(
        surface=surface,
        name="controlled_operation",
        arguments={} if arguments is None else arguments,
        options=opts,
        semantic_context={} if context is None else context,
        component_version=version,
    )


def admit(
    store,
    *,
    key=KEY,
    arguments=None,
    owner="stable-user-A",
    project=PROJECT,
    retain=False,
):
    opts = options(store, key, project, retain=retain)
    return store.admit(
        ReceiptScope(owner, project), opts, identity(opts, arguments), floor=FLOOR, grants=GRANTS
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"extra": True},
        {"version": True},
        {"version": 2},
        {"retain_result": 1},
        {"idempotency_key": None},
        {"idempotency_key": "short"},
        {"project_hash": "prefix"},
        {"store_id": "not-an-installation"},
    ],
)
def test_optional_contract_rejects_malformed_without_callbacks(changed):
    raw = {"version": 1, "store_id": "a" * 32, "project_hash": PROJECT, **changed}
    with pytest.raises(ReceiptInvalid):
        ReceiptOptions.parse(raw)


def test_identity_preserves_exact_values_and_owned_nested_arguments():
    opts = ReceiptOptions("a" * 32, PROJECT, KEY)
    nested = {"items": [1, "value"]}
    first = identity(opts, {"payload": nested, "unity_instance": "A@" + PROJECT})
    reordered = identity(opts, {"unity_instance": "B@" + PROJECT, "payload": nested})
    assert first.digest == reordered.digest
    nested["items"].append("changed")
    assert first.arguments["payload"] == {"items": [1, "value"]}
    assert identity(opts, {}).digest != identity(opts, {"default": 17}).digest
    assert identity(opts, {"value": 1}).digest != identity(opts, {"value": 1.0}).digest
    assert identity(opts, {"items": [1, 2]}).digest != identity(opts, {"items": [2, 1]}).digest
    assert identity(opts, version="1.0").digest != identity(opts, version="2.0").digest
    assert identity(opts, {"text": "\u00e9"}).digest != identity(opts, {"text": "e\u0301"}).digest


def test_rest_route_and_captured_expected_root_belong_to_identity():
    opts = ReceiptOptions("a" * 32, PROJECT, KEY)
    captured = {"expected_project": {"root": "E:/Projects/Original"}, "supplied": True}
    command = identity(opts, surface="/api/command", context=captured)
    context_command = identity(opts, surface="/api/context-command", context=captured)
    assert command.digest != context_command.digest
    captured["expected_project"]["root"] = "E:/Projects/Changed"
    changed = identity(opts, surface="/api/context-command", context=captured)
    assert changed.digest != context_command.digest
    with pytest.raises(ReceiptInvalid):
        identity(opts, surface="/api/context-command")


@pytest.mark.parametrize(
    "value",
    [float("nan"), {1: "not-json"}, "x" * (MAX_ARGUMENT_BYTES + 1)],
    ids=["non-finite", "non-string-key", "oversized-string"],
)
def test_identity_bounds_refuse_unrepresentable_values(value):
    with pytest.raises(ReceiptInvalid):
        owned_json(value)


def test_copy_rejects_hooks_cycles_and_depth_without_running_user_code():
    called = []

    class Hook(dict):
        def __iter__(self):
            called.append(True)
            return super().__iter__()

    with pytest.raises(ReceiptInvalid):
        owned_json(Hook())
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ReceiptInvalid):
        owned_json(cyclic)
    with pytest.raises(ReceiptInvalid):
        owned_json("\ud800")
    assert called == []


def test_default_off_private_root_and_second_writer_are_explicit(tmp_path):
    with pytest.raises(ReceiptUnavailable):
        ExecutionReceiptStore(None)
    with pytest.raises(ReceiptUnavailable):
        ExecutionReceiptStore(tmp_path / "unapproved")
    assert not (tmp_path / "unapproved").exists()
    store = ExecutionReceiptStore(tmp_path / "private", private_root=True)
    try:
        with pytest.raises(ReceiptUnavailable):
            ExecutionReceiptStore(tmp_path / "private", private_root=True)
    finally:
        store.close()


def test_intent_exists_before_handler_and_does_not_persist_input_or_raw_key(opened, tmp_path):
    create, _ = opened
    store = create()
    invocation = admit(store, arguments={"secret": "UNIQUE_APPLICATION_INPUT"})
    assert isinstance(invocation, ReceiptInvocation)
    raw = (tmp_path / "receipts" / (invocation.receipt_id + ".json")).read_text()
    assert json.loads(raw)["intent"] is True
    assert json.loads(raw)["status"] == "pending"
    assert all(value not in raw for value in (KEY, "stable-user-A", "UNIQUE_APPLICATION_INPUT"))
    invocation.enter_handler()
    invocation.finish("completed")


def test_concurrent_same_key_enters_one_controlled_body(opened):
    create, _ = opened
    store = create()
    with ThreadPoolExecutor(max_workers=8) as pool:
        admissions = list(pool.map(lambda _: admit(store), range(8)))
    first = [item for item in admissions if isinstance(item, ReceiptInvocation)]
    assert len(first) == 1 and sum(isinstance(item, ReceiptView) for item in admissions) == 7
    body_entries = 0
    for item in admissions:
        if isinstance(item, ReceiptInvocation):
            item.enter_handler()
            body_entries += 1
            item.finish("completed")
    assert body_entries == 1
    with pytest.raises(ReceiptConflict):
        admit(store, arguments={"changed": True})
    assert body_entries == 1


def test_ownership_operations_do_not_wait_for_a_blocked_journal_commit(opened, monkeypatch):
    # Given a journal call, a fresh handler and a stopped wrapper with an actual worker pin.
    create, _ = opened
    store = create()
    journal_call = admit(store)
    journal_call.enter_handler()
    fresh_call = admit(store, key="fresh-owner-request-002")
    stopped_call = admit(store, key="stopped-owner-request-003")
    stopped_worker = stopped_call.acquire_worker()
    stopped_call.enter_handler()
    stopped_call.finish("unknown", cause="wrapper_cancelled")
    journal_blocked = threading.Barrier(2)
    continue_journal = threading.Event()
    original_write = store._files.write

    def blocked_write(receipt_id, raw):
        journal_blocked.wait(timeout=watchdog_timeout())
        assert continue_journal.wait(timeout=watchdog_timeout())
        original_write(receipt_id, raw)

    def ownership_operations():
        fresh_call.enter_handler()
        acquired_worker = journal_call.acquire_worker()
        try:
            assert store.active_count == 3
            stopped_worker.close()
            return store.active_count, fresh_call.retain_result
        finally:
            acquired_worker.close()

    monkeypatch.setattr(store._files, "write", blocked_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        journal_future = pool.submit(journal_call.extend_floor, frozenset({"run"}), grants=GRANTS)
        ownership_future = None
        try:
            journal_blocked.wait(timeout=watchdog_timeout())

            # When metadata I/O holds _lock, independent ownership still completes promptly.
            ownership_future = pool.submit(ownership_operations)
            assert ownership_future.result(timeout=watchdog_timeout()) == (2, False)

            # Then enter/acquire/release/count needed no journal completion to make progress.
            assert not journal_future.done()
        finally:
            continue_journal.set()
            journal_future.result(timeout=watchdog_timeout())
            if ownership_future is not None:
                ownership_future.result(timeout=watchdog_timeout())
            monkeypatch.setattr(store._files, "write", original_write)
            stopped_worker.close()
            journal_call.finish("unknown")
            fresh_call.finish("unknown")


def test_owner_project_and_store_domains_do_not_rebind(opened):
    create, _ = opened
    store, other_store = create(), create("other-installation")
    assert (
        ReceiptScope("same-id", PROJECT).owner_digest
        != ReceiptScope("same-id", PROJECT, owner_kind="local").owner_digest
    )
    invocations = [
        admit(store),
        admit(store, owner="stable-user-B"),
        admit(store, project=OTHER_PROJECT),
    ]
    assert len({item.receipt_id for item in invocations}) == 3
    for item in invocations:
        item.finish("not_dispatched")
    for scope, store_id, receipt_id in [
        (ReceiptScope("foreign", PROJECT), store.store_id, invocations[0].receipt_id),
        (ReceiptScope("stable-user-A", OTHER_PROJECT), store.store_id, invocations[0].receipt_id),
        (ReceiptScope("stable-user-A", PROJECT), other_store.store_id, invocations[0].receipt_id),
        (ReceiptScope("stable-user-A", PROJECT), store.store_id, "0" * 32),
    ]:
        with pytest.raises(ReceiptNotFound, match="receipt_not_found"):
            store.get(scope, store_id=store_id, grants=GRANTS, receipt_id=receipt_id)


def test_stored_permission_floor_blocks_same_owner_weaker_credential(opened):
    create, _ = opened
    store = create()
    invocation = admit(store)
    invocation.extend_floor(frozenset({"run"}), grants=GRANTS)
    invocation.enter_handler()
    invocation.finish("completed")
    scope = ReceiptScope("stable-user-A", PROJECT)
    with pytest.raises(ReceiptNotFound):
        store.get(
            scope,
            store_id=store.store_id,
            grants=frozenset({"read", "edit"}),
            receipt_id=invocation.receipt_id,
        )
    assert (
        store.get(
            scope, store_id=store.store_id, grants=GRANTS, receipt_id=invocation.receipt_id
        ).status
        == "completed"
    )


def test_unknown_and_expiry_keep_key_and_charge_until_actual_worker_release(opened):
    create, clock = opened
    store = create()
    invocation = admit(store)
    worker = invocation.acquire_worker()
    invocation.enter_handler()
    invocation.finish("unknown", cause="wrapper_cancelled")
    charge = store.charged_metadata_bytes
    clock.value += TTL_SECONDS + 1
    duplicate = admit(store)
    assert isinstance(duplicate, ReceiptView) and duplicate.status == "unknown"
    assert store.active_count == 1 and store.charged_metadata_bytes == charge
    with pytest.raises(ReceiptNotFound):
        store.get(
            ReceiptScope("stable-user-A", PROJECT),
            store_id=store.store_id,
            grants=GRANTS,
            receipt_id=invocation.receipt_id,
        )
    with pytest.raises(ReceiptUnavailable, match="receipt_workers_still_owned"):
        store.close()
    worker.close()
    worker.close()
    assert store.active_count == 0
    replacement = admit(store)
    assert isinstance(replacement, ReceiptInvocation)
    assert replacement.receipt_id != invocation.receipt_id
    replacement.finish("not_dispatched")


def test_post_entry_cannot_claim_not_dispatched(opened):
    create, _ = opened
    store = create()
    invocation = admit(store)
    invocation.enter_handler()
    with pytest.raises(ReceiptInvalid):
        invocation.finish("not_dispatched")
    invocation.finish("unknown")


def test_lookup_and_duplicates_do_not_extend_fixed_expiry(opened):
    create, clock = opened
    store = create()
    invocation = admit(store)
    original = invocation.finish("not_dispatched")
    clock.value += 100
    assert admit(store).expires_at == original.expires_at
    view = store.get(
        ReceiptScope("stable-user-A", PROJECT),
        store_id=store.store_id,
        grants=GRANTS,
        idempotency_key=KEY,
    )
    assert view.expires_at == original.expires_at


def test_keyless_calls_remain_distinct_and_clock_rollback_fails_closed(opened):
    create, clock = opened
    store = create()
    first, second = admit(store, key=None), admit(store, key=None)
    assert first.receipt_id != second.receipt_id
    first.finish("not_dispatched")
    second.finish("not_dispatched")
    clock.value -= 1
    with pytest.raises(ReceiptUnavailable, match="receipt_clock_unavailable"):
        admit(store)


def test_owner_project_active_budget_denies_before_seventeenth_handle(opened):
    create, _ = opened
    store = create()
    invocations = [admit(store, key=f"active-request-{index:04d}") for index in range(16)]
    try:
        with pytest.raises(ReceiptCapacity):
            admit(store, key="seventeenth-request-0017")
        assert store.active_count == 16
    finally:
        for invocation in invocations:
            invocation.finish("not_dispatched")


def test_retention_intent_reserves_two_slots_before_handler_and_stores_exact_bytes(
    opened, tmp_path
):
    # Given an explicit public result whose application metadata may contain sensitive text.
    create, _ = opened
    store = create()
    raw = (
        b'{"content":[{"type":"text","text":"exact application output"}],'
        b'"structuredContent":{"count":2},"_meta":{"application":"sensitive fixture"},'
        b'"isError":true}'
    )
    invocation = admit(store, retain=True)
    assert invocation.retain_result is True
    assert store.charged_result_bytes == 2 * ledger.RESULT_BYTES
    path = tmp_path / "receipts" / (invocation.receipt_id + ".json")
    assert json.loads(path.read_bytes())["result_reason"] == "pending"
    invocation.enter_handler()

    # When the caller supplies its already validated, encoded result.
    view = invocation.finish("completed", result=raw)

    # Then bytes remain exact and only their bounded metadata appears in the record.
    assert view.result_available is True and view.result_reason == "available"
    assert view.result_bytes == len(raw)
    assert (
        store.read_result(
            ReceiptScope("stable-user-A", PROJECT),
            store_id=store.store_id,
            grants=GRANTS,
            receipt_id=invocation.receipt_id,
        )
        == raw
    )
    assert json.loads(path.read_bytes())["result_sha256"] == hashlib.sha256(raw).hexdigest()
    assert b"sensitive fixture" not in path.read_bytes()
    assert store.charged_result_bytes == len(raw)


def test_same_key_cannot_change_result_retention_in_either_direction(opened):
    # Given one record in each retention mode, under distinct keys.
    create, _ = opened
    store = create()
    admit(store).finish("not_dispatched")
    admit(store, key="retained-request-key-002", retain=True).finish("not_dispatched")

    # When either caller changes the opt-in under its existing key.
    for key, retain in [(KEY, True), ("retained-request-key-002", False)]:
        opts = options(store, key, retain=retain)
        with pytest.raises(ReceiptConflict):
            store.lookup(
                ReceiptScope("stable-user-A", PROJECT), opts, identity(opts), grants=GRANTS
            )

    # Then no new admission or handler was created.
    assert store.active_count == 0 and len(store._records) == 2


def test_result_expiry_removes_only_result_and_keeps_fixed_status_and_key(opened, tmp_path):
    # Given an observed retained result at the original wall time.
    create, clock = opened
    store = create()
    invocation = admit(store, retain=True)
    invocation.enter_handler()
    original = invocation.finish("completed", result=b"exact retained bytes")
    clock.value += ledger.RESULT_TTL_SECONDS

    # When its fixed ten-minute result lifetime expires.
    expired = store.get(
        ReceiptScope("stable-user-A", PROJECT),
        store_id=store.store_id,
        grants=GRANTS,
        receipt_id=invocation.receipt_id,
    )

    # Then status/key remain available and the result slot is reclaimed without replay.
    assert expired.status == "completed" and expired.result_reason == "result_expired"
    assert expired.expires_at == original.expires_at and expired.result_available is False
    assert not (tmp_path / "receipts" / (invocation.receipt_id + ".result")).exists()
    assert store.charged_result_bytes == 0
    assert (
        store.read_result(
            ReceiptScope("stable-user-A", PROJECT),
            store_id=store.store_id,
            grants=GRANTS,
            receipt_id=invocation.receipt_id,
        )
        is None
    )
    duplicate = admit(store, retain=True)
    assert isinstance(duplicate, ReceiptView) and duplicate.receipt_id == invocation.receipt_id


def test_result_lifetime_starts_when_observed_and_is_capped_by_status_expiry(opened):
    # Given a call whose result is observed late in its two-hour status lifetime.
    create, clock = opened
    store = create()
    invocation = admit(store, retain=True)
    started = clock.value
    clock.value += TTL_SECONDS - 100
    invocation.enter_handler()

    # When the public result is observed.
    view = invocation.finish("completed", result=b"late result")

    # Then the fixed receipt expiry caps the result's otherwise ten-minute lifetime.
    assert view.result_expires_at == started + TTL_SECONDS
    assert view.expires_at == started + TTL_SECONDS


def test_oversize_result_keeps_truthful_completion_and_key_without_result_file(opened, tmp_path):
    # Given a retained call and a public result above the fixed encoded-byte ceiling.
    create, _ = opened
    store = create()
    invocation = admit(store, retain=True)
    invocation.enter_handler()

    # When completion is observed with an oversized result.
    view = invocation.finish("completed", result=b"x" * (ledger.RESULT_BYTES + 1))

    # Then completion/key survive without a partial result or an eviction.
    assert view.status == "completed" and view.result_reason == "result_too_large"
    assert view.result_available is False and store.charged_result_bytes == 0
    assert list((tmp_path / "receipts").glob("*.result*")) == []
    assert isinstance(admit(store, retain=True), ReceiptView)


def test_result_stage_capacity_refuses_before_fifth_owner_project_handler(opened):
    # Given four active retained calls reserving both current and staging slots.
    create, _ = opened
    store = create()
    calls = [admit(store, key=f"retained-active-{index:04d}", retain=True) for index in range(4)]
    try:
        # When a fifth call would exceed the 8 MiB owner/project budget.
        with pytest.raises(ReceiptCapacity):
            admit(store, key="retained-active-0005", retain=True)

        # Then all existing reservations and keys remain intact.
        assert store.charged_result_bytes == ledger.PROJECT_RESULT_QUOTA
        assert store.active_count == 4
        assert isinstance(admit(store, key="retained-active-0000", retain=True), ReceiptView)
    finally:
        for invocation in calls:
            invocation.finish("not_dispatched")


def test_result_reservations_follow_actual_worker_exit_even_after_unknown_and_expiry(opened):
    # Given one retained call with an actual worker still owned by its invocation.
    create, clock = opened
    store = create()
    invocation = admit(store, retain=True)
    worker = invocation.acquire_worker()
    invocation.enter_handler()
    invocation.finish("unknown", cause="wrapper_cancelled")
    clock.value += TTL_SECONDS + 1
    assert isinstance(admit(store, retain=True), ReceiptView)
    assert store.charged_result_bytes == 2 * ledger.RESULT_BYTES

    # When the actual worker finally exits.
    worker.close()

    # Then its reservations settle and an expired key can be admitted again.
    assert store.charged_result_bytes == 0 and store.active_count == 0
    replacement = admit(store, retain=True)
    assert isinstance(replacement, ReceiptInvocation)
    replacement.finish("not_dispatched")


def test_completed_result_workers_keep_stage_reservations_until_actual_exit(opened):
    # Given completed public results whose actual workers still own all four reservations.
    create, _ = opened
    store = create()
    calls = [admit(store, key=f"retained-worker-{index:04d}", retain=True) for index in range(4)]
    workers = [invocation.acquire_worker() for invocation in calls]
    for invocation in calls:
        invocation.enter_handler()
        invocation.finish("completed", result=b"result")
    try:
        # When another result-retaining call attempts to consume their reserved staging space.
        with pytest.raises(ReceiptCapacity):
            admit(store, key="retained-worker-0005", retain=True)

        # Then active reservations stay at two worst-case slots per actual live worker.
        assert store.charged_result_bytes == 8 * ledger.RESULT_BYTES
        assert store.active_count == 4
    finally:
        for worker in workers:
            worker.close()
    assert store.charged_result_bytes == 4 * len(b"result")
    admit(store, key="retained-worker-0005", retain=True).finish("not_dispatched")


def test_foreign_or_weaker_result_lookup_never_reads_result_bytes(opened, monkeypatch):
    # Given a completed result protected by its stored edit/run permission floor.
    create, _ = opened
    store = create()
    invocation = admit(store, retain=True)
    invocation.extend_floor(frozenset({"run"}), grants=GRANTS)
    invocation.enter_handler()
    invocation.finish("completed", result=b"private application output")
    reads = []
    original = store._files.read_result

    def observed_read(receipt_id):
        reads.append(receipt_id)
        return original(receipt_id)

    monkeypatch.setattr(store._files, "read_result", observed_read)

    # When foreign, wrong-project and weaker credentials try to read it.
    for scope, grants in [
        (ReceiptScope("foreign", PROJECT), GRANTS),
        (ReceiptScope("stable-user-A", OTHER_PROJECT), GRANTS),
        (ReceiptScope("stable-user-A", PROJECT), frozenset({"read", "edit"})),
    ]:
        with pytest.raises(ReceiptNotFound):
            store.read_result(
                scope, store_id=store.store_id, grants=grants, receipt_id=invocation.receipt_id
            )

    # Then authorization rejected every request before result-file I/O.
    assert reads == []


def test_result_expiring_during_disk_read_is_never_delivered(opened, monkeypatch):
    # Given an available result whose disk read crosses its expiry instant.
    create, clock = opened
    store = create()
    invocation = admit(store, retain=True)
    invocation.enter_handler()
    invocation.finish("completed", result=b"expired during disk read")
    original = store._files.read_result

    def delayed_read(receipt_id):
        raw = original(receipt_id)
        clock.value += ledger.RESULT_TTL_SECONDS
        return raw

    monkeypatch.setattr(store._files, "read_result", delayed_read)

    # When the authorized read finishes after the fixed deadline.
    raw = store.read_result(
        ReceiptScope("stable-user-A", PROJECT),
        store_id=store.store_id,
        grants=GRANTS,
        receipt_id=invocation.receipt_id,
    )

    # Then no expired payload is returned and the result charge is released.
    assert raw is None and store.charged_result_bytes == 0


def test_result_hash_corruption_poison_blocks_replay_and_recovery(opened, tmp_path):
    # Given a retained result whose private file was altered outside the writer.
    create, _ = opened
    store = create()
    invocation = admit(store, retain=True)
    invocation.enter_handler()
    invocation.finish("completed", result=b"original bytes")
    path = tmp_path / "receipts" / (invocation.receipt_id + ".result")
    path.write_bytes(b"tampered bytes")

    # When the result's bytes no longer match durable metadata.
    with pytest.raises(ReceiptUnavailable, match="receipt_result_unavailable"):
        store.read_result(
            ReceiptScope("stable-user-A", PROJECT),
            store_id=store.store_id,
            grants=GRANTS,
            receipt_id=invocation.receipt_id,
        )

    # Then the domain refuses new work and a reopen cannot silently trust it.
    with pytest.raises(ReceiptUnavailable):
        admit(store, key="another-request-key-003")
    store.close()
    with pytest.raises(ReceiptUnavailable):
        create()


@pytest.mark.parametrize("limit", ["OWNER_RESULT_QUOTA", "RESULT_QUOTA"])
def test_result_owner_and_global_budgets_include_staging_before_admission(
    opened, monkeypatch, limit
):
    # Given two calls whose two-slot reservations exhaust a reduced owner/global quota.
    create, _ = opened
    store = create()
    monkeypatch.setattr(ledger, limit, 4 * ledger.RESULT_BYTES)
    owners = ["stable-user-A"] * 3 if limit == "OWNER_RESULT_QUOTA" else ["A", "B", "C"]
    calls = [
        admit(store, owner=owners[index], project=f"{index:016x}", retain=True)
        for index in range(2)
    ]
    try:
        # When a distinct project/owner requests a third reservation.
        with pytest.raises(ReceiptCapacity):
            admit(store, owner=owners[2], project=f"{2:016x}", retain=True)

        # Then both older keys survive and storage stays fully charged.
        assert store.charged_result_bytes == 4 * ledger.RESULT_BYTES
        assert store.active_count == 2
    finally:
        for invocation in calls:
            invocation.finish("not_dispatched")


def test_result_publication_before_uncertain_metadata_recovers_unknown_and_cleans_orphan(
    opened,
    monkeypatch,
    tmp_path,
):
    # Given a real result write followed by an uncertain final metadata replacement.
    create, _ = opened
    store = create()
    invocation = admit(store, retain=True)
    worker = invocation.acquire_worker()
    invocation.enter_handler()
    original_replace = os.replace

    def fail_metadata_replace(source, destination):
        if Path(source).suffix == ".stage":
            raise OSError("controlled_metadata_after_result_fault")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_metadata_replace)

    # When final receipt metadata cannot commit after the result was published.
    with pytest.raises(ReceiptUnavailable, match="receipt_commit_uncertain"):
        invocation.finish("completed", result=b"must never replay an uncertain observation")

    # Then worker charge persists, and exclusive recovery suppresses resend and orphan replay.
    assert store.charged_result_bytes == 2 * ledger.RESULT_BYTES
    worker.close()
    store.close()
    monkeypatch.setattr(os, "replace", original_replace)
    recovered = create()
    duplicate = admit(recovered, retain=True)
    assert isinstance(duplicate, ReceiptView) and duplicate.status == "unknown"
    assert duplicate.result_reason == "observation_lost"
    assert list((tmp_path / "receipts").glob("*.result*")) == []


def test_uncertain_result_replace_poison_preserves_intent_and_recovers_without_resend(
    opened,
    monkeypatch,
    tmp_path,
):
    # Given a durable retained intent before any result is written.
    create, _ = opened
    store = create()
    invocation = admit(store, retain=True)
    invocation.enter_handler()
    original_replace = os.replace

    def fail_result_replace(source, destination):
        if Path(source).suffix == ".result-stage":
            raise OSError("controlled_result_replace_fault")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_result_replace)

    # When the result's atomic publication has an uncertain failure.
    with pytest.raises(ReceiptUnavailable, match="receipt_result_commit_uncertain"):
        invocation.finish("completed", result=b"unobserved persisted completion")

    # Then admission fails closed and recovery keeps the key UNKNOWN without result bytes.
    with pytest.raises(ReceiptUnavailable):
        admit(store, key="second-request-key-002", retain=True)
    store.close()
    monkeypatch.setattr(os, "replace", original_replace)
    recovered = create()
    duplicate = admit(recovered, retain=True)
    assert isinstance(duplicate, ReceiptView) and duplicate.status == "unknown"
    assert duplicate.result_reason == "observation_lost"
    assert list((tmp_path / "receipts").glob("*.result*")) == []


def test_reopen_preserves_exact_unexpired_result_and_cleans_expired_result(opened, tmp_path):
    # Given a completed result that survives a normal close and exclusive reopen.
    create, clock = opened
    store = create()
    invocation = admit(store, retain=True)
    invocation.enter_handler()
    original = invocation.finish("completed", result=b"stable original payload")
    store.close()
    recovered = create()
    assert (
        recovered.read_result(
            ReceiptScope("stable-user-A", PROJECT),
            store_id=recovered.store_id,
            grants=GRANTS,
            receipt_id=invocation.receipt_id,
        )
        == b"stable original payload"
    )
    recovered.close()
    clock.value = original.result_expires_at

    # When a later reopen observes expiry.
    expired_store = create()

    # Then its receipt/key remain completed while the expired bytes are removed.
    duplicate = admit(expired_store, retain=True)
    assert isinstance(duplicate, ReceiptView) and duplicate.status == "completed"
    assert duplicate.result_reason == "result_expired"
    assert list((tmp_path / "receipts").glob("*.result*")) == []


def test_duplicate_only_lookup_never_creates_intent_and_enforces_stored_floor(opened, tmp_path):
    # Given a missing key and then a completed record with an extended run floor.
    create, _ = opened
    store = create()
    opts = options(store, retain=True)
    scope = ReceiptScope("stable-user-A", PROJECT)
    assert store.lookup(scope, opts, identity(opts), grants=GRANTS) is None
    assert list((tmp_path / "receipts").glob("*.json")) == []
    invocation = admit(store, retain=True)
    invocation.extend_floor(frozenset({"run"}), grants=GRANTS)
    invocation.enter_handler()
    invocation.finish("completed", result=b"authorized historical output")

    # When a weaker same-owner credential requests the historical duplicate.
    with pytest.raises(ReceiptNotFound):
        store.lookup(scope, opts, identity(opts), grants=frozenset({"read", "edit"}))

    # Then it obtains neither a replay nor a new invocation.
    assert store.active_count == 0 and len(store._records) == 1


def test_native_attempts_keep_recent_scalars_and_never_upgrade_stopped_wrapper(opened):
    # Given bounded observations for HTTP UUID and stdio integer generations.
    create, _ = opened
    store = create()
    invocation = admit(store)
    invocation.enter_handler()
    for index in range(10):
        invocation.observe_attempt(
            "http" if index % 2 else "stdio",
            f"native-attempt-{index}",
            session_id="session-a",
            generation="http-generation" if index % 2 else index,
            dispatched=True,
            observed=False,
            cause="dispatching",
        )
    invocation.observe_attempt(
        "http",
        "native-attempt-9",
        session_id="session-a",
        generation="http-generation",
        dispatched=True,
        observed=True,
        cause="observed",
    )

    # When the wrapper becomes UNKNOWN and a stale worker reports later success.
    view = invocation.finish("unknown")
    with pytest.raises(ReceiptInvalid):
        invocation.observe_attempt(
            "http",
            "native-attempt-9",
            dispatched=True,
            observed=True,
            cause="observed",
        )

    # Then metadata stays UNKNOWN with eight safe recent attempts and one count per recent ID.
    assert view.status == "unknown" and len(view.attempts) == 8 and view.attempt_count == 10
    assert view.attempts[-1].observed is True


def test_native_attempt_cannot_rebind_an_existing_attempt_to_a_replacement_generation(opened):
    # Given one pending attempt bound to its selected generation.
    create, _ = opened
    store = create()
    invocation = admit(store)
    invocation.enter_handler()
    invocation.observe_attempt(
        "http",
        "same-attempt",
        session_id="same-session",
        generation="old-generation",
        dispatched=True,
        observed=False,
        cause="dispatching",
    )
    try:
        # When an observation tries to attach that identifier to a replacement target.
        with pytest.raises(ReceiptInvalid, match="receipt_attempt_target_changed"):
            invocation.observe_attempt(
                "http",
                "same-attempt",
                session_id="same-session",
                generation="new-generation",
                dispatched=True,
                observed=True,
                cause="observed",
            )
    finally:
        view = invocation.finish("unknown")

    # Then historical metadata preserves the original target and UNKNOWN status.
    assert view.status == "unknown" and view.attempts[0].generation == "old-generation"


def test_unknown_result_schema_is_not_silently_reinterpreted(opened, tmp_path):
    # Given an otherwise valid record stamped with an unknown on-disk schema.
    create, _ = opened
    store = create()
    invocation = admit(store, retain=True)
    invocation.finish("not_dispatched")
    store.close()
    path = tmp_path / "receipts" / (invocation.receipt_id + ".json")
    data = json.loads(path.read_bytes())
    data["schema_version"] = 999
    path.write_bytes(json.dumps(data).encode("utf-8"))

    # When exclusive recovery reads that record.
    with pytest.raises(ReceiptUnavailable):
        create()

    # Then it retains the unknown file rather than manufacturing new executable intent.
    assert json.loads(path.read_bytes())["schema_version"] == 999


def test_capacity_refuses_without_evicting_unexpired_key(opened, monkeypatch):
    create, _ = opened
    store = create()
    # Reduced record budget exercises eviction behavior without 1024 fsyncs.
    monkeypatch.setattr(ledger, "MAX_PROJECT_RECORDS", 2)
    for key in (KEY, "second-request-key-002"):
        admit(store, key=key).finish("not_dispatched")
    with pytest.raises(ReceiptCapacity):
        admit(store, key="third-request-key-003")
    assert isinstance(admit(store), ReceiptView)


def test_stage_reservation_refuses_second_handler_before_storage_overflow(opened, monkeypatch):
    create, _ = opened
    store = create()
    monkeypatch.setattr(ledger, "METADATA_QUOTA", store._files.overhead + 3 * ledger.METADATA_BYTES)
    invocation = admit(store)
    with pytest.raises(ReceiptCapacity):
        admit(store, key="second-request-key-002")
    assert store.active_count == 1
    invocation.finish("not_dispatched")


def test_unknown_policy_version_cannot_be_read_or_reused(opened, tmp_path):
    create, _ = opened
    store = create()
    invocation = admit(store)
    invocation.finish("not_dispatched")
    store.close()
    path = tmp_path / "receipts" / (invocation.receipt_id + ".json")
    data = json.loads(path.read_bytes())
    data["policy_version"] = 999
    path.write_bytes(json.dumps(data).encode("utf-8"))
    with pytest.raises(ReceiptUnavailable):
        create()


def test_uncertain_final_replace_blocks_new_work_and_recovers_unknown(opened, monkeypatch):
    create, _ = opened
    store = create()
    invocation = admit(store)
    worker = invocation.acquire_worker()
    invocation.enter_handler()
    effects = []
    effects.append("controlled_effect")
    original_replace = os.replace

    def failed_replace(*_args):
        raise OSError("controlled_atomic_replace_fault")

    monkeypatch.setattr(os, "replace", failed_replace)
    with pytest.raises(ReceiptUnavailable, match="receipt_commit_uncertain"):
        invocation.finish("completed")
    with pytest.raises(ReceiptUnavailable):
        admit(store)
    assert store.active_count == 1
    with pytest.raises(ReceiptUnavailable, match="receipt_workers_still_owned"):
        store.close()
    worker.close()
    assert store.active_count == 0
    store.close()
    monkeypatch.setattr(os, "replace", original_replace)
    recovered = create()
    duplicate = admit(recovered)
    if isinstance(duplicate, ReceiptInvocation):
        duplicate.enter_handler()
        effects.append("controlled_effect")
        duplicate.finish("completed")
    assert isinstance(duplicate, ReceiptView) and duplicate.status == "unknown"
    assert effects == ["controlled_effect"]


def test_process_crash_after_intent_recovers_unknown_without_resend(tmp_path):
    source = Path(ledger.__file__).resolve().parents[1]
    root = tmp_path / "crashed-ledger"
    code = "\n".join(
        [
            "import os,sys",
            "from pathlib import Path",
            f"sys.path.insert(0,{str(source)!r})",
            "from services.execution_receipt_identity import ("
            "ReceiptScope,ReceiptOptions,capture_identity)",
            "from services.execution_receipts import ExecutionReceiptStore",
            f"s=ExecutionReceiptStore(Path({str(root)!r}),private_root=True)",
            f"o=ReceiptOptions(s.store_id,{PROJECT!r},{KEY!r})",
            "i=capture_identity(surface='mcp-tool',name='controlled_operation',arguments={},"
            "options=o,semantic_context={})",
            f"s.admit(ReceiptScope('stable-user-A',{PROJECT!r}),o,i,"
            "floor=frozenset(['edit']),grants=frozenset(['edit']))",
            "os._exit(23)",
        ]
    )
    result = subprocess.run(
        [sys._base_executable, "-B", "-I", "-S", "-c", code],
        cwd=tmp_path,
        timeout=8,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    store = ExecutionReceiptStore(root, private_root=True)
    try:
        view = admit(store)
        assert isinstance(view, ReceiptView) and view.status == "unknown"
    finally:
        store.close()


def test_late_evicted_attempt_observation_does_not_double_count_physical_attempt(opened):
    # Given ten distinct physical attempts with only eight recent evidence entries.
    create, _ = opened
    store = create()
    invocation = admit(store)
    invocation.enter_handler()
    for number in range(10):
        invocation.observe_attempt(
            "http",
            f"attempt-{number}",
            session_id="session",
            generation="generation",
            dispatched=True,
            observed=False,
            cause="dispatching",
        )
    # When an old evicted physical attempt is observed later.
    invocation.observe_attempt(
        "http",
        "attempt-0",
        session_id="session",
        generation="generation",
        dispatched=True,
        observed=True,
        cause="observed",
        new_attempt=False,
    )
    view = invocation.finish("completed")
    # Then bounded recent evidence changes without inventing another physical attempt.
    assert view.attempt_count == 10
    assert len(view.attempts) == 8
    assert view.attempts[-1].attempt_id == "attempt-0"


def test_rest_native_unity_instance_parameter_is_not_removed_from_identity(opened):
    # Given a bound REST selector separate from an ordinary native parameter of that name.
    create, _ = opened
    store = create()
    opts = options(store)
    first = identity(opts, {"unity_instance": "application-value-a"}, surface="/api/command")
    # When only that native parameter changes.
    second = identity(opts, {"unity_instance": "application-value-b"}, surface="/api/command")
    # Then REST exact native payload identity changes; only MCP's top selector is excluded.
    assert first.digest != second.digest


@pytest.mark.parametrize("rollback", [0.1, 86400])
def test_clock_catchup_restores_health_without_replacing_deduplication(opened, rollback):
    create, wall = opened
    store = create()
    original = admit(store).finish("not_dispatched")
    wall.value -= rollback
    with pytest.raises(ReceiptUnavailable, match="receipt_clock_unavailable"):
        admit(store)
    assert not store.available
    assert not store._poisoned
    wall.value += rollback + 1
    assert store.available
    duplicate = admit(store)
    assert isinstance(duplicate, ReceiptView)
    assert duplicate.receipt_id == original.receipt_id
    assert duplicate.expires_at == original.expires_at


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), None, True])
def test_invalid_clock_sample_can_recover_without_clearing_storage(opened, invalid):
    create, wall = opened
    store = create()
    original = admit(store).finish("not_dispatched")
    wall.value = invalid
    assert not store.available
    wall.value = 1001
    assert store.available
    assert admit(store).receipt_id == original.receipt_id


def test_persisted_future_time_refuses_reopen_until_clock_catches_up(opened, tmp_path):
    create, wall = opened
    store = create()
    original = admit(store).finish("not_dispatched")
    store.close()
    root = tmp_path / "receipts"
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    wall.value = 999
    with pytest.raises(ReceiptUnavailable, match="receipt_clock_unavailable"):
        create()
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    wall.value = 1001
    reopened = create()
    assert reopened.store_id == store.store_id
    assert admit(reopened).receipt_id == original.receipt_id


def test_wall_catchup_cannot_clear_actual_storage_poison(opened, tmp_path):
    create, wall = opened
    store = create()
    original = admit(store).finish("not_dispatched")
    path = tmp_path / "receipts" / (original.receipt_id + ".json")
    path.write_bytes(b"broken")
    with pytest.raises(ReceiptUnavailable):
        admit(store)
    wall.value = 2000
    assert not store.available and store._poisoned
    with pytest.raises(ReceiptUnavailable):
        admit(store)
    store.close()
    with pytest.raises(ReceiptUnavailable):
        create()


def test_monotonic_elapsed_controls_result_and_key_expiry_across_wall_adjustments(tmp_path):
    wall, elapsed = Clock(), Clock(0)
    store = ExecutionReceiptStore(
        tmp_path / "clock-store", private_root=True, clock=wall, monotonic=elapsed
    )
    try:
        invocation = admit(store, retain=True)
        invocation.enter_handler()
        original = invocation.finish("completed", result=b"retained")
        wall.value += 10 * TTL_SECONDS
        # A forward wall adjustment must not expire a key or result early.
        duplicate = admit(store, retain=True)
        assert duplicate.receipt_id == original.receipt_id and duplicate.result_available
        # A stopped wall clock must not keep the result alive beyond elapsed TTL.
        elapsed.value = ledger.RESULT_TTL_SECONDS
        duplicate = admit(store, retain=True)
        assert duplicate.receipt_id == original.receipt_id and not duplicate.result_available
        assert duplicate.expires_at == original.expires_at
        elapsed.value = TTL_SECONDS + 1
        replacement = admit(store, retain=True)
        assert isinstance(replacement, ReceiptInvocation)
        assert replacement.receipt_id != original.receipt_id
        replacement.finish("not_dispatched")
    finally:
        store.close()


def test_monotonic_rollback_refuses_until_validated_catchup(tmp_path):
    wall, elapsed = Clock(), Clock(10)
    store = ExecutionReceiptStore(
        tmp_path / "monotonic-store", private_root=True, clock=wall, monotonic=elapsed
    )
    try:
        original = admit(store).finish("not_dispatched")
        elapsed.value = 9
        assert not store.available and not store._poisoned
        with pytest.raises(ReceiptUnavailable, match="receipt_clock_unavailable"):
            admit(store)
        elapsed.value = 11
        assert store.available
        assert admit(store).receipt_id == original.receipt_id
    finally:
        store.close()


def test_clock_recovery_keeps_expired_key_pinned_to_actual_worker(tmp_path):
    wall, elapsed = Clock(), Clock(0)
    store = ExecutionReceiptStore(
        tmp_path / "owned-clock-store", private_root=True, clock=wall, monotonic=elapsed
    )
    invocation = admit(store)
    worker = invocation.acquire_worker()
    try:
        invocation.enter_handler()
        original = invocation.finish("unknown", cause="wrapper_cancelled")
        wall.value -= 0.1
        elapsed.value = TTL_SECONDS + 1
        with pytest.raises(ReceiptUnavailable, match="receipt_clock_unavailable"):
            admit(store)
        wall.value = 1001
        duplicate = admit(store)
        assert isinstance(duplicate, ReceiptView) and duplicate.status == "unknown"
        assert duplicate.receipt_id == original.receipt_id and store.active_count == 1
        worker.close()
        replacement = admit(store)
        assert isinstance(replacement, ReceiptInvocation)
        assert replacement.receipt_id != original.receipt_id
        replacement.finish("not_dispatched")
    finally:
        worker.close()
        store.close()


def test_coarse_wall_crash_recovers_unknown_with_original_key(tmp_path):
    # Given a child whose monotonic clock advances within one coarse wall-clock tick.
    source = Path(ledger.__file__).resolve().parents[1]
    root = tmp_path / "coarse-crash"
    code = "\n".join(
        [
            "import os,sys",
            "from pathlib import Path",
            f"sys.path.insert(0,{str(source)!r})",
            "from services.execution_receipt_identity import ("
            "ReceiptScope,ReceiptOptions,capture_identity)",
            "from services.execution_receipts import ExecutionReceiptStore",
            "elapsed=[0.0]",
            f"s=ExecutionReceiptStore(Path({str(root)!r}),private_root=True,"
            "clock=lambda:1000.0,monotonic=lambda:elapsed[0])",
            "elapsed[0]=0.005",
            f"o=ReceiptOptions(s.store_id,{PROJECT!r},{KEY!r})",
            "i=capture_identity(surface='mcp-tool',name='controlled_operation',arguments={},"
            "options=o,semantic_context={})",
            f"s.admit(ReceiptScope('stable-user-A',{PROJECT!r}),o,i,"
            "floor=frozenset(['edit']),grants=frozenset(['edit']))",
            "os._exit(23)",
        ]
    )
    result = subprocess.run(
        [sys._base_executable, "-B", "-I", "-S", "-c", code],
        cwd=tmp_path,
        timeout=8,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    path = next(root.glob("*.json"))
    original = json.loads(path.read_bytes())
    assert original["created_at"] == original["updated_at"] == 1000.005

    # When a new process opens before the coarse wall tick advances.
    store = ExecutionReceiptStore(root, private_root=True, clock=Clock(), monotonic=Clock(0.010))
    try:
        # Then the logical floor and deduplication survive without a fresh invocation.
        duplicate = admit(store)
        assert isinstance(duplicate, ReceiptView) and duplicate.status == "unknown"
        assert duplicate.receipt_id == original["receipt_id"]
        assert duplicate.updated_at >= original["updated_at"]
        assert duplicate.expires_at == original["expires_at"]
        assert isinstance(admit(store), ReceiptView)
    finally:
        store.close()


@pytest.mark.parametrize("rollback", [0.001, 8000])
def test_restart_uses_raw_wall_floor_after_forward_adjustment(tmp_path, rollback):
    # Given a commit whose raw wall is far ahead of its monotonic-derived timestamp.
    wall, elapsed = Clock(), Clock(0)
    root = tmp_path / "forward-wall"
    store = ExecutionReceiptStore(root, private_root=True, clock=wall, monotonic=elapsed)
    invocation = admit(store)
    wall.value, elapsed.value = 10000, 1
    original = invocation.finish("not_dispatched")
    assert original.updated_at == 1001
    store.close()
    before = {path.name: path.read_bytes() for path in root.iterdir()}

    # When even a sub-tick actual wall rollback occurs before exclusive reopen.
    wall.value -= rollback
    unexpected = None
    try:
        with pytest.raises(ReceiptUnavailable, match="receipt_clock_unavailable"):
            unexpected = ExecutionReceiptStore(
                root, private_root=True, clock=wall, monotonic=elapsed
            )
    finally:
        if unexpected is not None:
            unexpected.close()

    # Then the original bytes/key stay intact until that raw-wall high-water catches up.
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    wall.value = 10000
    recovered = ExecutionReceiptStore(root, private_root=True, clock=wall, monotonic=elapsed)
    try:
        # Existing restart semantics expire fixed wall-time deadlines across downtime.
        replacement = admit(recovered)
        assert isinstance(replacement, ReceiptInvocation)
        assert replacement.receipt_id != original.receipt_id
        replacement.finish("not_dispatched")
    finally:
        recovered.close()


def test_stopped_wall_does_not_extend_result_or_owned_key_lifetimes(tmp_path):
    # Given a retained result plus an UNKNOWN invocation with an actual live worker.
    wall, elapsed = Clock(), Clock(0)
    store = ExecutionReceiptStore(
        tmp_path / "stopped-wall", private_root=True, clock=wall, monotonic=elapsed
    )
    completed = admit(store, retain=True)
    completed.enter_handler()
    original = completed.finish("completed", result=b"retained")
    owned = admit(store, key="owned-stopped-wall-002", retain=True)
    worker = owned.acquire_worker()
    owned.enter_handler()
    stopped = owned.finish("unknown", cause="wrapper_cancelled")
    try:
        # When monotonic time passes both TTLs while the raw wall remains stopped.
        elapsed.value = ledger.RESULT_TTL_SECONDS
        duplicate = admit(store, retain=True)
        assert isinstance(duplicate, ReceiptView) and not duplicate.result_available
        assert duplicate.receipt_id == original.receipt_id
        elapsed.value = TTL_SECONDS + 1

        # Then unowned keys expire, but the actual worker still pins its own key/charge.
        replacement = admit(store, retain=True)
        assert isinstance(replacement, ReceiptInvocation)
        assert replacement.receipt_id != original.receipt_id
        replacement.finish("not_dispatched")
        pinned = admit(store, key="owned-stopped-wall-002", retain=True)
        assert isinstance(pinned, ReceiptView) and pinned.receipt_id == stopped.receipt_id
        assert store.charged_result_bytes == 2 * ledger.RESULT_BYTES
        worker.close()
        fresh = admit(store, key="owned-stopped-wall-002", retain=True)
        assert isinstance(fresh, ReceiptInvocation) and fresh.receipt_id != stopped.receipt_id
        fresh.finish("not_dispatched")
    finally:
        worker.close()
        store.close()


def test_clock_health_and_ownership_progress_during_commit_and_next_commit_stamps_wall(
    tmp_path, monkeypatch
):
    # Given a metadata commit stopped inside disk I/O after taking its clock snapshot.
    wall, elapsed = Clock(), Clock(0)
    root = tmp_path / "blocked-clock"
    store = ExecutionReceiptStore(root, private_root=True, clock=wall, monotonic=elapsed)
    invocation = admit(store)
    blocked, resume = threading.Event(), threading.Event()
    original_write = store._files.write

    def blocked_write(receipt_id, raw):
        blocked.set()
        assert resume.wait(timeout=watchdog_timeout())
        original_write(receipt_id, raw)

    def health_and_ownership():
        worker = invocation.acquire_worker()
        try:
            return store.available, store.active_count
        finally:
            worker.close()

    monkeypatch.setattr(store._files, "write", blocked_write)
    finished = False
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            committing = pool.submit(invocation.extend_floor, frozenset({"run"}), grants=GRANTS)
            try:
                assert blocked.wait(timeout=watchdog_timeout())
                wall.value, elapsed.value = 1001, 1
                # When availability/ownership sample the newer wall during the blocked write.
                health = pool.submit(health_and_ownership)
                assert health.result(timeout=watchdog_timeout()) == (True, 1)
                assert not committing.done()
            finally:
                resume.set()
                committing.result(timeout=watchdog_timeout())
        monkeypatch.setattr(store._files, "write", original_write)

        # Then a later commit persists that validated sample without any extra clock file.
        invocation.finish("not_dispatched")
        finished = True
        raw = json.loads((root / (invocation.receipt_id + ".json")).read_bytes())
        assert raw["schema_version"] == 3 and raw["wall_clock_high_water"] == 1001
    finally:
        resume.set()
        monkeypatch.setattr(store._files, "write", original_write)
        if not finished:
            invocation.finish("not_dispatched")
        store.close()


def test_mixed_legacy_and_current_records_recover_without_rebinding_keys(opened, tmp_path):
    # Given one legacy pending record beside a current terminal record.
    create, _ = opened
    store = create()
    legacy = admit(store).finish("not_dispatched")
    current = admit(store, key="current-schema-key-002").finish("not_dispatched")
    store.close()
    path = tmp_path / "receipts" / (legacy.receipt_id + ".json")
    data = json.loads(path.read_bytes())
    data.pop("wall_clock_high_water", None)
    data.update(schema_version=2, status="pending", cause="intent_committed")
    path.write_bytes(json.dumps(data).encode("utf-8"))

    # When exclusive recovery upgrades the pending legacy observation.
    recovered = create()

    # Then both keys keep their IDs and only UNKNOWN metadata is returned for legacy intent.
    duplicate = admit(recovered)
    assert isinstance(duplicate, ReceiptView) and duplicate.status == "unknown"
    assert duplicate.receipt_id == legacy.receipt_id
    assert admit(recovered, key="current-schema-key-002").receipt_id == current.receipt_id
    upgraded = json.loads(path.read_bytes())
    assert upgraded["schema_version"] == 3 and upgraded["wall_clock_high_water"] == 1000


def test_legacy_future_logical_timestamp_stays_fail_closed_until_wall_catches_up(tmp_path):
    # Given an old schema record with no evidence separating wall from logical time.
    wall, elapsed = Clock(), Clock(0)
    root = tmp_path / "legacy-future"
    store = ExecutionReceiptStore(root, private_root=True, clock=wall, monotonic=elapsed)
    elapsed.value = 0.005
    original = admit(store).finish("not_dispatched")
    store.close()
    path = root / (original.receipt_id + ".json")
    data = json.loads(path.read_bytes())
    data.pop("wall_clock_high_water", None)
    data["schema_version"] = 2
    path.write_bytes(json.dumps(data).encode("utf-8"))
    before = {entry.name: entry.read_bytes() for entry in root.iterdir()}

    # When the legacy logical timestamp is still ahead of the wall, refuse unchanged.
    with pytest.raises(ReceiptUnavailable, match="receipt_clock_unavailable"):
        ExecutionReceiptStore(root, private_root=True, clock=wall, monotonic=elapsed)
    assert {entry.name: entry.read_bytes() for entry in root.iterdir()} == before

    # Then validated catch-up reopens that same key without manufacturing an invocation.
    wall.value = original.updated_at
    recovered = ExecutionReceiptStore(root, private_root=True, clock=wall, monotonic=elapsed)
    try:
        duplicate = admit(recovered)
        assert isinstance(duplicate, ReceiptView) and duplicate.receipt_id == original.receipt_id
    finally:
        recovered.close()


@pytest.mark.parametrize("invalid", [None, True, "1000", -1, float("nan"), float("inf")])
def test_current_schema_requires_finite_numeric_raw_wall_floor(opened, tmp_path, invalid):
    # Given current metadata with a malformed wall-domain recovery floor.
    create, _ = opened
    store = create()
    original = admit(store).finish("not_dispatched")
    store.close()
    path = tmp_path / "receipts" / (original.receipt_id + ".json")
    data = json.loads(path.read_bytes())
    data.update(schema_version=3, wall_clock_high_water=invalid)
    path.write_bytes(json.dumps(data).encode("utf-8"))
    before = path.read_bytes()

    # When exclusive recovery reads the invalid floor, it refuses unchanged.
    with pytest.raises(ReceiptUnavailable):
        create()
    assert path.read_bytes() == before


def test_current_schema_requires_raw_wall_floor_member(opened, tmp_path):
    # Given current metadata with its required wall-domain evidence removed.
    create, _ = opened
    store = create()
    original = admit(store).finish("not_dispatched")
    store.close()
    path = tmp_path / "receipts" / (original.receipt_id + ".json")
    data = json.loads(path.read_bytes())
    data["schema_version"] = 3
    data.pop("wall_clock_high_water", None)
    path.write_bytes(json.dumps(data).encode("utf-8"))
    before = path.read_bytes()

    # When exclusive recovery reads it, it cannot reinterpret it as legacy evidence.
    with pytest.raises(ReceiptUnavailable):
        create()
    assert path.read_bytes() == before
