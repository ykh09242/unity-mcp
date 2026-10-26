"""Capacity snapshots remain observable without admitting more Unity commands."""

from tests.timing import watchdog_timeout

import asyncio  # noqa: ANYIO_OK -- controlled Hub command ownership and cancellation.
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import anyio
from fastmcp import Client, FastMCP
import pytest

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.http.test_project_capabilities import (
    capability_server,
    native_observations,
    read_capabilities,
)
from core.config import config
from services.operation_policy import OperationMode, resolve_operation_policy
from tests.remote_credentials import remote_credentials
from transport.models import CommandResultMessage
from transport.unity_instance_middleware import UnityInstanceMiddleware
from transport.plugin_hub import PluginHub


def retained_command(index):
    # Keep the first Exclusive command held. Additional reviewed Controls can
    # actually reach the same session's transport quota without bypassing policy.
    command = "manage_scene" if index == 0 else "get_editor_state"
    params = {"name": str(index)}
    expected = OperationMode.EXCLUSIVE if index == 0 else OperationMode.CONTROL
    assert resolve_operation_policy(command, params, native=True).mode == expected
    return command, params


@asynccontextmanager
async def pending_commands(hub, count=2, session_id="session", *, auth=None, user=None):
    """Retain real Exclusive/Control commands while their owned peer withholds results."""
    sent = anyio.Event()
    original_send = hub.websocket.send_json
    execute_count = 0

    async def hold(message):
        nonlocal execute_count
        hub.sent.append(message)
        if message["type"] == "execute":
            execute_count += 1
            if execute_count >= count:
                sent.set()

    hub.websocket.send_json = hold

    async def dispatch(index):
        command, params = retained_command(index)
        if auth is None:
            return await PluginHub.send_command(session_id, command, params)
        return await auth.call(
            f"requester:{user}",
            PluginHub.send_command,
            session_id,
            command,
            params,
        )

    tasks = [asyncio.create_task(dispatch(i)) for i in range(count)]
    try:
        with anyio.fail_after(watchdog_timeout()):
            await sent.wait()
        assert sum(e["session_id"] == session_id for e in PluginHub._pending.values()) == count
        assert all(not task.done() for task in tasks)
        yield tasks
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        hub.websocket.send_json = original_send


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize(
    "ceiling", ["MAX_PENDING_PER_SESSION", "MAX_PENDING_PER_USER", "MAX_PENDING_COMMANDS"]
)
async def test_saturated_sdk_read_explains_unobserved_native_sources(
    command_hub, monkeypatch, mode, ceiling
):
    monkeypatch.setattr(PluginHub, ceiling, 2)
    async with Client(capability_server(), mode=mode) as client:
        await client.call_tool("select_fixture")
        async with pending_commands(command_hub) as mutations:
            pending = dict(PluginHub._pending)
            sends = list(command_hub.sent)
            with anyio.fail_after(watchdog_timeout()):
                data = await read_capabilities(client)
            assert data["connection"]["status"] == "connected"
            assert data["connection"]["admission_available"] is False
            assert data["connection"]["pending_commands"] == 2
            assert data["connection"]["tenant_pending_commands"] == 2
            assert data["partial"] is True
            assert data["readiness"]["status"] == "unknown"
            assert PluginHub._pending == pending
            assert command_hub.sent == sends
            assert all(not task.done() for task in mutations)
            assert len(data["evidence"]) == 5
            assert all(item["status"] == "unknown" for item in data["evidence"].values())
            assert {item["reason"] for item in data["evidence"].values()} == {"command_capacity"}
    assert PluginHub._pending == {}


def accounting():
    return (
        dict(PluginHub._pending),
        sum(entry["payload_bytes"] for entry in PluginHub._pending.values()),
        PluginHub._raw_results.total_bytes,
        PluginHub._retained_results.total_bytes,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ceiling",
    [
        "MAX_PENDING_PAYLOAD_BYTES_PER_SESSION",
        "MAX_PENDING_PAYLOAD_BYTES_PER_USER",
        "MAX_PENDING_PAYLOAD_BYTES",
    ],
)
async def test_real_payload_saturation_preserves_all_command_accounting(
    command_hub, monkeypatch, ceiling
):
    charge = sum(PluginHub._command_payload_size(*retained_command(index)) for index in range(2))
    assert charge > 0
    monkeypatch.setattr(PluginHub, ceiling, charge)
    async with Client(capability_server()) as client:
        await client.call_tool("select_fixture")
        async with pending_commands(command_hub):
            assert sum(entry["payload_bytes"] for entry in PluginHub._pending.values()) == charge
            before, sends = accounting(), list(command_hub.sent)
            data = await read_capabilities(client)
            assert data["connection"]["admission_available"] is False
            assert {item["reason"] for item in data["evidence"].values()} == {"command_capacity"}
            assert accounting() == before
            assert command_hub.sent == sends


@pytest.mark.asyncio
async def test_repeated_reads_ignore_ready_cache_and_preserve_admitted_send_order(
    command_hub, monkeypatch
):
    monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_SESSION", 2)
    command_hub.websocket.state.plugin_features |= {"editor_state_v1"}
    assert PluginHub._editor_states.register_session("session", None)
    state = native_observations()["get_editor_state"]
    state["compilation"]["is_domain_reload_pending"] = False
    state["advice"] = {"ready_for_tools": True}
    assert PluginHub._editor_states.accept(
        "session",
        {
            "type": "editor_state",
            "epoch": "owned",
            "sequence": state["sequence"],
            "observed_at_unix_ms": state["observed_at_unix_ms"],
            "state": state,
        },
    )
    assert (await PluginHub.get_cached_editor_state("Fixture@hash"))["advice"][
        "ready_for_tools"
    ] is True
    async with Client(capability_server()) as client:
        await client.call_tool("select_fixture")
        async with pending_commands(command_hub) as mutations:
            before, sends = accounting(), list(command_hub.sent)
            assert [message["params"]["name"] for message in sends] == ["0", "1"]
            for _ in range(4):
                with anyio.fail_after(watchdog_timeout()):
                    observations = await asyncio.gather(
                        *(read_capabilities(client) for _ in range(8))
                    )
                assert all(item["readiness"]["status"] == "unknown" for item in observations)
                assert all(item["connection"]["pending_commands"] == 2 for item in observations)
                assert all(item["partial"] is True for item in observations)
                assert accounting() == before
                assert command_hub.sent == sends
            refused = await PluginHub.send_command_for_instance(
                "Fixture@hash", "get_editor_state", {}
            )
            assert refused["data"]["reason"] == "command_capacity"
            assert command_hub.sent == sends
            # Observe completion in a controlled order; status reads never touch either owner.
            for index in (1, 0):
                await command_hub.endpoint._handle_command_result(
                    command_hub.websocket,
                    CommandResultMessage(id=sends[index]["id"], result={"success": True}),
                )
                assert (await mutations[index])["success"] is True
            assert PluginHub._pending == {}
            async with pending_commands(command_hub, count=1) as recovered:
                message = command_hub.sent[-1]
                assert message["type"] == "execute"
                assert message["id"] not in {entry["id"] for entry in sends}
                await command_hub.endpoint._handle_command_result(
                    command_hub.websocket,
                    CommandResultMessage(id=message["id"], result={"success": True}),
                )
                assert (await recovered[0])["success"] is True
            assert [message["type"] for message in command_hub.sent] == ["execute"] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("lock_name", ["hub", "registry"])
async def test_saturated_status_lock_wait_uses_original_deadline(
    command_hub, monkeypatch, lock_name
):
    import services.capabilities as capabilities

    monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_SESSION", 2)
    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.03)
    lock = PluginHub._lock if lock_name == "hub" else command_hub.registry._lock
    async with Client(capability_server()) as client:
        await client.call_tool("select_fixture")
        async with pending_commands(command_hub) as mutations:
            before, sends = accounting(), list(command_hub.sent)
            async with lock:
                # Completion while this lock remains held proves deadline expiry.
                # Ten seconds is only a separate scheduler/runaway guard.
                with anyio.fail_after(watchdog_timeout()):
                    data = await read_capabilities(client)
            assert data["connection"]["status"] == "unknown"
            assert data["connection"]["generation"] is None
            assert data["connection"]["pending_commands"] is None
            assert data["readiness"]["status"] == "unknown"
            assert accounting() == before
            assert command_hub.sent == sends
            assert all(not task.done() for task in mutations)


@pytest.mark.asyncio
async def test_canceling_status_wait_does_not_cancel_mutations(command_hub, monkeypatch):
    monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_SESSION", 2)
    entered = anyio.Event()
    original = PluginHub.get_capability_snapshot

    async def observed(*args, **kwargs):
        entered.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(PluginHub, "get_capability_snapshot", observed)
    async with Client(capability_server()) as client:
        await client.call_tool("select_fixture")
        async with pending_commands(command_hub) as mutations:
            before, sends = accounting(), list(command_hub.sent)
            lock = PluginHub._lock
            assert lock is not None
            # configure() replaces the class's initial None; Pylint retains that initializer.
            async with lock:  # pylint: disable=not-async-context-manager
                request = asyncio.create_task(read_capabilities(client))
                try:
                    with anyio.fail_after(watchdog_timeout()):
                        await entered.wait()
                    request.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await request
                finally:
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
            data = await read_capabilities(client)
            assert data["connection"]["pending_commands"] == 2
            assert accounting() == before
            assert command_hub.sent == sends
            assert all(not task.done() for task in mutations)


def tenant_server(monkeypatch, user_id, auth):
    from services.resources import _serialize_pydantic
    from services.resources.capabilities import get_capabilities

    class TenantRequesterMiddleware(UnityInstanceMiddleware):
        async def on_read_resource(self, context, call_next):
            return await auth.call(
                f"requester:{user_id}", super().on_read_resource, context, call_next
            )

    middleware = TenantRequesterMiddleware()

    async def authenticated_user():
        return user_id

    # This seam supplies the trusted authentication result, never request-controlled identity.
    monkeypatch.setattr(middleware, "_resolve_user_id", authenticated_user)
    server = FastMCP("headroom-tenant")
    server.add_middleware(middleware)
    server.resource("mcpforunity://capabilities")(_serialize_pydantic(get_capabilities))
    return server


async def tenant_socket(hub, session, user, auth, project_hash="hash"):
    await hub.registry.register(session, "Fixture", project_hash, "6000", user_id=user)
    socket = SimpleNamespace(
        state=SimpleNamespace(
            plugin_registered=True,
            plugin_session_id=session,
            plugin_generation=session,
            plugin_features=frozenset({"command_cancel_v1"}),
            user_id=user,
        ),
        client_state=hub.websocket.client_state,
        application_state=hub.websocket.application_state,
        send_json=hub.websocket.send_json,
        close=hub.websocket.close,
    )
    await auth.attach_socket(socket, f"plugin:{user}")
    PluginHub._connections[session] = socket
    return SimpleNamespace(websocket=socket, sent=hub.sent)


@pytest.mark.asyncio
async def test_saturated_sdk_counts_are_owned_by_authenticated_tenant_and_current_generation(
    command_hub, monkeypatch
):
    async with remote_credentials(
        {
            "requester:alice": "alice",
            "plugin:alice": "alice",
            "requester:bob": "bob",
            "plugin:bob": "bob",
        }
    ) as auth:
        monkeypatch.setattr(config, "http_remote_hosted", True)
        monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_USER", 2)
        monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 3)
        a = await tenant_socket(command_hub, "alice-main", "alice", auth)
        a2 = await tenant_socket(command_hub, "alice-other", "alice", auth, "other")
        b = await tenant_socket(command_hub, "bob-main", "bob", auth)
        async with (
            pending_commands(a, 1, "alice-main", auth=auth, user="alice"),
            pending_commands(a2, 1, "alice-other", auth=auth, user="alice"),
        ):
            # Alice's full user quota does not prevent Bob's separate first admission.
            async with pending_commands(b, 1, "bob-main", auth=auth, user="bob"):
                before, sends = accounting(), list(command_hub.sent)
                async with (
                    Client(tenant_server(monkeypatch, "alice", auth)) as alice,
                    Client(tenant_server(monkeypatch, "bob", auth)) as bob,
                ):

                    async def read(client, **meta):
                        result = await client.read_resource(
                            "mcpforunity://capabilities",
                            meta={"unity_instance": "Fixture@hash", **meta},
                        )
                        return json.loads(result[0].text)["data"]

                    owned = await read(alice, user_id="bob")
                    other = await read(bob)
                    assert (
                        owned["connection"]["pending_commands"]
                        == other["connection"]["pending_commands"]
                        == 1
                    )
                    assert owned["connection"]["tenant_pending_commands"] == 2
                    assert other["connection"]["tenant_pending_commands"] == 1
                    assert owned["connection"]["generation"] != other["connection"]["generation"]
                    assert "global_pending_commands" not in owned["connection"]
                    assert "command_ids" not in owned["connection"]
                    b.websocket.state.plugin_generation = "new-bob-generation"
                    current = await read(bob)
                    assert current["connection"]["generation"] != other["connection"]["generation"]
                    assert current["connection"]["tenant_pending_commands"] == 1
                    assert accounting() == before
                    assert command_hub.sent == sends
