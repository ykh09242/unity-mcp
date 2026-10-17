"""Credential boundaries compose with real Hub/read/ASGI state without a native Editor."""

import asyncio
import importlib
import json
import time
from contextlib import asynccontextmanager
from contextvars import Context
from types import SimpleNamespace

import anyio
import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute

from core.config import config
from services.credential_lease import bind_credential_lease
from services.tools.shared_tool_reads import SharedToolReads
from tests.http.test_remote_credential_leases import credential_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE, RemoteControlAuthMiddleware
from transport.unity_transport import send_with_unity_instance


@pytest.mark.asyncio
async def test_same_principal_distinct_credentials_cannot_join_a_shared_flight(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    async with credential_service(monkeypatch) as fixture:
        first = (await fixture.service.validate("first-key")).lease
        second = (await fixture.service.validate("second-key")).lease
        reads = SharedToolReads()
        entered = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        calls = []

        async def caller(lease, index):
            async def fetch():
                calls.append(index)
                entered[index].set()
                await release.wait()
                return {"caller": index}

            async with lease.own():
                async with reads.session(("same-user", "same-editor")) as read:
                    return await read.fetch(fetch)

        a = asyncio.create_task(caller(first, 0))
        b = asyncio.create_task(caller(second, 1))
        try:
            await asyncio.gather(*(event.wait() for event in entered))
            await fixture.service.invalidate_cache("first-key")
            release.set()
            results = await asyncio.gather(a, b, return_exceptions=True)
            assert isinstance(results[0], asyncio.CancelledError)
            assert results[1] == {"caller": 1}
            assert sorted(calls) == [0, 1]
        finally:
            release.set()
            await asyncio.gather(a, b, return_exceptions=True)


@pytest.mark.asyncio
async def test_batch_scalar_cache_is_partitioned_before_any_shared_read(monkeypatch):
    batch = importlib.import_module("services.tools.batch_execute")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(batch, "_cached_max_commands", {})
    monkeypatch.setattr(batch, "_limit_reads", SharedToolReads())
    async with credential_service(monkeypatch) as fixture:
        leases = [(await fixture.service.validate(key)).lease for key in ["one", "two"]]
        calls = []

        async def state(_name):
            return "alice" if _name == "user_id" else "session"

        async def send(*_args, **_kwargs):
            calls.append(1)
            return {
                "success": True,
                "data": {
                    "settings": {
                        "batch_execute_max_commands": 20 + len(calls),
                    }
                },
            }

        monkeypatch.setattr(batch, "send_with_unity_instance", send)
        values = []
        for lease in leases:
            async with lease.own():
                values.append(
                    await batch._get_max_commands_from_editor_state(
                        SimpleNamespace(get_state=state), "Fixture@hash"
                    )
                )
        assert values == [21, 22]
        assert len(calls) == 2


async def remote_hub_credentials(command_hub, monkeypatch, service):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    requester = (await service.validate("request-key")).lease
    plugin = (await service.validate("plugin-key")).lease
    command_hub.websocket.state.user_id = "alice"
    setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
    return requester, plugin


@pytest.mark.asyncio
async def test_invalid_requester_has_zero_dispatch_even_with_explicit_principal(
    command_hub, monkeypatch
):
    async with credential_service(monkeypatch) as fixture:
        requester, _ = await remote_hub_credentials(command_hub, monkeypatch, fixture.service)
        await fixture.service.invalidate_cache("request-key")
        with bind_credential_lease(requester):
            result = await send_with_unity_instance(
                None, "Fixture@hash", "manage_scene", {}, user_id="alice"
            )
        assert result["error"] == "auth_required"
        assert command_hub.sent == []


@pytest.mark.asyncio
async def test_plugin_lease_loss_after_send_preserves_unknown_outcome(command_hub, monkeypatch):
    async with credential_service(monkeypatch) as fixture:
        requester, _ = await remote_hub_credentials(command_hub, monkeypatch, fixture.service)
        entered, release = asyncio.Event(), asyncio.Event()

        async def suspended_send(message):
            command_hub.sent.append(message)
            if message["type"] == "execute":
                entered.set()
                await release.wait()

        command_hub.websocket.send_json = suspended_send
        with bind_credential_lease(requester):
            command = asyncio.create_task(PluginHub.send_command("session", "manage_scene", {}))
        await entered.wait()
        await fixture.service.invalidate_cache("plugin-key")
        release.set()
        result = await command
        assert result["data"]["reason"] == "outcome_unknown"
        assert result["hint"] == "inspect_state_before_retry"
        assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1
        assert PluginHub._pending == {}


@asynccontextmanager
async def connected_plugin(app, key, project):
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()
    await incoming.put({"type": "websocket.connect"})

    async def receive():
        return await incoming.get()

    async def send(message):
        await outgoing.put(message)

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "path": "/hub/plugin",
        "raw_path": b"/hub/plugin",
        "scheme": "ws",
        "query_string": b"",
        "headers": [(b"x-api-key", key.encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("fixture", 80),
        "subprotocols": [],
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        assert (await outgoing.get())["type"] == "websocket.accept"
        assert json.loads((await outgoing.get())["text"])["type"] == "welcome"
        await incoming.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "register",
                        "project_hash": project,
                        "project_name": project,
                    }
                ),
            }
        )
        registration = json.loads((await outgoing.get())["text"])
        assert registration["type"] == "registered"
        yield task, incoming, outgoing, registration
    finally:
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_remote_socket_revocation_preserves_replacement_and_other_tenant(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    for name in ["_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted"]:
        monkeypatch.setattr(PluginHub, name, {})
    registry = PluginRegistry()
    PluginHub.configure(registry)
    app = RemoteControlAuthMiddleware(Starlette(routes=[WebSocketRoute("/hub/plugin", PluginHub)]))
    async with credential_service(monkeypatch) as fixture:
        fixture.verdicts["bob-key"] = "bob"
        try:
            async with connected_plugin(app, "old-alice-key", "same") as old:
                async with connected_plugin(app, "bob-key", "same") as bob:
                    async with connected_plugin(app, "new-alice-key", "same") as replacement:
                        current = dict(PluginHub._connections)
                        await fixture.service.invalidate_cache("old-alice-key")
                        await asyncio.gather(old[0], return_exceptions=True)
                        assert PluginHub._connections == current
                        assert not bob[0].done() and not replacement[0].done()
                        alice_sessions = await registry.list_sessions(user_id="alice")
                        bob_sessions = await registry.list_sessions(user_id="bob")
                        assert set(alice_sessions) == {replacement[3]["session_id"]}
                        assert set(bob_sessions) == {bob[3]["session_id"]}
                        sessions = {**alice_sessions, **bob_sessions}
                        assert sorted(s.user_id for s in sessions.values()) == ["alice", "bob"]
        finally:
            await PluginHub.shutdown()


@pytest.mark.asyncio
async def test_detached_wait_keeps_private_lease_and_stops_future_polls(monkeypatch, tmp_path):
    from services.credential_lease import require_credential_lease
    from services.test_wait_journal import DeadlineCheckpoint, WaitJournal, WaitOperation
    from transport.test_job_tasks import CapturedWait, TestJobTasks, WaitAdmission

    async with credential_service(monkeypatch) as fixture:
        lease = (await fixture.service.validate("fixture-private-key")).lease
        entered, release = asyncio.Event(), asyncio.Event()
        polls = []

        async def execute(captured):
            assert await require_credential_lease() is lease
            polls.append("get_test_job")
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass  # Model an already-running poll completing despite cancellation.
            await require_credential_lease()
            polls.append("get_test_job")
            return {"result": {"status": "running"}}

        async def unused(*_args):
            pytest.fail("Direct worker scenario does not recapture request context")

        now, wall = asyncio.get_running_loop().time(), time.time()
        operation = WaitOperation(
            owner="hosted:v1:alice",
            user_id="alice",
            project="hash",
            instance="Fixture@hash",
            session_id="session",
            job_id="owned",
            wait=DeadlineCheckpoint.capture(1, now=wall),
            outer=DeadlineCheckpoint.capture(2, now=wall),
        )
        captured = CapturedWait(operation, now + 1, now + 2, {}, credential_lease=lease)
        journal = WaitJournal(tmp_path / "private-waits")
        admission = WaitAdmission()
        runtime = TestJobTasks(
            journal, capture=unused, execute=execute, owner=unused, admission=admission
        )
        runtime._closed = False
        record = journal.create(operation)
        slot = admission.acquire(operation)
        runtime._scopes[record.task_id] = anyio.CancelScope()
        worker = asyncio.create_task(
            runtime._run(record.task_id, captured, slot), context=Context()
        )
        runtime._workers[record.task_id] = worker
        try:
            await entered.wait()
            await fixture.service.invalidate_cache("fixture-private-key")
            release.set()
            await worker
            stored = journal.get(record.task_id, "hosted:v1:alice")
            assert stored.status == "failed"
            assert polls == ["get_test_job"]
            assert admission.active == {}
            assert fixture.service._lease_owners == 0
            durable = json.dumps(stored.operation.model_dump(mode="json"))
            assert "fixture-private-key" not in durable
            assert "credential_lease" not in stored.operation.model_dump()
        finally:
            release.set()
            await asyncio.gather(worker, return_exceptions=True)
            await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,remote", [("stdio", False), ("stdio", True), ("http", False)])
async def test_local_and_stdio_shared_reads_keep_existing_uncredentialed_keys(
    monkeypatch, mode, remote
):
    monkeypatch.setattr(config, "transport_mode", mode)
    monkeypatch.setattr(config, "http_remote_hosted", remote)
    reads = SharedToolReads()
    calls = []

    async def fetch():
        calls.append(1)
        return {"value": 42}

    async with reads.session("existing-key") as first:
        async with reads.session("existing-key") as second:
            assert first is second
            assert await first.fetch(fetch) == {"value": 42}
            assert await second.fetch(fetch) == {"value": 42}
    assert calls == [1, 1]  # Existing freshness=0 still fetches anew after completion.


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["retirement", "disconnect"])
async def test_standalone_plugin_retains_owner_through_cancel_resistant_disconnect(
    monkeypatch, ending
):
    from services.credential_lease import CredentialLeaseError

    # Given: a real authenticated standalone route, without the outer auth middleware.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    for name in ["_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted"]:
        monkeypatch.setattr(PluginHub, name, {})
    registry = PluginRegistry()
    PluginHub.configure(registry)
    app = Starlette(routes=[WebSocketRoute("/hub/plugin", PluginHub)])
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    actual_disconnect = PluginHub.on_disconnect
    captured = []

    async def held_disconnect(endpoint, websocket, close_code):
        captured.append((PluginHub._registry, websocket))
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        await actual_disconnect(endpoint, websocket, close_code)

    monkeypatch.setattr(PluginHub, "on_disconnect", held_disconnect)
    async with credential_service(monkeypatch) as fixture:
        fixture.service.MAX_LEASE_OWNERS = 1
        fixture.service.MAX_OWNERS_PER_LEASE = 1
        second = (await fixture.service.validate("second-key")).lease
        try:
            async with connected_plugin(app, "standalone-key", "same") as peer:
                task = peer[0]
                websocket = next(iter(PluginHub._connections.values()))
                session_id = websocket.state.plugin_session_id
                first = getattr(websocket.state, AUTHENTICATED_LEASE_STATE)
                record = fixture.service._lease_records[first.generation]
                # When: retirement ends the loop, but actual disconnect cleanup resists cancellation.
                if ending == "retirement":
                    await fixture.service.invalidate_cache("standalone-key")
                else:
                    await peer[1].put({"type": "websocket.disconnect", "code": 1000})
                await entered.wait()
                task.cancel()
                await cancelled.wait()
                try:
                    assert not task.done()
                    assert captured == [(registry, websocket)]
                    assert PluginHub._connections[session_id] is websocket
                    assert fixture.service._lease_owners == 1
                    assert len(record.owners) == 1 and next(iter(record.owners.values())) is task
                    with pytest.raises(CredentialLeaseError) as refused:
                        async with second.own():
                            pytest.fail("Actual standalone cleanup still owns global capacity")
                    assert refused.value.overloaded
                    if ending == "disconnect":
                        # Leave global room to distinguish the exact generation cap.
                        with pytest.MonkeyPatch.context() as capacity:
                            capacity.setattr(fixture.service, "MAX_LEASE_OWNERS", 2)
                            with pytest.raises(CredentialLeaseError) as per_lease:
                                async with first.own():
                                    pytest.fail("The old task still owns per-lease capacity")
                            assert per_lease.value.overloaded
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
                # Then: the captured socket/registry cleanup and actual owner exit restore capacity.
                assert fixture.service._lease_owners == 0
                assert not record.owners
                assert PluginHub._connections == {}
                assert await registry.list_sessions(user_id=first.user_id) == {}
                assert not PluginHub._admitted and not PluginHub._pending
                if ending == "disconnect":
                    async with first.own():
                        assert fixture.service._lease_owners == 1
                async with second.own():
                    assert fixture.service._lease_owners == 1
        finally:
            release.set()
            await PluginHub.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["welcome_error", "retirement"])
async def test_standalone_failed_welcome_keeps_actual_owner_through_cleanup(monkeypatch, ending):
    from services.credential_lease import CredentialLeaseError, current_credential_lease

    # Given: a standalone route with a suspended welcome, before registration or receive.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    for name in ["_connections", "_pending", "_ping_tasks", "_last_pong", "_admitted"]:
        monkeypatch.setattr(PluginHub, name, {})
    registry = PluginRegistry()
    PluginHub.configure(registry)
    app = Starlette(routes=[WebSocketRoute("/hub/plugin", PluginHub)])
    welcome, fail_welcome, cleanup, cancelled, release = (asyncio.Event() for _ in range(5))
    actual_disconnect = PluginHub.on_disconnect
    observed, captured = [], []
    incoming = asyncio.Queue()
    await incoming.put({"type": "websocket.connect"})

    async def receive():
        return await incoming.get()

    async def send(message):
        if message["type"] == "websocket.send":
            assert json.loads(message["text"])["type"] == "welcome"
            observed.append((asyncio.current_task(), current_credential_lease()))
            welcome.set()
            await fail_welcome.wait()
            raise OSError("Fixture welcome writer failed")

    async def held_disconnect(endpoint, websocket, close_code):
        captured.append((PluginHub._registry, websocket))
        cleanup.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        await actual_disconnect(endpoint, websocket, close_code)

    monkeypatch.setattr(PluginHub, "on_disconnect", held_disconnect)
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "path": "/hub/plugin",
        "raw_path": b"/hub/plugin",
        "scheme": "ws",
        "query_string": b"",
        "headers": [(b"x-api-key", b"welcome-key")],
        "client": ("127.0.0.1", 1234),
        "server": ("fixture", 80),
        "subprotocols": [],
    }
    async with credential_service(monkeypatch) as fixture:
        fixture.service.MAX_LEASE_OWNERS = 1
        fixture.service.MAX_OWNERS_PER_LEASE = 1
        first = (await fixture.service.validate("welcome-key")).lease
        second = (await fixture.service.validate("second-key")).lease
        record = fixture.service._lease_records[first.generation]
        task = asyncio.create_task(app(scope, receive, send))
        try:
            await welcome.wait()
            assert observed == [(task, first)]
            assert fixture.service._lease_owners == 1
            assert list(record.owners.values()) == [task]
            # When: welcome fails or is retired before dispatch reaches its receive loop.
            if ending == "retirement":
                await fixture.service.invalidate_cache("welcome-key")
            else:
                fail_welcome.set()
            await cleanup.wait()
            task.cancel()
            await cancelled.wait()
            assert not task.done()
            assert captured[0][0] is registry
            assert getattr(captured[0][1].state, AUTHENTICATED_LEASE_STATE) is first
            assert list(record.owners.values()) == [task]
            assert fixture.service._lease_owners == 1
            with pytest.raises(CredentialLeaseError) as refused:
                async with second.own():
                    pytest.fail("Failed welcome cleanup still owns global capacity")
            assert refused.value.overloaded
            if ending == "welcome_error":
                with pytest.MonkeyPatch.context() as capacity:
                    capacity.setattr(fixture.service, "MAX_LEASE_OWNERS", 2)
                    with pytest.raises(CredentialLeaseError) as per_lease:
                        async with first.own():
                            pytest.fail("Failed welcome cleanup still owns generation capacity")
                    assert per_lease.value.overloaded
        finally:
            fail_welcome.set()
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await PluginHub.shutdown()
        # Then: captured cleanup finishes before either owner budget is released.
        assert not record.owners and fixture.service._lease_owners == 0
        assert not PluginHub._admitted and not PluginHub._connections and not PluginHub._pending
        assert await registry.list_sessions(user_id=first.user_id) == {}
        async with second.own():
            assert fixture.service._lease_owners == 1
