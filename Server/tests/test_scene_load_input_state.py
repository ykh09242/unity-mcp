"""Input nonces are scoped, bounded, expiring one-attempt claims."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace

import pytest


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def scope():
    from transport.scene_load_inputs import SceneScope

    return SceneScope("hosted:v1:owner", "A@a", "a", "session-a", 1, "generation-a")


def test_nonce_claim_is_single_use_after_completion():
    # Given: one pending input under an original fixed deadline.
    from transport.scene_load_inputs import InputLedger

    ledger = InputLedger(clock=Clock())
    nonce = ledger.issue(scope())
    # When: the accepted continuation claims and then completes.
    assert ledger.claim(nonce, scope()) is True
    ledger.finish(nonce)
    # Then: a retry never admits another continuation, whatever the original outcome.
    assert ledger.claim(nonce, scope()) is False
    assert ledger.record_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", "other"),
        ("instance", "B@b"),
        ("project_hash", "b"),
        ("session_id", "other"),
        ("socket_id", 2),
        ("generation", "other"),
    ],
)
def test_each_trusted_scope_dimension_is_rechecked(field, value):
    # Given: a nonce admitted for one immutable selected identity.
    from mcp.shared.exceptions import MCPError
    from transport.scene_load_inputs import InputLedger

    ledger = InputLedger(clock=Clock())
    nonce = ledger.issue(scope())
    # When/Then: another owner/target/socket generation cannot consume it.
    with pytest.raises(MCPError) as error:
        ledger.claim(nonce, replace(scope(), **{field: value}))
    assert error.value.error.code == -32602
    assert ledger.claim(nonce, scope()) is True


def test_prompt_lookup_does_not_refresh_the_original_deadline():
    # Given: a pending prompt whose deadline is exactly 120 seconds after issuance.
    from mcp.shared.exceptions import MCPError
    from transport.scene_load_inputs import InputLedger

    clock = Clock()
    ledger = InputLedger(clock=clock)
    nonce = ledger.issue(scope())
    clock.now += 119
    ledger.lookup(nonce, scope())
    # When: another missing-answer round crosses the original deadline.
    clock.now += 1
    # Then: it expires even if the SDK could reseal a new envelope for a round.
    with pytest.raises(MCPError):
        ledger.lookup(nonce, scope())
    assert ledger.record_count == 0


def test_active_expired_claim_retains_capacity_until_actual_finally():
    # Given: an active continuation with the only available record charge.
    from transport.scene_load_inputs import InputCapacityError, InputLedger

    clock = Clock()
    ledger = InputLedger(clock=clock, total=1)
    nonce = ledger.issue(scope())
    assert ledger.claim(nonce, scope())
    # When: expiry occurs while the real continuation has not exited.
    clock.now += 120
    # Then: capacity stays charged until the actual finish callback.
    with pytest.raises(InputCapacityError):
        ledger.issue(scope())
    assert ledger.record_count == 1
    ledger.finish(nonce)
    assert ledger.record_count == 0
    assert ledger.issue(scope())


def test_principal_and_total_admission_refuse_without_evicting_live_prompts():
    # Given: small deterministic versions of the same production count limits.
    from transport.scene_load_inputs import InputCapacityError, InputLedger

    ledger = InputLedger(clock=Clock(), total=2, principal=1)
    alice = ledger.issue(scope())
    with pytest.raises(InputCapacityError):
        ledger.issue(scope())
    bob_scope = replace(scope(), owner="bob")
    bob = ledger.issue(bob_scope)
    # When/Then: neither per-owner nor total refusal evicts another valid prompt.
    with pytest.raises(InputCapacityError):
        ledger.issue(replace(scope(), owner="third"))
    assert ledger.claim(alice, scope())
    assert ledger.claim(bob, bob_scope)


def test_close_refuses_new_work_and_keeps_active_ownership():
    # Given: one active accepted continuation and another pending prompt.
    from mcp.shared.exceptions import MCPError
    from transport.scene_load_inputs import InputCapacityError, InputLedger

    ledger = InputLedger(clock=Clock())
    active = ledger.issue(scope())
    ledger.issue(scope())
    assert ledger.claim(active, scope())
    # When: the runtime closes while the accepted ordinary handler still owns work.
    ledger.close()
    # Then: no new input/continuation is admitted and active ownership remains until exit.
    assert ledger.record_count == 1
    with pytest.raises(InputCapacityError):
        ledger.issue(scope())
    with pytest.raises(MCPError):
        ledger.claim(active, scope())
    ledger.finish(active)
    assert ledger.record_count == 0


def test_concurrent_retry_claims_are_atomic():
    # Given: one nonce, with two genuine concurrent callers released together.
    from transport.scene_load_inputs import InputLedger

    ledger = InputLedger(clock=Clock())
    nonce = ledger.issue(scope())
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=3)
        return ledger.claim(nonce, scope())

    # When: both try to admit the continuation.
    with ThreadPoolExecutor(max_workers=2) as executor:
        answers = list(executor.map(lambda _: claim(), range(2)))
    # Then: precisely one caller wins, and no terminal outcome is invented.
    assert sorted(answers) == [False, True]


def test_scope_storage_is_admitted_before_a_record_is_reserved():
    # Given: an oversized trusted-identity projection, not a request-driven result graph.
    from transport.scene_load_inputs import InputCapacityError, InputLedger

    ledger = InputLedger(clock=Clock())
    # When/Then: refusal leaves no partial charge or usable nonce.
    with pytest.raises(InputCapacityError):
        ledger.issue(replace(scope(), instance="x" * 5000))
    assert ledger.record_count == 0


@pytest.mark.parametrize(
    "hosted,state",
    [
        (True, {}),
        (True, {"user_id": "client-claim"}),
        (False, {}),
        (False, {"unity_mcp_authenticated_local_control": "true"}),
    ],
)
def test_principal_binding_requires_server_authentication_state(hosted, state):
    # Given: request metadata that is not the exact trusted server-auth state.
    from mcp.shared.exceptions import MCPError
    from transport.scene_load_inputs import SceneLoadInputs

    runtime = SceneLoadInputs(remote_hosted=hosted)
    context = SimpleNamespace(request=SimpleNamespace(scope={"state": state}))
    # When/Then: neither client user metadata nor a truthy local marker grants ownership.
    with pytest.raises(MCPError):
        runtime.principal(context)
