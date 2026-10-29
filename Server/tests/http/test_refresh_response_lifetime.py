"""Refresh polling uses real producer reservations without a native/network peer."""

import asyncio
import importlib
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from starlette.websockets import WebSocketState

from core.config import config
from models.response_limits import ResponseOwner, response_owner
from services.registry.operation_invocation import wrap_tool
from tests.test_compile_job_contracts import native_job
from transport.charge_ledger import ChargeLedger
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.response_limit_middleware import ResponseLimitMiddleware

refresh = importlib.import_module("services.tools.refresh_unity")
editor = importlib.import_module("services.resources.editor_state")


class SelectedContext:
    async def get_state(self, name):
        return {"unity_instance": "Refresh@" + "a" * 32, "unity_session_id": "refresh"}.get(name)


@pytest_asyncio.fixture
async def peer(monkeypatch):
    registry = PluginRegistry()
    await registry.register("refresh", "Refresh", "a" * 32, "test")
    socket = SimpleNamespace(
        state=SimpleNamespace(
            plugin_generation="generation",
            plugin_registered=True,
            plugin_session_id="refresh",
            user_id=None,
        ),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
    )
    endpoint = PluginHub.__new__(PluginHub)
    state = SimpleNamespace(
        now=0.0,
        states=0,
        compiles=0,
        ready_at=24,
        samples=[],
        failures=[],
        block=None,
        entered=asyncio.Event(),
        unavailable_after=None,
        expire_at=None,
        ledger=ChargeLedger(),
        snapshots=[],
        keys={},
        ctx=SelectedContext(),
    )

    async def answer(message):
        name = message["name"]
        if name == "get_editor_state":
            state.states += 1
            count = state.states
            data = {
                "schema_version": "unity-mcp/editor_state@2",
                "observed_at_unix_ms": editor._now_unix_ms(),
                "sequence": count,
                "unity": {"platform": "x" * 4096},
                "compilation": {"is_compiling": count < state.ready_at},
                "assets": {"is_updating": False},
                "tests": {"is_running": False},
            }
        elif name == "get_compile_job":
            state.compiles += 1
            count = state.compiles
            data = native_job(message["params"]["compile_id"], "unknown")
            data.update(state="running", collection_complete=False, finished_at_unix_ms=None)
        else:
            count = 1
            data = {"refresh_triggered": True, "compile_requested": False}
            if message["params"].get("compile_id"):
                data["compilation"] = native_job(message["params"]["compile_id"], "unknown")
        if state.block == (name, count):
            state.entered.set()
            await asyncio.Event().wait()
        value = {"success": True, "data": data}
        if (
            state.unavailable_after
            and name == state.unavailable_after[0]
            and count >= state.unavailable_after[1]
        ):
            value = {"success": False, "error": "observation unavailable"}
        pending = PluginHub._pending[message["id"]]
        previous_keys = set(state.ledger)
        await endpoint._handle_command_result(
            socket, CommandResultMessage(id=message["id"], result=value)
        )
        state.keys[name, count] = set(state.ledger) - previous_keys
        if state.expire_at == (name, count):
            state.now = 1.0
        if pending["future"].result().get("data", {}).get("reason") == "result_capacity":
            state.failures.append(name)
        state.samples.append(len(state.ledger))

    socket.send_json = answer

    async def advance(seconds):
        state.now += seconds
        await asyncio.sleep(0)

    async def no_scan(_):
        return {"external_changes_dirty": False, "external_changes_scan_complete": True}

    for obj, name, value in [
        (config, "transport_mode", "http"),
        (config, "http_remote_hosted", False),
        (PluginHub, "_registry", registry),
        (PluginHub, "_lock", asyncio.Lock()),
        (PluginHub, "_connections", {"refresh": socket}),
        (PluginHub, "_pending", {}),
        (PluginHub, "_retained_results", state.ledger),
        (PluginHub, "_raw_results", ChargeLedger()),
        (PluginHub, "_large_results", None),
        (PluginHub, "MAX_RETAINED_RESULT_BYTES_PER_SESSION", 150_000),
        (refresh, "_in_pytest", lambda: False),
        (refresh, "time", SimpleNamespace(monotonic=lambda: state.now)),
        (refresh, "asyncio", SimpleNamespace(sleep=advance, wait_for=asyncio.wait_for)),
        (editor.external_changes_scanner, "update_and_get_async", no_scan),
    ]:
        monkeypatch.setattr(obj, name, value)
    yield state
    assert not PluginHub._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("compile", ["none", "request"])
async def test_sdk_refresh_can_finish_after_more_polls_than_retained_capacity(peer, compile):
    class Selected(Middleware):
        async def on_call_tool(self, context, call_next):
            for key in ("unity_instance", "unity_session_id"):
                await context.fastmcp_context.set_state(key, await peer.ctx.get_state(key))
            return await call_next(context)

    class Guard(ResponseLimitMiddleware):
        async def on_call_tool(self, context, call_next):
            result = await super().on_call_tool(context, call_next)
            peer.snapshots.append(len(response_owner.get().entries))
            return result

    app = FastMCP("refresh-poll-lifetime")
    app.add_middleware(Guard())
    app.add_middleware(Selected())
    app.tool(wrap_tool(refresh.refresh_unity, "refresh_unity", "refresh_unity"))
    async with Client(app, mode="2026-07-28") as client:
        result = await client.call_tool(
            "refresh_unity",
            {
                "compile": compile,
                "wait_for_ready": True,
                "timeout_seconds": 10,
            },
        )
        payload = result.structured_content["result"]
        assert payload["success"] is True
        assert payload["data"]["readiness"]["ready"] is True
    assert peer.states == peer.ready_at
    assert peer.compiles >= 5 if compile == "request" else peer.compiles == 0
    assert not peer.failures
    # A replacement can temporarily own both old/new state and old/new compile
    # replies while the observer commits, alongside the original mutation.
    assert max(peer.samples) <= (5 if compile == "request" else 3)
    assert peer.snapshots == [3 if compile == "request" else 2]
    assert not peer.ledger


@pytest.mark.asyncio
async def test_boolean_readiness_wait_discards_observations_but_keeps_sibling(peer):
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        sibling = await PluginHub.send_command("refresh", "sibling", {})
        entry = owner.entries[0]
        assert (await refresh.wait_for_editor_ready(peer.ctx, timeout_s=10))[0]
        assert sibling["success"] and owner.entries == [entry]
        assert len(peer.ledger) == 1 and not owner.released
    finally:
        response_owner.reset(token)
        owner.release()
    assert not peer.ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("block", [("get_editor_state", 3), ("get_compile_job", 2)])
async def test_cancelled_refresh_releases_polls_but_preserves_mutation(peer, block):
    peer.block = block
    owner = ResponseOwner()
    token = response_owner.set(owner)
    task = asyncio.create_task(
        refresh.refresh_unity(peer.ctx, compile="request", timeout_seconds=10)
    )
    try:
        await asyncio.wait_for(peer.entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not owner.released
        assert len(owner.entries) == len(peer.ledger) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        response_owner.reset(token)
        owner.release()
    assert not peer.ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["get_editor_state", "get_compile_job"])
async def test_unavailable_observation_keeps_last_valid_evidence_reserved(peer, command):
    peer.ready_at = 100
    peer.unavailable_after = (command, 2)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await refresh.refresh_unity(peer.ctx, compile="request", timeout_seconds=3)
        assert not result.success and result.data["timeout"]
        assert result.data["readiness"]["blocking_reasons"]
        assert result.data["compilation"]["state"] == "running"
        assert len(owner.entries) == len(peer.ledger) == 3
        assert peer.keys[command, 1] and peer.keys[command, 1] <= set(peer.ledger)
        assert not peer.failures
    finally:
        response_owner.reset(token)
        owner.release()
    assert not peer.ledger


@pytest.mark.asyncio
async def test_state_arriving_at_deadline_keeps_delivered_summary_reservation(peer):
    peer.ready_at = 100
    peer.expire_at = ("get_editor_state", 2)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await refresh.refresh_unity(peer.ctx, compile="none", timeout_seconds=1)
        assert not result.success and result.data["timeout"]
        assert result.data["readiness"]["blocking_reasons"] == ["compiling"]
        assert peer.states == 2
        assert peer.keys["get_editor_state", 1] <= set(peer.ledger)
        assert not (peer.keys["get_editor_state", 2] & set(peer.ledger))
        assert len(owner.entries) == len(peer.ledger) == 2
    finally:
        response_owner.reset(token)
        owner.release()
    assert not peer.ledger
