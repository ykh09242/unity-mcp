"""Command capacity is enforced before a Unity payload or task is retained."""

import asyncio  # noqa: ANYIO_OK -- exercise the hub's asyncio transport contract.
import json
import sys
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import anyio
import transport.plugin_hub as hub_module
from fastmcp import Client
from starlette.websockets import WebSocketState
import httpx

from core.config import config
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest_asyncio.fixture
async def admission_hub(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[PluginRegistry]:
    monkeypatch.setattr(config, "http_remote_hosted", True)
    for name in ("_connections", "_pending", "_ping_tasks", "_last_pong"):
        monkeypatch.setattr(PluginHub, name, {})
    for name in ("_registry", "_lock", "_loop", "_mcp"):
        monkeypatch.setattr(PluginHub, name, None)
    for name, value in (
        ("MAX_PENDING_COMMANDS", 4),
        ("MAX_PENDING_PER_USER", 2),
        ("MAX_PENDING_PER_SESSION", 1),
        ("MAX_PENDING_PAYLOAD_BYTES", 100_000),
        ("MAX_PENDING_PAYLOAD_BYTES_PER_USER", 50_000),
        ("MAX_PENDING_PAYLOAD_BYTES_PER_SESSION", 25_000),
        ("MAX_COMMAND_PAYLOAD_BYTES", 25_000),
    ):
        # raising=False lets the same regression execute against the scan baseline.
        monkeypatch.setattr(PluginHub, name, value, raising=False)
    registry = PluginRegistry()
    PluginHub.configure(registry)
    yield registry
    await PluginHub.shutdown()


async def register_socket(registry: PluginRegistry, session: str, user: str) -> AsyncMock:
    await registry.register(session, session, session, "test", user_id=user)
    socket = AsyncMock()
    socket.client_state = WebSocketState.CONNECTED
    socket.application_state = WebSocketState.CONNECTED
    PluginHub._connections[session] = socket
    PluginHub._last_pong[session] = time.monotonic()
    return socket


async def start_command(session: str, params: dict | None = None) -> asyncio.Task:
    task = asyncio.create_task(PluginHub.send_command(session, "run_tests", params or {}))
    for _ in range(20):
        if (any(entry["session_id"] == session for entry in PluginHub._pending.values())
                and PluginHub._connections[session].send_json.await_count):
            return task
        await asyncio.sleep(0)
    assert False, "Command never entered pending transport state"


async def reject_without_send(session: str, socket: AsyncMock, params: dict) -> dict:
    before_pending = len(PluginHub._pending)
    before_sends = socket.send_json.await_count
    loop = asyncio.get_running_loop()
    create_future = loop.create_future
    command_futures = 0

    def counted_create_future():
        nonlocal command_futures
        if sys._getframe(1).f_code is PluginHub.send_command.__func__.__code__:
            command_futures += 1
        return create_future()

    loop.create_future = counted_create_future
    task = asyncio.create_task(PluginHub.send_command(session, "run_tests", params))
    try:
        done, _ = await asyncio.wait({task}, timeout=0.1)
        assert task in done, "Overloaded commands must finish without waiting for Unity"
        result = task.result()
        assert result["success"] is False
        assert len(PluginHub._pending) == before_pending
        assert socket.send_json.await_count == before_sends
        assert command_futures == 0, "Rejected command must not allocate its pending Future"
        return result
    finally:
        loop.create_future = create_future
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_session_admission_rejects_before_building_payload(admission_hub, monkeypatch):
    socket = await register_socket(admission_hub, "a", "alice")
    first = await start_command("a", {"timeout_seconds": 3600})
    original_uuid = hub_module.uuid.uuid4
    original_message = hub_module.ExecuteCommandMessage
    try:
        await asyncio.sleep(0)
        def unexpected_work(*args, **kwargs):
            raise AssertionError("Rejected command allocated an ID or execute model")

        monkeypatch.setattr(hub_module.uuid, "uuid4", unexpected_work)
        monkeypatch.setattr(hub_module, "ExecuteCommandMessage", unexpected_work)
        # A saturated request must not even traverse its parameters.
        class Untouchable(dict):
            def items(self):
                raise AssertionError("Rejected payload was traversed")

        result = await reject_without_send("a", socket, Untouchable())
        assert result["hint"] == "retry"
        assert result["data"]["reason"] == "command_capacity"
    finally:
        monkeypatch.setattr(hub_module.uuid, "uuid4", original_uuid)
        monkeypatch.setattr(hub_module, "ExecuteCommandMessage", original_message)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert PluginHub._pending == {}

    recovered = await start_command("a")
    recovered.cancel()
    await asyncio.gather(recovered, return_exceptions=True)
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_user_capacity_spans_sessions_and_preserves_other_tenant(admission_hub):
    sockets = {sid: await register_socket(admission_hub, sid, user) for sid, user in (
        ("a1", "alice"), ("a2", "alice"), ("a3", "alice"), ("b1", "bob"),
    )}
    tasks = [await start_command("a1"), await start_command("a2")]
    try:
        result = await reject_without_send("a3", sockets["a3"], {"user_id": "bob"})
        assert result["data"]["reason"] == "command_capacity"
        tasks.append(await start_command("b1"))
        assert len(PluginHub._pending) == 3
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_global_capacity_bounds_distinct_tenants(admission_hub):
    sockets = {sid: await register_socket(admission_hub, sid, sid) for sid in ("a", "b", "c", "d", "e")}
    tasks = [await start_command(sid) for sid in ("a", "b", "c", "d")]
    try:
        await reject_without_send("e", sockets["e"], {})
        assert len(PluginHub._pending) == 4
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"script": "x" * 30_000}, {"items": [None] * 70_000}])
async def test_oversized_payload_never_enters_pending(admission_hub, payload):
    socket = await register_socket(admission_hub, "a", "alice")
    result = await reject_without_send("a", socket, payload)
    assert result["data"]["reason"] == "command_payload_limit"
    assert result["hint"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ceiling", ["MAX_PENDING_PAYLOAD_BYTES_PER_SESSION", "MAX_PENDING_PAYLOAD_BYTES_PER_USER", "MAX_PENDING_PAYLOAD_BYTES"])
async def test_each_retained_payload_budget_rejects_before_send(admission_hub, monkeypatch, ceiling):
    monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_SESSION", 5)
    monkeypatch.setattr(PluginHub, "MAX_PENDING_PER_USER", 5)
    monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 10)
    socket_a = await register_socket(admission_hub, "a", "alice")
    socket_a2 = await register_socket(admission_hub, "a2", "alice")
    socket_b = await register_socket(admission_hub, "b", "bob")
    first = await start_command("a", {"script": "x" * 100})
    try:
        size = next(iter(PluginHub._pending.values())).get("payload_bytes", 0)
        assert size > 0, "Retained payload must have a recorded admission charge"
        monkeypatch.setattr(PluginHub, ceiling, size)
        if ceiling == "MAX_PENDING_PAYLOAD_BYTES":
            await reject_without_send("b", socket_b, {})
        elif ceiling == "MAX_PENDING_PAYLOAD_BYTES_PER_USER":
            await reject_without_send("a2", socket_a2, {})
            other = await start_command("b")
            other.cancel()
            await asyncio.gather(other, return_exceptions=True)
        else:
            await reject_without_send("a", socket_a, {})
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["result", "timeout", "disconnect", "evict", "shutdown"])
async def test_all_completion_paths_release_admission(admission_hub, monkeypatch, cleanup):
    socket = await register_socket(admission_hub, "a", "alice")
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.02 if cleanup == "timeout" else 30)
    task = await start_command("a")
    try:
        if cleanup == "result":
            await hub.on_receive(socket, {"type": "command_result", "id": next(iter(PluginHub._pending)), "result": {"success": True}})
        elif cleanup == "disconnect":
            await hub.on_disconnect(socket, 1001)
        elif cleanup == "evict":
            await PluginHub._evict_connection("a", "test-stale")
        elif cleanup == "shutdown":
            await PluginHub.shutdown()
        if cleanup == "timeout":
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(task, 0.3)
        else:
            result = await asyncio.wait_for(task, 0.3)
            assert result["success"] is (cleanup == "result")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert PluginHub._pending == {}


    if cleanup == "shutdown":
        PluginHub.configure(admission_hub)
    if cleanup in {"disconnect", "evict", "shutdown"}:
        socket = await register_socket(admission_hub, "a", "alice")
    recovered = await start_command("a")
    recovered.cancel()
    await asyncio.gather(recovered, return_exceptions=True)
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_level_cancellation_releases_pending_before_cleanup_await(admission_hub):
    socket = await register_socket(admission_hub, "a", "alice")
    started = anyio.Event()

    async def blocked_send(payload):
        started.set()
        await anyio.sleep_forever()

    socket.send_json.side_effect = blocked_send
    async with anyio.create_task_group() as group:
        group.start_soon(PluginHub.send_command, "a", "run_tests", {})
        await started.wait()
        assert len(PluginHub._pending) == 1
        group.cancel_scope.cancel()
    assert PluginHub._pending == {}, "Level cancellation must release retained command capacity"


@pytest.mark.asyncio
async def test_send_failure_releases_quota_for_the_next_command(admission_hub):
    socket = await register_socket(admission_hub, "a", "alice")
    socket.send_json.side_effect = RuntimeError("controlled socket failure")
    with pytest.raises(RuntimeError, match="controlled socket failure"):
        await PluginHub.send_command("a", "run_tests", {})
    assert PluginHub._pending == {}
    socket.send_json.side_effect = None
    recovered = await start_command("a")
    recovered.cancel()
    await asyncio.gather(recovered, return_exceptions=True)
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_readiness_probe_propagates_capacity_without_retry_wait(admission_hub):
    socket = await register_socket(admission_hub, "a", "alice")
    first = await start_command("a")
    try:
        before = socket.send_json.await_count
        result = await asyncio.wait_for(PluginHub.send_command_for_instance("a", "get_editor_state", {}, user_id="alice"), 0.3)
        assert result["data"]["reason"] == "command_capacity"
        assert socket.send_json.await_count == before
        assert len(PluginHub._pending) == 1
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_replacement_session_releases_old_user_payload_quota(admission_hub, monkeypatch):
    await register_socket(admission_hub, "a", "alice")
    old = await start_command("a")
    charge = next(iter(PluginHub._pending.values())).get("payload_bytes", 0)
    monkeypatch.setattr(PluginHub, "MAX_PENDING_PAYLOAD_BYTES_PER_USER", charge or 25_000)
    replacement = AsyncMock()
    replacement.state = SimpleNamespace(user_id="alice", plugin_registered=False)
    replacement.client_state = replacement.application_state = WebSocketState.CONNECTED
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    try:
        await hub.on_receive(replacement, {"type": "register", "project_hash": "a"})
        assert (await asyncio.wait_for(old, 0.3))["hint"] == "retry"
        assert PluginHub._pending == {}
        new_session = (await admission_hub.get_session_id_by_hash("a", user_id="alice"))
        assert new_session != "a"
        recovered = await start_command(new_session)
        recovered.cancel()
        await asyncio.gather(recovered, return_exceptions=True)
        assert PluginHub._pending == {}
    finally:
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("structure", ["cycle", "depth", "non_json", "node_count"])
async def test_invalid_or_complex_payload_is_bounded(admission_hub, monkeypatch, structure):
    socket = await register_socket(admission_hub, "a", "alice")
    if structure == "cycle":
        payload = {}
        payload["self"] = payload
    elif structure == "depth":
        payload = {}
        for _ in range(70):
            payload = {"inner": payload}
    elif structure == "non_json":
        payload = {"value": set()}
    else:
        monkeypatch.setattr(PluginHub, "MAX_COMMAND_PAYLOAD_NODES", 8, raising=False)
        payload = {"values": [None] * 10}
    result = await reject_without_send("a", socket, payload)
    assert result["data"]["reason"] == "command_payload_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["ascii", "\x00\\\"", "\U0001f600\uac00"])
async def test_payload_boundary_charges_unicode_and_json_escapes(admission_hub, monkeypatch, text):
    socket = await register_socket(admission_hub, "a", "alice")
    params = {"script": text * 10}
    estimator = getattr(PluginHub, "_command_payload_size", None)
    assert estimator is not None, "Command payload requires bounded size admission"
    size = estimator("run_tests", params)
    monkeypatch.setattr(PluginHub, "MAX_COMMAND_PAYLOAD_BYTES", size - 1)
    await reject_without_send("a", socket, params)
    monkeypatch.setattr(PluginHub, "MAX_COMMAND_PAYLOAD_BYTES", size)
    accepted = await start_command("a", params)
    accepted.cancel()
    await asyncio.gather(accepted, return_exceptions=True)
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_normal_long_command_keeps_requested_deadline(admission_hub):
    socket = await register_socket(admission_hub, "a", "alice")
    hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
    task = await start_command("a", {"timeout_seconds": 900})
    try:
        for _ in range(20):
            if socket.send_json.await_count:
                break
            await asyncio.sleep(0)
        assert socket.send_json.call_args.args[0]["timeout"] == 900
        assert not task.done()
        await hub.on_receive(socket, {"type": "command_result", "id": next(iter(PluginHub._pending)), "result": {"success": True}})
        assert (await task)["success"] is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_registered_tool_reports_capacity_through_public_sdk(admission_hub, monkeypatch):
    # Full production registration/decorators/routing; only the Unity socket is inert.
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    mcp = create_mcp_server(False)
    async with Client(mcp) as client:
        PluginHub.configure(admission_hub, mcp=mcp)
        socket = await register_socket(admission_hub, "a", None)
        hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
        await hub.on_receive(socket, {"type": "register_tools", "tools": [{"name": "run_tests", "description": "owned tool control"}]})
        first = await start_command("a")
        try:
            before = socket.send_json.await_count
            response = await asyncio.wait_for(client.call_tool("run_tests", {"clear_stuck": True, "unity_instance": "a"}), 0.5)
            result = json.loads(response.content[0].text)
            assert result["success"] is False
            assert result["data"]["reason"] == "command_capacity"
            assert socket.send_json.await_count == before
            assert len(PluginHub._pending) == 1
        finally:
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        assert PluginHub._pending == {}
        async def complete(payload):
            if payload.get("type") == "execute":
                await hub.on_receive(socket, {"type": "command_result", "id": payload["id"], "result": {"success": True}})

        socket.send_json.side_effect = complete
        response = await asyncio.wait_for(client.call_tool("run_tests", {"clear_stuck": True, "unity_instance": "a"}), 0.5)
        assert json.loads(response.content[0].text)["success"] is True
        assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_real_asgi_plugin_endpoint_enforces_capacity(admission_hub, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()
    scope = {"type": "websocket", "path": "/hub/plugin", "headers": [], "client": ("127.0.0.1", 1)}
    incoming.put_nowait({"type": "websocket.connect"})

    async def dispatch():
        await PluginHub(scope, incoming.get, outgoing.put)

    endpoint = asyncio.create_task(dispatch())
    command = None
    try:
        assert (await asyncio.wait_for(outgoing.get(), 0.3))["type"] == "websocket.accept"
        assert json.loads((await outgoing.get())["text"])["type"] == "welcome"
        await incoming.put({"type": "websocket.receive", "text": json.dumps({"type": "register", "project_hash": "owned"})})
        session_id = json.loads((await asyncio.wait_for(outgoing.get(), 0.3))["text"])["session_id"]
        command = asyncio.create_task(PluginHub.send_command(session_id, "run_tests", {}))
        execution = json.loads((await asyncio.wait_for(outgoing.get(), 0.3))["text"])
        assert execution["type"] == "execute"
        result = await asyncio.wait_for(PluginHub.send_command(session_id, "run_tests", {}), 0.3)
        assert result["data"]["reason"] == "command_capacity"
        assert outgoing.empty(), "Rejected command must not reach the ASGI WebSocket send"
        await incoming.put({"type": "websocket.receive", "text": json.dumps({"type": "command_result", "id": execution["id"], "result": {"success": True}})})
        assert (await asyncio.wait_for(command, 0.3))["success"] is True
        assert PluginHub._pending == {}
    finally:
        if command is not None:
            command.cancel()
            await asyncio.gather(command, return_exceptions=True)
        await incoming.put({"type": "websocket.disconnect", "code": 1001})
        await asyncio.wait_for(endpoint, 0.3)
    assert PluginHub._pending == {}
    assert PluginHub._connections == {}


@pytest.mark.asyncio
async def test_real_http_command_route_returns_capacity_without_unity_send(admission_hub, monkeypatch):
    from main import create_mcp_server

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "local_auth_token", "owned-admission-control")
    app = create_mcp_server(False).http_app()
    socket = await register_socket(admission_hub, "a", None)
    first = await start_command("a")
    try:
        before = socket.send_json.await_count
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://owned.invalid") as client:
            response = await asyncio.wait_for(client.post("/api/command", headers={"X-Unity-MCP-Token": "owned-admission-control"}, json={"type": "run_tests", "unity_instance": "a", "params": {}}), 0.5)
        assert response.status_code == 200
        assert response.json()["data"]["reason"] == "command_capacity"
        assert socket.send_json.await_count == before
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert PluginHub._pending == {}
