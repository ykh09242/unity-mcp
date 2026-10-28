"""A command's HTTP selection, readiness and I/O share one elapsed budget."""

from tests.timing import await_completion, await_signal, watchdog_timeout

import asyncio
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketState

from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from tests.test_http_command_outcome import command_hub, dispatch  # pylint: disable=unused-import


async def reply(hub, message, result=None):
    await hub.endpoint._handle_command_result(
        hub.websocket,
        CommandResultMessage(id=message["id"], result=result or {"success": True}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["selection", "admission"])
async def test_total_budget_bounds_lock_wait_before_dispatch(command_hub, monkeypatch, stage):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.08)
    async with command_hub.registry._lock:
        pending = (
            dispatch()
            if stage == "selection"
            else PluginHub.send_command("session", "manage_scene", {})
        )
        started = asyncio.get_running_loop().time()
        result = await asyncio.wait_for(pending, watchdog_timeout())
    assert asyncio.get_running_loop().time() - started < 0.25
    assert result["hint"] == "retry"
    assert result["data"]["reason"] == "command_deadline_exceeded"
    assert result["data"]["phase"] == ("session_selection" if stage == "selection" else "admission")
    assert command_hub.sent == []
    assert PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("preflight", ["selection", "recovery", "readiness"])
async def test_preflight_and_send_do_not_receive_independent_budgets(
    command_hub, monkeypatch, preflight
):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.16)
    monkeypatch.setattr(PluginHub, "FAST_FAIL_TIMEOUT", 0.16)
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "1")
    background = None
    command = "manage_scene"
    if preflight in {"selection", "recovery"}:
        if preflight == "selection":
            await command_hub.registry.unregister("session")
        else:
            command_hub.websocket.client_state = WebSocketState.DISCONNECTED

        async def reconnect():
            await asyncio.sleep(0.1)
            await command_hub.registry.register("session", "Fixture", "hash", "6000.3")
            command_hub.websocket.client_state = WebSocketState.CONNECTED
            PluginHub._connections["session"] = command_hub.websocket

        background = asyncio.create_task(reconnect())
    else:
        command = "read_console"
        monkeypatch.setenv("UNITY_MCP_SESSION_READY_WAIT_SECONDS", "1")

    async def delayed_send(message):
        command_hub.sent.append(message)
        if message["type"] != "execute":
            return
        await asyncio.sleep(0.1)
        await reply(
            command_hub,
            message,
            {"status": "success", "result": {"message": "pong"}}
            if message["name"] == "ping"
            else None,
        )

    command_hub.websocket.send_json = delayed_send
    started = asyncio.get_running_loop().time()
    try:
        result = await dispatch(command)
    finally:
        if background is not None:
            await background
    elapsed = asyncio.get_running_loop().time() - started
    assert result["success"] is False, "Preflight must consume the command's execution budget"
    assert result["hint"] == "inspect_state_before_retry"
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["data"]["phase"] == "send"
    assert elapsed < 0.25
    assert [m["name"] for m in command_hub.sent if m["type"] == "execute"].count(command) == 1
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_readiness_deadline_never_dispatches_the_requested_mutation(command_hub, monkeypatch):
    monkeypatch.setattr(PluginHub, "FAST_FAIL_TIMEOUT", 0.08)
    monkeypatch.setenv("UNITY_MCP_SESSION_READY_WAIT_SECONDS", "1")

    async def unanswered(message):
        command_hub.sent.append(message)

    command_hub.websocket.send_json = unanswered
    started = asyncio.get_running_loop().time()
    result = await dispatch("read_console")
    assert asyncio.get_running_loop().time() - started < 0.25
    assert result["hint"] == "retry"
    assert result["data"]["reason"] == "command_deadline_exceeded"
    assert result["data"]["phase"] == "readiness"
    assert all(message.get("name") != "read_console" for message in command_hub.sent)
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_long_command_policy_keeps_time_beyond_default_budget(command_hub, monkeypatch):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.02)

    async def delayed_result(message):
        command_hub.sent.append(message)
        await asyncio.sleep(0.05)
        await reply(command_hub, message)

    command_hub.websocket.send_json = delayed_result
    result = await PluginHub.send_command_for_instance(
        "hash", "run_tests", {"timeout_seconds": 900}
    )
    assert result["success"] is True
    assert command_hub.sent[0]["timeout"] == 900
    assert PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["registry", "socket_close"])
async def test_expired_recovery_returns_while_owned_cleanup_finishes(
    command_hub, monkeypatch, blocked
):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.08)
    command_hub.websocket.client_state = WebSocketState.DISCONNECTED
    entered = asyncio.Event()
    release = asyncio.Event()
    held_registry = False
    if blocked == "socket_close":

        async def close(**_kwargs):
            entered.set()
            await release.wait()

        command_hub.websocket.close = close
    else:
        lookup = PluginHub._get_connection

        async def lock_after_selection(session_id):
            nonlocal held_registry
            websocket = await lookup(session_id)
            await command_hub.registry._lock.acquire()
            held_registry = True
            entered.set()
            return websocket

        monkeypatch.setattr(PluginHub, "_get_connection", lock_after_selection)

    task = asyncio.create_task(dispatch())
    shutdown = None
    try:
        await await_signal(entered, "entered")
        result = await asyncio.wait_for(asyncio.shield(task), watchdog_timeout())
        assert result["hint"] == "retry"
        assert result["data"]["reason"] == "command_deadline_exceeded"
        assert result["data"]["phase"] == "recovery"
        assert command_hub.sent == []
        assert PluginHub._eviction_tasks, "Cleanup must stay owned after its caller expires"
        shutdown = asyncio.create_task(PluginHub.shutdown())
        await asyncio.sleep(0.02)
        assert not shutdown.done(), "Shutdown must drain its outstanding cleanup"
    finally:
        if held_registry:
            command_hub.registry._lock.release()
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), watchdog_timeout())
        if shutdown is not None:
            await asyncio.wait_for(shutdown, watchdog_timeout())
    assert not PluginHub._eviction_tasks
    assert PluginHub._connections == {}
    assert PluginHub._pending == {}
    assert await command_hub.registry.list_sessions() == {}


@pytest.mark.asyncio
async def test_timeout_cleanup_has_only_its_existing_bounded_grace(command_hub, monkeypatch):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.04)
    monkeypatch.setattr(PluginHub, "CANCEL_TIMEOUT", 0.04)
    cancelled_cleanup = asyncio.Event()

    async def send_json(message):
        command_hub.sent.append(message)
        if message["type"] == "cancel":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled_cleanup.set()

    command_hub.websocket.send_json = send_json
    started = asyncio.get_running_loop().time()
    result = await dispatch()
    elapsed = asyncio.get_running_loop().time() - started
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["data"]["phase"] == "response"
    assert 0.06 <= elapsed < 0.25
    assert cancelled_cleanup.is_set()
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_socket_write_and_response_share_the_remaining_budget(command_hub, monkeypatch):
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.12)
    response = None

    async def send_json(message):
        nonlocal response
        command_hub.sent.append(message)
        if message["type"] != "execute":
            return
        await asyncio.sleep(0.06)

        async def late_result():
            await asyncio.sleep(0.1)
            await reply(command_hub, message)

        response = asyncio.create_task(late_result())

    command_hub.websocket.send_json = send_json
    started = asyncio.get_running_loop().time()
    try:
        result = await dispatch()
        assert result["data"]["reason"] == "outcome_unknown"
        assert result["data"]["phase"] == "response"
        assert asyncio.get_running_loop().time() - started < 0.2
        assert PluginHub._pending == {}
    finally:
        if response is not None:
            await response
    assert len([message for message in command_hub.sent if message["type"] == "execute"]) == 1
    assert PluginHub._pending == {}
    assert not PluginHub._retained_results


@pytest.mark.asyncio
async def test_shared_state_owner_expires_before_admission_without_dispatch(
    command_hub, monkeypatch
):
    monkeypatch.setenv("UNITY_MCP_STATE_TIMEOUT", "0.06")
    read_identity = PluginHub._read_identity
    held = False

    async def capture_then_block(session_id, **kwargs):
        nonlocal held
        identity = await read_identity(session_id, **kwargs)
        await command_hub.registry._lock.acquire()
        held = True
        return identity

    monkeypatch.setattr(PluginHub, "_read_identity", capture_then_block)
    try:
        result = await PluginHub.send_command_for_instance(
            "hash", "get_editor_state", {}, editor_state_read_mode="ordinary"
        )
    finally:
        if held:
            command_hub.registry._lock.release()
    assert result["hint"] == "retry"
    assert result["data"]["reason"] == "command_deadline_exceeded"
    assert result["data"]["phase"] == "admission"
    assert command_hub.sent == []
    assert PluginHub._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatched", [False, True])
async def test_earlier_deadline_follower_uses_the_shared_flights_dispatch_evidence(
    command_hub, monkeypatch, dispatched
):
    import transport.plugin_hub as hub_module
    from services.tools.shared_tool_reads import SharedRead

    monkeypatch.setenv("UNITY_MCP_STATE_TIMEOUT", str(watchdog_timeout()))
    resolve = PluginHub._resolve_session_id
    get_connection = PluginHub._get_connection
    early_waiting = asyncio.Event()
    release_early = asyncio.Event()
    owner_waiting = asyncio.Event()
    follower_joined = asyncio.Event()
    release_owner = asyncio.Event()
    captured = []
    first = True
    scopes = {}
    fetch = SharedRead.fetch

    def capture_deadline(when):
        scope = asyncio.timeout_at(when)
        scopes.setdefault(asyncio.current_task(), scope)
        return scope

    async def observe_fetch(read, factory, *, on_flight=None):
        def observe(flight):
            assert on_flight is not None
            on_flight(flight)
            if asyncio.current_task() is early:
                follower_joined.set()

        return await fetch(read, factory, on_flight=observe)

    local_asyncio = SimpleNamespace(**vars(asyncio))
    local_asyncio.timeout_at = capture_deadline
    monkeypatch.setattr(hub_module, "asyncio", local_asyncio)
    monkeypatch.setattr(SharedRead, "fetch", observe_fetch)

    async def delayed_first_selection(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            early_waiting.set()
            await release_early.wait()
        return await resolve(*args, **kwargs)

    lookups = 0

    async def hold_owner_admission(session_id):
        nonlocal lookups
        lookups += 1
        if not dispatched and lookups == 2:
            owner_waiting.set()
            await release_owner.wait()
        return await get_connection(session_id)

    async def hold_owner_response(message):
        command_hub.sent.append(message)
        if message["type"] == "execute":
            captured.append(message)
            owner_waiting.set()
            await release_owner.wait()
            await reply(command_hub, message)

    monkeypatch.setattr(PluginHub, "_resolve_session_id", delayed_first_selection)
    monkeypatch.setattr(PluginHub, "_get_connection", hold_owner_admission)
    if dispatched:
        command_hub.websocket.send_json = hold_owner_response

    async def read():
        return await PluginHub.send_command_for_instance(
            "hash", "get_editor_state", {}, editor_state_read_mode="ordinary"
        )

    early = asyncio.create_task(read())
    owner = None
    try:
        await await_signal(early_waiting, "early caller selection", producers=(early,))
        # Give the later owner a strictly later installed deadline even on a
        # coarse monotonic clock; neither setup budget is a speed assertion.
        monkeypatch.setenv("UNITY_MCP_STATE_TIMEOUT", str(watchdog_timeout(120)))
        owner = asyncio.create_task(read())
        await await_signal(owner_waiting, "shared read owner", producers=(early, owner))
        assert scopes[early].when() < scopes[owner].when()
        release_early.set()
        await await_signal(follower_joined, "follower joined owner", producers=(early, owner))
        # Keep the real earlier caller's timeout while the joined owner stays live.
        scopes[early].reschedule(asyncio.get_running_loop().time())
        result = await await_completion(early, "earlier follower deadline")
        assert scopes[early].expired()
        if dispatched:
            assert result["data"]["reason"] == "outcome_unknown"
            assert result["data"]["command_id"] == captured[0]["id"]
        else:
            assert result["hint"] == "retry"
            assert result["data"]["reason"] == "command_deadline_exceeded"
            assert result["data"]["phase"] == "admission"
            assert command_hub.sent == []
        assert not owner.done()
        release_owner.set()
        assert (await await_completion(owner, "shared read owner result"))["success"] is True
    finally:
        release_early.set()
        release_owner.set()
        tasks = (early, *([owner] if owner else []))
        for task in tasks:
            if not task.done():
                task.cancel()
        await await_completion(asyncio.gather(*tasks, return_exceptions=True), "read teardown")
    assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 1
    assert PluginHub._pending == {}
