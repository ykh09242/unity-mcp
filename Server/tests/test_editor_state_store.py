"""Version, ownership and finite freshness behavior using a real latest-value store."""

from copy import deepcopy

import pytest
from pydantic import JsonValue

from transport.editor_state_store import EditorStateStore


def message(
    *,
    sequence: int = 1,
    observed: int = 100_000,
    epoch: str = "domain-a",
    reload_pending: bool = False,
) -> dict[str, JsonValue]:
    return {
        "type": "editor_state",
        "epoch": epoch,
        "sequence": sequence,
        "observed_at_unix_ms": observed,
        "state": {
            "schema_version": "unity-mcp/editor_state@2",
            "sequence": sequence,
            "observed_at_unix_ms": observed,
            "compilation": {"is_compiling": False, "is_domain_reload_pending": reload_pending},
            "editor": {"active_scene": {"name": "fixture"}},
        },
    }


@pytest.fixture
def store():
    clock = {"wall": 100.0, "mono": 10.0}
    cache = EditorStateStore(monotonic=lambda: clock["mono"], wall_time=lambda: clock["wall"])
    cache.register_session("actual-socket", "alice")
    return cache, clock


def test_initial_state_is_detached_from_sender_and_readers(store):
    # Given a complete state from the authenticated socket.
    cache, _ = store
    payload = message()
    assert cache.accept("actual-socket", payload)
    # When a reader mutates its detached snapshot.
    returned = cache.get("actual-socket", "alice")
    returned["compilation"]["is_compiling"] = True
    payload["state"]["editor"]["active_scene"]["name"] = "mutated"
    # Then subsequent readers receive the original state.
    original = cache.get("actual-socket", "alice")
    assert original["compilation"]["is_compiling"] is False
    assert original["editor"]["active_scene"]["name"] == "fixture"


@pytest.mark.parametrize(
    "session,user", [("unknown", "alice"), ("actual-socket", "bob"), ("actual-socket", None)]
)
def test_read_requires_registered_socket_owner(store, session, user):
    # Given Alice's cached state.
    cache, _ = store
    cache.accept("actual-socket", message())
    # When an unrelated identity attempts the read.
    result = cache.get(session, user)
    # Then no snapshot is disclosed.
    assert result is None


@pytest.mark.parametrize(
    "changes",
    [
        {"sequence": 0},
        {"observed_at_unix_ms": 99_000},
        {"session_id": "another-socket"},
        {"sequence": True},
        {"epoch": ""},
        {"type": "other"},
        {"state": {}},
    ],
)
def test_rejects_invalid_or_out_of_order_snapshot(store, changes):
    # Given a valid accepted watermark.
    cache, _ = store
    cache.accept("actual-socket", message())
    payload = message()
    payload.update(changes)
    # When a conflicting envelope arrives.
    accepted = cache.accept("actual-socket", payload)
    # Then it cannot replace the state.
    assert accepted is False
    assert cache.get("actual-socket", "alice")["sequence"] == 1


def test_newer_snapshot_supersedes_state(store):
    # Given sequence 1 from one registered domain.
    cache, _ = store
    cache.accept("actual-socket", message())
    # When a full newer snapshot arrives.
    accepted = cache.accept("actual-socket", message(sequence=3, observed=100_500))
    # Then readers receive sequence 3.
    assert accepted is True
    assert cache.get("actual-socket", "alice")["sequence"] == 3


def test_same_sequence_changed_content_cannot_be_a_heartbeat(store):
    # Given one accepted state.
    cache, _ = store
    cache.accept("actual-socket", message())
    payload = message(observed=100_500)
    payload["state"]["compilation"]["is_compiling"] = True
    # When content changes without a sequence advance.
    accepted = cache.accept("actual-socket", payload)
    # Then the previous state remains.
    assert accepted is False
    assert cache.get("actual-socket", "alice")["compilation"]["is_compiling"] is False


def test_unchanged_heartbeat_refreshes_observation_liveness(store):
    # Given a nearly expired state.
    cache, clock = store
    cache.accept("actual-socket", message())
    clock.update(wall=101.9, mono=11.9)
    # When the same state is successfully observed again.
    assert cache.accept("actual-socket", message(observed=101_900))
    clock.update(wall=103.0, mono=13.0)
    # Then content remains reusable with its original sequence.
    assert cache.get("actual-socket", "alice")["sequence"] == 1


@pytest.mark.parametrize("wall,mono", [(102.001, 10.0), (100.0, 12.001), (94.9, 10.0)])
def test_expired_receive_or_observation_or_future_clock_requires_rpc(store, wall, mono):
    # Given a valid received state.
    cache, clock = store
    cache.accept("actual-socket", message())
    # When either freshness clock expires or the sender time is implausibly ahead.
    clock.update(wall=wall, mono=mono)
    # Then even an excessive requested TTL cannot serve it.
    assert cache.get("actual-socket", "alice", max_age_s=999_999) is None


def test_duplicate_frame_cannot_keep_dead_editor_fresh(store):
    # Given a valid state whose observation has stopped advancing.
    cache, clock = store
    cache.accept("actual-socket", message())
    clock["mono"] = 11.9
    # When the exact frame is replayed before receive expiry.
    assert cache.accept("actual-socket", deepcopy(message()))
    clock["mono"] = 12.1
    # Then replay has not extended freshness.
    assert cache.get("actual-socket", "alice") is None


def test_reload_invalidates_and_preserves_watermark(store):
    # Given an earlier ready snapshot.
    cache, _ = store
    cache.accept("actual-socket", message())
    # When a domain reload begins.
    assert cache.accept("actual-socket", message(sequence=2, reload_pending=True))
    # Then no ready snapshot survives and replay is rejected.
    assert cache.get("actual-socket", "alice") is None
    assert not cache.accept("actual-socket", message())
    assert not cache.accept("actual-socket", message(sequence=1, epoch="domain-b"))
    assert not cache.accept("actual-socket", message(sequence=3))


def test_changed_epoch_invalidates_previous_ready_snapshot_until_new_connection(store):
    # Given a ready state from the current registered domain.
    cache, _ = store
    cache.accept("actual-socket", message())
    # When another domain attempts to reuse the same socket generation.
    accepted = cache.accept("actual-socket", message(epoch="domain-b"))
    # Then no old ready state remains reusable and re-registration cannot reset it.
    assert accepted is False
    assert cache.get("actual-socket", "alice") is None
    assert cache.register_session("actual-socket", "alice")
    assert not cache.accept("actual-socket", message(sequence=3))


def test_disconnect_removes_and_new_session_allows_sequence_reset(store):
    # Given an old domain with a high sequence.
    cache, _ = store
    cache.accept("actual-socket", message(sequence=99))
    # When the socket closes and a new authenticated connection registers.
    cache.remove_session("actual-socket")
    assert cache.register_session("new-socket", "alice")
    # Then only the new session can start a new epoch/sequence.
    assert cache.get("actual-socket", "alice") is None
    assert not cache.accept("actual-socket", message(sequence=100))
    assert cache.accept("new-socket", message(epoch="domain-b"))
    assert cache.get("new-socket", "alice")["sequence"] == 1


def test_duplicate_registration_cannot_reset_or_transfer_owner(store):
    # Given a populated authenticated slot.
    cache, _ = store
    cache.accept("actual-socket", message(sequence=20))
    # When registration is replayed by the same or another owner.
    assert cache.register_session("actual-socket", "alice")
    assert not cache.register_session("actual-socket", "bob")
    # Then the watermark and owner remain intact.
    assert not cache.accept("actual-socket", message())
    assert cache.get("actual-socket", "bob") is None


def test_store_is_bounded_to_registered_sessions():
    # Given a store with one registration slot.
    cache = EditorStateStore(max_sessions=1)
    cache.register_session("one", None)
    # When another session tries to register or push.
    accepted = cache.register_session("two", None)
    # Then it retains no unsolicited state.
    assert accepted is False
    assert not cache.accept("two", message())
    cache.reset()
    assert cache.register_session("two", None)


@pytest.mark.parametrize("age", [float("nan"), float("inf"), -1.0, 0.0])
def test_nonfinite_or_nonpositive_ttl_never_bypasses_freshness(store, age):
    # Given a valid state and an invalid requested freshness bound.
    cache, _ = store
    cache.accept("actual-socket", message())
    # When the reader supplies the invalid TTL.
    result = cache.get("actual-socket", "alice", max_age_s=age)
    # Then it must use the authoritative fallback.
    assert result is None


@pytest.mark.parametrize(
    "value",
    ["x" * (129 * 1024), [0] * 4097, float("nan")],
    ids=["oversized-string", "wide-list", "nonfinite-number"],
)
def test_state_payload_has_independent_size_and_shape_budget(store, value):
    # Given a registered state channel and an excessive/malformed extension.
    cache, _ = store
    payload = message()
    payload["state"]["extension"] = value
    # When it arrives within a potentially larger WebSocket tool-result budget.
    accepted = cache.accept("actual-socket", payload)
    # Then the state store retains none of it.
    assert accepted is False
    assert cache.get("actual-socket", "alice") is None


@pytest.mark.parametrize(
    "payload,accepted,changed",
    [
        (message(), True, False),
        (message(observed=100_500), True, False),
        (message(sequence=2), True, True),
        (message(sequence=0), False, False),
        (message(epoch="new-domain"), False, True),
        (message(sequence=2, reload_pending=True), True, True),
        ({"type": "editor_state"}, False, False),
    ],
    ids=["duplicate", "heartbeat", "commit", "out-of-order", "epoch", "reload", "malformed"],
)
def test_change_token_only_advances_for_commits_or_invalidation(store, payload, accepted, changed):
    # Given: one valid committed snapshot and its independently observable revision.
    cache, _ = store
    assert cache.accept("actual-socket", message()) is True
    before = cache.change_token("actual-socket")
    # When: an incoming frame is accepted or rejected under the existing boolean contract.
    result = cache.accept("actual-socket", payload)
    # Then: duplicate/heartbeat/rejected frames cannot trigger a resource invalidation.
    assert result is accepted
    assert (cache.change_token("actual-socket") != before) is changed


def test_change_tokens_remain_bounded_and_reset_only_for_a_new_slot(store):
    # Given: one registered slot, with no arbitrary-session retained revision.
    cache, _ = store
    assert cache.change_token("unknown") is None
    assert cache.change_token("actual-socket") == 0
    cache.accept("actual-socket", message())
    # When: the connection is removed and a new generation registers.
    cache.remove_session("actual-socket")
    cache.register_session("replacement", "alice")
    # Then: no old revision remains and sequence zero is valid in the new generation.
    assert cache.change_token("actual-socket") is None
    assert cache.change_token("replacement") == 0
    assert cache.accept("replacement", message(sequence=0, epoch="replacement")) is True
    cache.reset()
    assert not cache._changes


def test_heartbeats_reuse_detached_content_without_redumping(store, monkeypatch):
    from transport.editor_state_store import PushedState

    cache, clock = store
    dumps = []
    original = PushedState.model_dump

    def tracked_dump(self, *args, **kwargs):
        dumps.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PushedState, "model_dump", tracked_dump)
    payload = message()
    payload["state"]["extension"] = [{"name": str(i), "values": [i, i + 1]} for i in range(200)]
    assert cache.accept("actual-socket", payload)
    initial_content = cache._states["actual-socket"].state["extension"]
    for i in range(1, 101):
        heartbeat = deepcopy(payload)
        heartbeat["observed_at_unix_ms"] += i
        heartbeat["state"]["observed_at_unix_ms"] += i
        clock["wall"] = 100.0 + i / 1000
        clock["mono"] += 0.001
        assert cache.accept("actual-socket", heartbeat)
        heartbeat["state"]["extension"][0]["values"][0] = -1
    assert len(dumps) == 1
    assert cache._states["actual-socket"].state["extension"] is initial_content
    snapshot = cache.get("actual-socket", "alice")
    assert snapshot["observed_at_unix_ms"] == 100_100
    assert snapshot["extension"][0]["values"][0] == 0
    snapshot["extension"][0]["values"][0] = -2
    assert cache.get("actual-socket", "alice")["extension"][0]["values"][0] == 0


@pytest.mark.parametrize("field", ["sequence", "observed_at_unix_ms"])
def test_equivalent_heartbeat_values_still_require_strict_validation(store, field):
    cache, _ = store
    payload = message(sequence=1, observed=1)
    assert cache.accept("actual-socket", payload)
    payload[field] = True
    payload["state"][field] = True
    assert not cache.accept("actual-socket", payload)
