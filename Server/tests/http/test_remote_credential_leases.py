"""Connected authorization uses real service state and event-controlled I/O."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from tests.timing import await_completion, await_signal, watchdog_timeout
from core.constants import API_KEY_HEADER
from services.api_key_service import ApiKeyService, ValidationResult
from services.credential_lease import CredentialLeaseError, current_credential_lease
from transport.remote_auth_middleware import RemoteControlAuthMiddleware


@asynccontextmanager
async def credential_service(monkeypatch, *, ttl=10, max_age=30):
    """Mock only the external validator and clock; all lease state is real."""
    now = [0.0]
    calls = []
    verdicts = {}
    service = ApiKeyService("https://fixture.invalid/validate", ttl, max_auth_age=max_age)
    monkeypatch.setattr(service, "_now", lambda: now[0])

    async def validate(key):
        calls.append(key)
        value = verdicts.get(key, "alice")
        return ValidationResult(valid=value is not None, user_id=value)

    monkeypatch.setattr(service, "_validate_external", validate)
    try:
        yield SimpleNamespace(service=service, now=now, calls=calls, verdicts=verdicts)
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl,max_age,deadline", [(10, 30, 10), (100, 30, 30), (0, 30, 30)])
async def test_authentication_age_is_bounded_without_changing_ttl_zero(
    monkeypatch, ttl, max_age, deadline
):
    # Given an original definitive authentication at monotonic zero.
    async with credential_service(monkeypatch, ttl=ttl, max_age=max_age) as fixture:
        first = await fixture.service.validate("fixture-key")
        lease = first.lease
        assert lease is not None
        fixture.now[0] = deadline - 1
        # When another caller authenticates near the original deadline.
        second = await fixture.service.validate("fixture-key")
        # Then a cache hit cannot slide age; TTL0 still performs fresh validation.
        assert len(fixture.calls) == (2 if ttl == 0 else 1)
        assert second.lease is lease
        assert fixture.service._lease_records[lease.generation].deadline == (
            fixture.now[0] + deadline if ttl == 0 else deadline
        )


@pytest.mark.asyncio
async def test_expired_lease_parks_all_consumers_behind_one_fresh_validation(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        lease = (await fixture.service.validate("fixture-key")).lease
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused(key):
            entered.set()
            await release.wait()
            return ValidationResult(valid=True, user_id="alice")

        monkeypatch.setattr(fixture.service, "_validate_external", paused)
        fixture.now[0] = 10
        first = asyncio.create_task(lease.check())
        await entered.wait()
        second = asyncio.create_task(lease.check())
        assert not first.done() and not second.done()
        assert fixture.service._validation_waiters == 1
        release.set()
        await asyncio.gather(first, second)
        assert lease.check_now() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", [None, "bob", ""])
async def test_renewal_cannot_change_original_principal(monkeypatch, verdict):
    async with credential_service(monkeypatch) as fixture:
        lease = (await fixture.service.validate("fixture-key")).lease
        fixture.verdicts["fixture-key"] = verdict
        fixture.now[0] = 10
        with pytest.raises(CredentialLeaseError):
            await lease.check()
        with pytest.raises(CredentialLeaseError):
            lease.check_now()


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_all", [False, True])
async def test_paused_positive_cannot_cross_invalidation_fence(monkeypatch, clear_all):
    async with credential_service(monkeypatch) as fixture:
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused(key):
            entered.set()
            await release.wait()
            return ValidationResult(valid=True, user_id="alice")

        monkeypatch.setattr(fixture.service, "_validate_external", paused)
        pending = asyncio.create_task(fixture.service.validate("fixture-key"))
        await entered.wait()
        if clear_all:
            await fixture.service.clear_cache()
        else:
            await fixture.service.invalidate_cache("fixture-key")
        release.set()
        result = await pending
        assert not result.valid and result.lease is None
        assert "fixture-key" not in fixture.service._cache
        # Invalidation requires new authentication rather than permanent revocation.
        assert (await fixture.service.validate("fixture-key")).lease is not None


@pytest.mark.asyncio
async def test_renewal_timeout_retires_before_noncooperative_work_exits(monkeypatch):
    import services.api_key_service as service_module

    async with credential_service(monkeypatch) as fixture:
        lease = (await fixture.service.validate("fixture-key")).lease
        entered, release = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(fixture.service, "REVALIDATION_TIMEOUT", watchdog_timeout())
        timeouts = []

        async def expire_after_entry(tasks, *, timeout):
            timeouts.append(timeout)
            await await_signal(entered, "renewal validator", producers=tasks)
            # Expire asyncio.wait's real timer after the cancellation-resistant
            # validator starts, rather than timing its admission on a busy runner.
            return await asyncio.wait(tasks, timeout=0)

        local_asyncio = SimpleNamespace(**vars(asyncio))
        local_asyncio.wait = expire_after_entry
        monkeypatch.setattr(service_module, "asyncio", local_asyncio)

        async def resistant(key):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return ValidationResult(valid=True, user_id="alice")

        monkeypatch.setattr(fixture.service, "_validate_external", resistant)
        fixture.now[0] = 10
        checking = asyncio.create_task(lease.check())
        try:
            await await_signal(entered, "renewal validator", producers=(checking,))
            with pytest.raises(CredentialLeaseError):
                await await_completion(checking, "expired renewal")
            assert len(timeouts) == 1
            assert 0 < timeouts[0] <= fixture.service.REVALIDATION_TIMEOUT
            assert len(fixture.service._inflight) == 1
            assert not (await fixture.service.validate("fixture-key")).valid
        finally:
            release.set()
            if not checking.done():
                checking.cancel()
            await await_completion(
                asyncio.gather(
                    checking,
                    *(f.task for f in fixture.service._inflight.values()),
                    return_exceptions=True,
                ),
                "renewal provider teardown",
            )


@pytest.mark.asyncio
async def test_retired_owner_retains_capacity_until_actual_exit(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        fixture.service.MAX_LEASE_OWNERS = 1
        first = (await fixture.service.validate("fixture-key")).lease
        other = (await fixture.service.validate("other-key")).lease
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def owner():
            async with first.own():
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()

        task = asyncio.create_task(owner())
        await entered.wait()
        await fixture.service.invalidate_cache("fixture-key")
        await cancelled.wait()
        try:
            with pytest.raises(CredentialLeaseError):
                async with other.own():
                    pytest.fail("Retired work must still own its admission")
            assert fixture.service._lease_owners == 1
        finally:
            release.set()
            await task
        assert fixture.service._lease_owners == 0
        async with other.own():
            assert current_credential_lease() is other


def request_scope(key):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "path": "/mcp",
        "headers": [(API_KEY_HEADER.lower().encode(), key.encode())],
        "client": ("127.0.0.1", 1234),
        "state": {},
    }


@pytest.mark.asyncio
async def test_revocation_during_receive_cannot_dispatch_or_affect_other_credentials(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        entered, resume = asyncio.Event(), asyncio.Event()
        dispatched, sent = [], []

        async def app(scope, receive, send):
            await receive()
            dispatched.append(current_credential_lease().user_id)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def blocked_receive():
            entered.set()
            try:
                await resume.wait()
            except asyncio.CancelledError:
                await resume.wait()
            return {"type": "http.request", "body": b"{}"}

        async def immediate_receive():
            return {"type": "http.request", "body": b"{}"}

        async def send(message):
            sent.append(message)

        middleware = RemoteControlAuthMiddleware(app)
        stopped = asyncio.create_task(middleware(request_scope("alice-key"), blocked_receive, send))
        await entered.wait()
        fixture.verdicts["bob-key"] = "bob"
        await fixture.service.invalidate_cache("alice-key")
        resume.set()
        await stopped
        assert dispatched == []
        assert not any(m.get("status") == 200 for m in sent)
        for key in ["alice-other-key", "bob-key"]:
            await middleware(request_scope(key), immediate_receive, send)
        assert dispatched == ["alice", "bob"]


@pytest.mark.asyncio
async def test_after_response_start_authentication_loss_cannot_emit_second_start(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        started, release = asyncio.Event(), asyncio.Event()
        sent = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            await send({"type": "http.response.body", "body": b"completed"})

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            sent.append(message)

        task = asyncio.create_task(
            RemoteControlAuthMiddleware(app)(request_scope("key"), receive, send)
        )
        await started.wait()
        await fixture.service.invalidate_cache("key")
        release.set()
        await task
        assert [m["type"] for m in sent] == ["http.response.start"]


@pytest.mark.asyncio
async def test_service_replacement_fences_old_generation_without_closing_new_service(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        lease = (await fixture.service.validate("key")).lease
        replacement = ApiKeyService("https://replacement.invalid/validate")
        try:
            with pytest.raises(CredentialLeaseError):
                await lease.check()
            await fixture.service.aclose()
            assert ApiKeyService.get_instance() is replacement
            assert not replacement._closed
        finally:
            await replacement.aclose()


@pytest.mark.parametrize(
    "ttl,max_age",
    [
        (-1, 10),
        (float("nan"), 10),
        (float("inf"), 10),
        (0, 0),
        (1, -1),
        (1, float("nan")),
        (1, float("inf")),
    ],
)
def test_configuration_rejects_nonfinite_or_nonpositive_authorization_age(ttl, max_age):
    with pytest.raises(ValueError):
        ApiKeyService("https://fixture.invalid/validate", ttl, max_auth_age=max_age)


@pytest.mark.asyncio
async def test_invalidation_wakes_parked_consumers_before_provider_cleanup(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        lease = (await fixture.service.validate("key")).lease
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused(key):
            entered.set()
            await release.wait()
            return ValidationResult(valid=True, user_id="alice")

        monkeypatch.setattr(fixture.service, "_validate_external", paused)
        fixture.now[0] = 10
        waiter = asyncio.create_task(lease.check())
        await entered.wait()
        try:
            await fixture.service.invalidate_cache("key")
            with pytest.raises(CredentialLeaseError):
                await waiter
            assert fixture.service._inflight
        finally:
            release.set()


@pytest.mark.asyncio
async def test_lease_capacity_is_retained_by_live_generations_only(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        monkeypatch.setattr(fixture.service, "MAX_ACTIVE_LEASES", 1)
        old = (await fixture.service.validate("old")).lease
        refused = await fixture.service.validate("new")
        assert not refused.valid and refused.overloaded
        await fixture.service.invalidate_cache("old")
        fresh = await fixture.service.validate("new")
        assert fresh.valid and fresh.lease is not old
        assert len(fixture.service._lease_records) == 1


@pytest.mark.asyncio
async def test_per_lease_owner_cap_does_not_charge_nested_same_task_twice(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        fixture.service.MAX_OWNERS_PER_LEASE = 1
        lease = (await fixture.service.validate("key")).lease
        async with lease.own():
            async with lease.own():
                assert fixture.service._lease_owners == 1

            async def another_owner():
                async with lease.own():
                    pytest.fail("A second actual owner must be refused")

            with pytest.raises(CredentialLeaseError) as refused:
                await asyncio.create_task(another_owner())
            assert refused.value.overloaded
        assert fixture.service._lease_owners == 0


@pytest.mark.asyncio
async def test_replacement_cannot_reset_global_owner_charge(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        old = (await fixture.service.validate("old")).lease
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def resistant_owner():
            async with old.own():
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()

        task = asyncio.create_task(resistant_owner())
        await entered.wait()
        replacement = ApiKeyService("https://replacement.invalid/validate")
        replacement.MAX_LEASE_OWNERS = 1

        async def valid(key):
            return ValidationResult(valid=True, user_id="alice")

        monkeypatch.setattr(replacement, "_validate_external", valid)
        try:
            await cancelled.wait()
            fresh = (await replacement.validate("new")).lease
            with pytest.raises(CredentialLeaseError):
                async with fresh.own():
                    pytest.fail("The old service still owns its actual task")
            release.set()
            await task
            async with fresh.own():
                assert replacement._lease_owners == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await replacement.aclose()


@pytest.mark.asyncio
async def test_renewal_capacity_refusal_retires_existing_authorization(monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        lease = (await fixture.service.validate("key")).lease
        fixture.now[0] = 10
        fixture.service.MAX_INFLIGHT_VALIDATIONS = 0
        with pytest.raises(CredentialLeaseError):
            await lease.check()
        assert lease.retired
        assert "key" not in fixture.service._cache


@pytest.mark.asyncio
async def test_service_validation_and_renewal_do_not_capture_request_context(monkeypatch):
    marker = ContextVar("fixture_request_context", default=None)
    async with credential_service(monkeypatch) as fixture:
        observed = []

        async def validate(key):
            observed.append(marker.get())
            return ValidationResult(valid=True, user_id="alice")

        monkeypatch.setattr(fixture.service, "_validate_external", validate)
        token = marker.set("private-request-marker")
        try:
            lease = (await fixture.service.validate("key")).lease
            fixture.now[0] = 10
            await lease.check()
        finally:
            marker.reset(token)
        assert observed == [None, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "websocket"])
async def test_retired_asgi_owner_remains_charged_through_held_policy_send(monkeypatch, protocol):
    # Given: an authenticated app that has not started a protocol response.
    async with credential_service(monkeypatch) as fixture:
        fixture.service.MAX_LEASE_OWNERS = 1
        fixture.service.MAX_OWNERS_PER_LEASE = 1
        first = (await fixture.service.validate("first-key")).lease
        second = (await fixture.service.validate("second-key")).lease
        record = fixture.service._lease_records[first.generation]
        entered, policy, cancelled, release = (asyncio.Event() for _ in range(4))
        messages = []

        async def app(scope, receive, send):
            assert current_credential_lease() is first
            entered.set()
            await asyncio.Event().wait()

        async def receive():
            pytest.fail("Policy response does not need request body input")

        async def send(message):
            messages.append(message)
            if not policy.is_set():
                policy.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()

        scope = {**request_scope("first-key"), "type": protocol}
        task = asyncio.create_task(RemoteControlAuthMiddleware(app)(scope, receive, send))
        try:
            await entered.wait()
            # When: revocation enters a policy writer which resists task cancellation.
            await fixture.service.invalidate_cache("first-key")
            await policy.wait()
            task.cancel()
            await cancelled.wait()
            assert not task.done()
            assert fixture.service._lease_owners == 1
            assert list(record.owners.values()) == [task]
            with pytest.raises(CredentialLeaseError) as refused:
                async with second.own():
                    pytest.fail("The original ASGI task still owns capacity")
            assert refused.value.overloaded
        finally:
            release.set()
            outcomes = await asyncio.gather(task, return_exceptions=True)
        # Then: only actual policy-send completion releases the retired owner.
        assert outcomes == [None]
        assert not record.owners and fixture.service._lease_owners == 0
        assert messages[0]["type"] == (
            "http.response.start" if protocol == "http" else "websocket.close"
        )
        assert messages[0].get("status", messages[0].get("code")) == (
            401 if protocol == "http" else 1008
        )
        async with second.own():
            assert fixture.service._lease_owners == 1


@pytest.mark.asyncio
async def test_live_asgi_stream_parks_and_resumes_after_successful_renewal(monkeypatch):
    # Given: one authenticated HTTP stream with its first chunk already delivered.
    async with credential_service(monkeypatch) as fixture:
        initial_sent, next_chunk = asyncio.Event(), asyncio.Event()
        renewing, renewal_release = asyncio.Event(), asyncio.Event()
        final_writer, final_release = asyncio.Event(), asyncio.Event()
        captured, messages = [], []
        app_entries = 0
        renewal_calls = 0

        async def app(scope, receive, send):
            nonlocal app_entries
            app_entries += 1
            captured.append(current_credential_lease())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"initial", "more_body": True})
            initial_sent.set()
            await next_chunk.wait()
            await send({"type": "http.response.body", "body": b"renewed", "more_body": True})
            assert current_credential_lease() is captured[0]
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def receive():
            pytest.fail("This streaming response does not consume a request body")

        async def send(message):
            if message["type"] == "http.response.body" and not message["more_body"]:
                final_writer.set()
                await final_release.wait()
            messages.append(message)

        task = asyncio.create_task(
            RemoteControlAuthMiddleware(app)(request_scope("stream-key"), receive, send)
        )
        try:
            await initial_sent.wait()
            lease = captured[0]
            record = fixture.service._lease_records[lease.generation]
            original_validator = fixture.service._validate_external

            async def paused_validation(key):
                nonlocal renewal_calls
                renewal_calls += 1
                renewing.set()
                await renewal_release.wait()
                return await original_validator(key)

            monkeypatch.setattr(fixture.service, "_validate_external", paused_validation)
            # When: the next guarded stream write crosses the original auth deadline.
            fixture.now[0] = 10
            next_chunk.set()
            await renewing.wait()
            assert not task.done() and not final_writer.is_set()
            assert renewal_calls == 1 and fixture.calls == ["stream-key"]
            assert [message.get("body") for message in messages] == [None, b"initial"]
            assert fixture.service._lease_owners == 1
            assert list(record.owners.values()) == [task]

            renewal_release.set()
            await final_writer.wait()
            # Then: the same stream/lease resumes, retaining ownership through its writer.
            assert not task.done() and app_entries == 1
            assert fixture.calls == ["stream-key", "stream-key"] and renewal_calls == 1
            assert fixture.service._lease_records[lease.generation] is record
            assert record.deadline == 20 and lease.check_now() is None
            assert [message.get("body") for message in messages] == [None, b"initial", b"renewed"]
            assert fixture.service._lease_owners == 1
            assert list(record.owners.values()) == [task]
        finally:
            next_chunk.set()
            renewal_release.set()
            final_release.set()
            outcomes = await asyncio.gather(task, return_exceptions=True)
        assert outcomes == [None]
        assert app_entries == 1 and not record.owners and fixture.service._lease_owners == 0
        assert [message["type"] for message in messages] == [
            "http.response.start",
            "http.response.body",
            "http.response.body",
            "http.response.body",
        ]
        assert [message["status"] for message in messages if "status" in message] == [200]
        assert [message.get("body") for message in messages] == [None, b"initial", b"renewed", b""]
        assert messages[-1]["more_body"] is False
