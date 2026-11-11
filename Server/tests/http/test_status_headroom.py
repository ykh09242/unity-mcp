"""Capacity snapshots remain observable without admitting more Unity commands."""

from tests.timing import watchdog_timeout

import asyncio  # noqa: ANYIO_OK -- controlled Hub command ownership and cancellation.
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from tests.transport_socket import JsonSocket

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


async def next_execute(hub, count):
    with anyio.fail_after(watchdog_timeout()):
        while len([message for message in hub.sent if message["type"] == "execute"]) < count:
            await anyio.sleep(0)
    return [message for message in hub.sent if message["type"] == "execute"][-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ceiling",
    [
        "MAX_PENDING_COMMANDS",
        "MAX_PENDING_PER_USER",
        "MAX_PENDING_PER_SESSION",
        "MAX_PENDING_PAYLOAD_BYTES",
        "MAX_PENDING_PAYLOAD_BYTES_PER_USER",
        "MAX_PENDING_PAYLOAD_BYTES_PER_SESSION",
    ],
)
async def test_reviewed_control_survives_ordinary_saturation_and_flood_is_refused(
    command_hub, monkeypatch, ceiling
):
    limit = PluginHub._command_payload_size(*retained_command(0)) if "BYTES" in ceiling else 1
    monkeypatch.setattr(PluginHub, ceiling, limit)
    async with pending_commands(command_hub, count=1):
        before = accounting()
        control = asyncio.create_task(PluginHub.send_command("session", "get_compile_job", {}))
        try:
            message = await next_execute(command_hub, 2)
            assert message["name"] == "get_compile_job"
            entry = PluginHub._pending[message["id"]]
            assert entry["control_reserve"] is True and entry["payload_bytes"] > 0
            for name, params in (
                ("get_compile_job", {}),
                (
                    "unknown_custom_tool",
                    {"action": "status", "mode": "control", "control_reserve": True},
                ),
                ("export_test_job", {}),
            ):
                refused = await PluginHub.send_command("session", name, params)
                assert refused["data"]["reason"] == "command_capacity"
            assert len(command_hub.sent) == 2
        finally:
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)
        assert accounting() == before
    assert accounting() == ({}, 0, 0, 0)


@pytest.mark.asyncio
async def test_reserved_control_response_uses_small_bound_and_delivery_ownership(
    command_hub, monkeypatch
):
    from models.response_limits import ResponseOwner, response_owner

    monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
    monkeypatch.setattr(PluginHub, "MAX_CONTROL_RESULT_BYTES", 4096)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert (await PluginHub.send_command("session", "get_compile_job", {}))["success"]
    finally:
        response_owner.reset(token)
    assert PluginHub._pending == {} and PluginHub._retained_results.total_bytes > 0
    assert PluginHub._retained_results.total_bytes <= 4096
    assert PluginHub._pending_capacity_error("session", None, control=True) is not None
    copied = owner.reserve_copy()
    assert copied is not None
    owner.release()
    assert PluginHub._control_reservations
    assert PluginHub._pending_capacity_error("session", None, control=True) is not None
    copied.release()
    assert accounting() == ({}, 0, 0, 0)
    assert PluginHub._control_reservations == {}

    async def oversized(message):
        if message["type"] == "execute":
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(id=message["id"], result={"data": "x" * 8192}),
            )

    command_hub.websocket.send_json = oversized
    result = await PluginHub.send_command("session", "get_compile_job", {})
    assert result["data"]["reason"] == "result_capacity"
    assert accounting() == ({}, 0, 0, 0)


@pytest.mark.asyncio
async def test_reserved_response_cannot_borrow_exhausted_global_result_budget(
    command_hub, monkeypatch
):
    monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
    monkeypatch.setattr(PluginHub, "MAX_RETAINED_RESULT_BYTES", 1)
    result = await PluginHub.send_command("session", "get_compile_job", {})
    assert result["data"]["reason"] == "result_capacity"
    assert accounting() == ({}, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduler_reserve", [False, True])
async def test_cancelled_reserved_control_keeps_actual_response_worker_accounted(
    command_hub, monkeypatch, scheduler_reserve
):
    import threading
    import transport.plugin_hub as hub_module
    from services.registry import operation_invocation
    from services.registry.operation_scheduler import OperationScheduler

    scheduler = OperationScheduler(max_participants=0 if scheduler_reserve else 1024)
    monkeypatch.setattr(operation_invocation, "scheduler", lambda: scheduler)
    monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered, release = asyncio.Event(), threading.Event()
    original = hub_module.response_size

    def measure(value, **kwargs):
        if isinstance(value, dict) and value.get("control_worker") is True:
            assert threading.get_ident() != loop_thread
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(watchdog_timeout())
        return original(value, **kwargs)

    monkeypatch.setattr(hub_module, "response_size", measure)

    async def reply(message):
        if message["type"] == "execute":
            await command_hub.endpoint._handle_command_result(
                command_hub.websocket,
                CommandResultMessage(
                    id=message["id"],
                    result={"success": True, "data": "x" * 70_000, "control_worker": True},
                ),
            )

    command_hub.websocket.send_json = reply
    caller = asyncio.create_task(PluginHub.send_command("session", "get_compile_job", {}))
    try:
        with anyio.fail_after(watchdog_timeout()):
            await entered.wait()
        caller.cancel()
        caller.cancel()
        for _ in range(20):
            await asyncio.sleep(0)
        assert bool(scheduler._controls) is scheduler_reserve
        assert scheduler.snapshot()["retained_bytes"] > 0
        assert 0 < PluginHub._retained_results.total_bytes <= PluginHub.MAX_CONTROL_RESULT_BYTES
        assert PluginHub._pending_capacity_error("session", None, control=True) is not None
    finally:
        release.set()
        await asyncio.gather(caller, return_exceptions=True)
    assert scheduler.snapshot()["retained_bytes"] == 0 and scheduler._controls == {}
    assert accounting() == ({}, 0, 0, 0)
    assert PluginHub._control_reservations == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ceiling",
    [
        "MAX_CONTROL_COMMANDS",
        "MAX_CONTROL_PER_USER",
        "MAX_CONTROL_PER_SESSION",
        "MAX_CONTROL_PAYLOAD_BYTES",
        "MAX_CONTROL_PAYLOAD_BYTES_PER_USER",
        "MAX_CONTROL_PAYLOAD_BYTES_PER_SESSION",
    ],
)
async def test_transport_control_reserve_has_independent_count_and_byte_limits(
    command_hub, monkeypatch, ceiling
):
    monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
    for name in ("MAX_CONTROL_COMMANDS", "MAX_CONTROL_PER_USER", "MAX_CONTROL_PER_SESSION"):
        monkeypatch.setattr(PluginHub, name, 8)
    charge = PluginHub._command_payload_size("get_compile_job", {})
    monkeypatch.setattr(PluginHub, ceiling, 2 * charge - 1 if "BYTES" in ceiling else 1)

    async def hold(message):
        command_hub.sent.append(message)

    command_hub.websocket.send_json = hold
    control = asyncio.create_task(PluginHub.send_command("session", "get_compile_job", {}))
    try:
        await next_execute(command_hub, 1)
        refused = await PluginHub.send_command("session", "get_compile_job", {})
        assert refused["data"]["reason"] == "command_capacity"
        assert len(command_hub.sent) == 1
        assert sum(e["payload_bytes"] for e in PluginHub._pending.values()) == charge
    finally:
        control.cancel()
        await asyncio.gather(control, return_exceptions=True)
    assert accounting() == ({}, 0, 0, 0)


@pytest.mark.asyncio
async def test_cancelled_control_retains_reserve_while_raw_ingress_worker_runs(
    command_hub, monkeypatch
):
    import threading

    monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = PluginHub._decode_json_message

    def decode(raw, max_nodes):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout())
        return original(raw, max_nodes)

    monkeypatch.setattr(PluginHub, "_decode_json_message", staticmethod(decode))

    async def hold(message):
        command_hub.sent.append(message)

    command_hub.websocket.send_json = hold
    caller = asyncio.create_task(PluginHub.send_command("session", "get_compile_job", {}))
    decoder = None
    try:
        message = await next_execute(command_hub, 1)
        raw = json.dumps(
            {"type": "command_result", "id": message["id"], "result": {"data": "x" * 70_000}}
        )
        decoder = asyncio.create_task(
            command_hub.endpoint._decode_ingress(command_hub.websocket, raw)
        )
        with anyio.fail_after(watchdog_timeout()):
            await entered.wait()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        decoder.cancel()
        assert PluginHub._pending == {}
        assert PluginHub._pending_capacity_error("session", None, control=True) is not None
        assert PluginHub._retained_results.total_bytes > 0
    finally:
        release.set()
        caller.cancel()
        await asyncio.gather(
            caller, *([decoder] if decoder is not None else []), return_exceptions=True
        )
    assert PluginHub._control_reservations == {}
    assert accounting() == ({}, 0, 0, 0)


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
            control = asyncio.create_task(
                PluginHub.send_command_for_instance("Fixture@hash", "get_editor_state", {})
            )
            try:
                with anyio.fail_after(watchdog_timeout()):
                    while len(command_hub.sent) < 3:
                        await anyio.sleep(0)
                control_message = command_hub.sent[-1]
                assert PluginHub._pending[control_message["id"]]["control_reserve"] is True
                await command_hub.endpoint._handle_command_result(
                    command_hub.websocket,
                    CommandResultMessage(id=control_message["id"], result={"success": True}),
                )
                assert (await control)["success"] is True
            finally:
                control.cancel()
                await asyncio.gather(control, return_exceptions=True)
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
            assert [message["type"] for message in command_hub.sent] == ["execute"] * 4


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
    socket = JsonSocket(
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
async def test_control_reserve_is_scoped_to_authenticated_principal_and_instance(
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
        monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
        sockets = {}
        for session, user in (("a1", "alice"), ("a2", "alice"), ("a3", "alice"), ("b1", "bob")):
            sockets[session] = await tenant_socket(command_hub, session, user, auth, session)

            async def hold(message):
                command_hub.sent.append(message)

            sockets[session].websocket.send_json = hold
        callers = []
        try:
            for session, user in (("a1", "alice"), ("a2", "alice"), ("b1", "bob")):
                callers.append(
                    asyncio.create_task(
                        auth.call(
                            f"requester:{user}",
                            PluginHub.send_command,
                            session,
                            "get_compile_job",
                            {},
                        )
                    )
                )
                await next_execute(command_hub, len(callers))
            assert len(PluginHub._pending) == 3
            assert {entry["user_id"] for entry in PluginHub._pending.values()} == {"alice", "bob"}
            refused = await auth.call(
                "requester:alice",
                PluginHub.send_command,
                "a3",
                "get_compile_job",
                {"user_id": "bob"},
            )
            assert refused["data"]["reason"] == "command_capacity"
            cross_tenant = await auth.call(
                "requester:alice", PluginHub.send_command, "b1", "get_compile_job", {}
            )
            assert cross_tenant["success"] is False and cross_tenant["error"] == "auth_required"
            assert len(PluginHub._pending) == 3
        finally:
            for caller in callers:
                caller.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
    assert accounting() == ({}, 0, 0, 0)


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
