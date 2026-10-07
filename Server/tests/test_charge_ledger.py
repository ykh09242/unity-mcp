"""Reservation totals agree with a full-scan oracle across mapping mutations."""

import random

import pytest

from models.response_limits import ResponseOwner
from transport.charge_ledger import ChargeLedger


def charge(size, user="alice", session="one"):
    return {"bytes": size, "user_id": user, "session_id": session}


def check(ledger, reference):
    assert ledger == reference
    assert ledger.total_bytes == sum(row["bytes"] for row in reference.values())
    for user in ("alice", "bob", None, "missing"):
        assert ledger.user_bytes(user) == sum(
            row["bytes"] for row in reference.values() if row["user_id"] == user
        )
    for session in ("one", "two", "missing"):
        assert ledger.session_bytes(session) == sum(
            row["bytes"] for row in reference.values() if row["session_id"] == session
        )


def test_mapping_mutations_match_full_scan_oracle():
    ledger, reference = ChargeLedger(), {}
    generator = random.Random(117)
    for _ in range(400):
        key = f"owned-{generator.randrange(12)}"
        operation = generator.randrange(6)
        record = charge(
            generator.randrange(1000),
            generator.choice(("alice", "bob", None)),
            generator.choice(("one", "two")),
        )
        if operation == 0:
            ledger[key] = record
            reference[key] = record.copy()
        elif operation == 1:
            assert ledger.pop(key, None) == reference.pop(key, None)
        elif operation == 2:
            ledger.update({key: record})
            reference.update({key: record.copy()})
        elif operation == 3:
            assert ledger.setdefault(key, record) == reference.setdefault(key, record.copy())
        elif operation == 4:
            if key in reference:
                del ledger[key]
                del reference[key]
            else:
                with pytest.raises(KeyError):
                    del ledger[key]
        elif reference:
            assert ledger.popitem() == reference.popitem()
        check(ledger, reference)
    ledger.clear()
    ledger.clear()
    check(ledger, {})
    assert not ledger._users and not ledger._sessions


def test_charge_records_are_independent_and_immutable():
    original = charge(10)
    ledger = ChargeLedger({"owned": original})
    original.update(charge(100, "bob", "two"))
    check(ledger, {"owned": charge(10)})
    with pytest.raises(TypeError):
        ledger["owned"]["bytes"] = 20
    check(ledger, {"owned": charge(10)})


@pytest.mark.parametrize(
    "record", [charge(-1), charge(True), charge(1, user=3), charge(1, session=None), {}]
)
def test_invalid_replacement_leaves_original_accounting_intact(record):
    ledger = ChargeLedger({"owned": charge(20)})
    with pytest.raises((TypeError, ValueError, KeyError)):
        ledger["owned"] = record
    check(ledger, {"owned": charge(20)})


def test_pop_default_and_missing_pop_preserve_totals():
    ledger = ChargeLedger({"owned": charge(10)})
    marker = object()
    assert ledger.pop("missing", marker) is marker
    with pytest.raises(KeyError):
        ledger.pop("missing")
    with pytest.raises(KeyError):
        ChargeLedger().popitem()
    check(ledger, {"owned": charge(10)})


def test_captured_old_owner_release_cannot_change_replacement_ledger():
    original = ChargeLedger({"owned": charge(10)})
    owner = ResponseOwner()
    owner.entries.append((original, "owned"))
    replacement = ChargeLedger({"owned": charge(30, "bob", "two")})
    owner.release()
    owner.release()
    check(original, {})
    check(replacement, {"owned": charge(30, "bob", "two")})


def test_adopt_moves_ownership_without_changing_captured_ledger_charge():
    ledger = ChargeLedger({"source": charge(10)})
    source = ResponseOwner()
    source.entries.append((ledger, "source"))

    def reserve(target):
        ledger["copy"] = ledger["source"]
        target.entries.append((ledger, "copy"))
        target.copy_reservations.append(reserve)
        return True

    source.copy_reservations.append(reserve)
    copy = source.reserve_copy()
    check(ledger, {"source": charge(10), "copy": charge(10)})
    consumer = ResponseOwner()
    assert consumer.adopt(copy)
    source.release()
    check(ledger, {"copy": charge(10)})
    consumer.release()
    check(ledger, {})


def test_failed_copy_rolls_back_only_its_already_admitted_reservations():
    ledger = ChargeLedger({"first": charge(10), "second": charge(20)})
    owner = ResponseOwner()
    owner.entries.extend(((ledger, "first"), (ledger, "second")))

    def admit(target):
        ledger["copy"] = ledger["first"]
        target.entries.append((ledger, "copy"))
        return True

    owner.copy_reservations.extend((admit, lambda target: False))
    assert owner.reserve_copy() is None
    check(ledger, {"first": charge(10), "second": charge(20)})
    owner.release()
    check(ledger, {})


def test_merge_and_update_iterables_preserve_overwrite_semantics():
    ledger = ChargeLedger({"owned": charge(10)})
    ledger |= {"owned": charge(20, "bob", "two")}
    ledger.update([("other", charge(30)), ("owned", charge(40))])
    check(ledger, {"owned": charge(40), "other": charge(30)})


def test_failed_key_insertion_restores_all_affected_indexes():
    class FailingKey:
        calls = 0

        def __hash__(self):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("owned insertion failure")
            return 71

    ledger = ChargeLedger({"owned": charge(10)})
    with pytest.raises(RuntimeError, match="owned insertion failure"):
        ledger[FailingKey()] = charge(30, "bob", "two")
    check(ledger, {"owned": charge(10)})
    assert "bob" not in ledger._users and "two" not in ledger._sessions
