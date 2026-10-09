"""Canonical preflight observations use a separate response ownership scope."""

import asyncio
import gc
import importlib
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import weakref

import anyio
import pytest
from starlette.websockets import WebSocketState

from core.config import config
from models.response_limits import ResponseOwner, response_owner, response_size
from models.unity_response import normalize_unity_response
from transport.charge_ledger import ChargeLedger
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub

module = importlib.import_module("services.tools.preflight")
editor = importlib.import_module("services.resources.editor_state")
prefabs = importlib.import_module("services.tools.manage_prefabs")
refresh = importlib.import_module("services.tools.refresh_unity")


class TrackedPayload(str):
    """Track producer strings without retaining the response in test observers."""


@pytest.fixture
def polling(monkeypatch):
    class OwnedHub(PluginHub):
        _lock = asyncio.Lock()
        _registry = SimpleNamespace(
            get_session=AsyncMock(return_value=SimpleNamespace(user_id="alice"))
        )
        _connections = {}
        _pending = {}
        _retained_results = ChargeLedger()
        _raw_results = ChargeLedger()
        _large_results = None

    endpoint = OwnedHub.__new__(OwnedHub)
    websocket = SimpleNamespace(
        state=SimpleNamespace(plugin_generation="owned"),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
    )
    OwnedHub._connections["session"] = websocket
    state = SimpleNamespace(
        hub=OwnedHub,
        calls=0,
        dispatched=0,
        ready_at=12,
        blocked_at=None,
        blocked=asyncio.Event(),
        payload_refs=[],
        observations=[],
        fail_before_at=None,
        fail_after_at=None,
        dirty=False,
        stale=False,
        tests_running=False,
        tool_calls=[],
    )

    async def send_json(message):
        if message["name"] == "get_editor_state":
            state.calls += 1
            state.observations.append(len(OwnedHub._retained_results))
            if state.calls == state.blocked_at:
                state.blocked.set()
                await asyncio.Event().wait()
            blob = TrackedPayload("x" * 131_072)
            state.payload_refs.append(weakref.ref(blob))
            data = {
                "schema_version": "unity-mcp/editor_state@2",
                "sequence": state.calls,
                "observed_at_unix_ms": int(time.time() * 1000) - (60_000 if state.stale else 0),
                "unity": {"instance_id": "Project@hash", "platform": blob},
                "compilation": {
                    "is_compiling": state.calls < state.ready_at,
                    "is_domain_reload_pending": False,
                },
                "assets": {"external_changes_dirty": state.dirty},
                "tests": {"is_running": state.tests_running},
            }
        else:
            state.tool_calls.append((message["name"], state.calls))
            data = {"command": message["name"]}
        await endpoint._handle_command_result(
            websocket,
            CommandResultMessage(id=message["id"], result={"success": True, "data": data}),
        )

    websocket.send_json = send_json

    async def dispatch(_send, target, name, params, **kwargs):
        assert target == "Project@hash"
        if name == "get_editor_state":
            assert kwargs == {"editor_state_read_mode": "authoritative"}
            state.dispatched += 1
            if state.dispatched == state.fail_before_at:
                raise OSError("connection lost before acquisition")
        result = normalize_unity_response(await OwnedHub.send_command("session", name, params))
        if name == "get_editor_state" and state.dispatched == state.fail_after_at:
            raise OSError("connection lost after acquisition")
        return result

    async def instance(_ctx):
        return "Project@hash"

    async def sleep(_delay):
        await asyncio.sleep(0)

    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    # Production preflight is deliberately bypassed by most existing pytest fixtures.
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(editor, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(editor.unity_transport, "send_with_unity_instance", dispatch)
    monkeypatch.setattr(prefabs, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(prefabs, "send_with_unity_instance", dispatch)
    yield state
    for entry in OwnedHub._pending.values():
        entry["future"].cancel()


def narrow_budget(polling, monkeypatch):
    charge = response_size({"data": "x" * 131_072})
    for name in (
        "MAX_RETAINED_RESULT_BYTES",
        "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
    ):
        monkeypatch.setattr(polling.hub, name, 3 * charge)


def assert_discarded(polling, owner):
    assert not polling.hub._retained_results and not owner.entries
    assert not owner.released
    gc.collect()
    assert all(reference() is None for reference in polling.payload_refs)


@pytest.mark.asyncio
async def test_compile_wait_finishes_under_two_response_budget(polling, monkeypatch):
    narrow_budget(polling, monkeypatch)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert await module.preflight(None, wait_for_no_compile=True) is None
        assert polling.calls == 12
        assert max(polling.observations) <= 1
        assert_discarded(polling, owner)
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_nonwaiting_preflight_discards_internal_snapshot(polling):
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert await module.preflight(None) is None
        assert polling.calls == 1
        assert_discarded(polling, owner)
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fail_before_at", "fail_after_at"])
async def test_unknown_state_fallback_releases_observations(polling, failure):
    setattr(polling, failure, 2)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert await module.preflight(None, wait_for_no_compile=True) is None
        assert polling.dispatched == 2
        assert_discarded(polling, owner)
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_snapshot_cleanup_preserves_sibling_response(polling):
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        sibling = await polling.hub.send_command("session", "sibling", {})
        entry = owner.entries[0]
        assert await module.preflight(None, wait_for_no_compile=True) is None
        assert owner.entries == [entry]
        assert len(polling.hub._retained_results) == 1
        assert sibling["data"]["command"] == "sibling"
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, "refresh_failed", "editor_unresponsive"])
async def test_snapshot_cleanup_preserves_nested_refresh_response(polling, monkeypatch, error):
    polling.dirty = True
    success = error is None

    async def nested(_ctx, **_kwargs):
        response = await polling.hub.send_command("session", "refresh", {})
        response["success"] = success
        if not success:
            response["error"] = error
        return response

    monkeypatch.setattr(refresh, "refresh_unity", nested)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await module.preflight(None, refresh_if_dirty=True, wait_for_no_compile=True)
        assert (result is None) is success
        if not success:
            assert result.data == {"command": "refresh"}
        assert len(polling.hub._retained_results) == len(owner.entries) == 1
        assert polling.tool_calls == [("refresh", 1)]
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "tests"])
async def test_local_busy_payload_discards_internal_snapshot(polling, kind):
    polling.tests_running = kind == "tests"
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await module.preflight(
            None, wait_for_no_compile=True, requires_no_tests=True, max_wait_s=0
        )
        assert result.error == "busy"
        assert result.data["reason"] == ("tests_running" if kind == "tests" else "compiling")
        assert_discarded(polling, owner)
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_diagnostic_delivery_adopts_current_observation(polling):
    polling.stale = True
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await module.preflight(None)
        assert result.error == "editor_unresponsive"
        assert result.data["diagnostics"]["status"] == "unresponsive"
        assert len(polling.hub._retained_results) == len(owner.entries) == 1
    finally:
        response_owner.reset(token)
        owner.release()
    assert not polling.hub._retained_results


@pytest.mark.asyncio
@pytest.mark.parametrize("outer_scope", [False, True])
async def test_cancel_discards_observations_without_closing_parent(polling, outer_scope):
    polling.blocked_at = 3
    owner = ResponseOwner()
    token = response_owner.set(owner)
    scopes = []
    done = asyncio.Event()

    async def caller():
        if outer_scope:
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await module.preflight(None, wait_for_no_compile=True)
        else:
            await module.preflight(None, wait_for_no_compile=True)
        done.set()

    task = asyncio.create_task(caller())
    try:
        await asyncio.wait_for(polling.blocked.wait(), 2)
        if outer_scope:
            scopes[0].cancel()
            await asyncio.wait_for(done.wait(), 2)
            await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not polling.hub._pending
        assert_discarded(polling, owner)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        response_owner.reset(token)
        owner.release()


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("cancel", [False, True])
def test_sdk_prefab_preflight_response_ownership(tmp_path, mode, cancel):
    # Isolate actual SDK from older integration fixtures replacing SDK modules.
    source = r"""import asyncio, json, sys
sys.path[:0] = ["src", "tests"]
from fastmcp import Client, FastMCP
from pytest import MonkeyPatch
import test_preflight_response_lifetime as regression
from transport.response_limit_middleware import ResponseLimitMiddleware

async def scenario():
    with MonkeyPatch.context() as monkeypatch:
        fixture = regression.polling.__wrapped__(monkeypatch)
        state = next(fixture)
        regression.narrow_budget(state, monkeypatch)
        if CANCEL:
            state.blocked_at = 3
        app = FastMCP("prefab-preflight-ownership")
        app.add_middleware(ResponseLimitMiddleware())
        app.tool(regression.prefabs.manage_prefabs)
        async with Client(app, mode=MODE) as client:
            call = asyncio.create_task(client.call_tool("manage_prefabs", {"action":"get_info", "prefab_path":"Assets/Fixture.prefab"}))
            if CANCEL:
                await asyncio.wait_for(state.blocked.wait(), 2)
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)
                for _ in range(100):
                    if not state.hub._pending and not state.hub._retained_results:
                        break
                    await asyncio.sleep(0.01)
                assert not state.tool_calls
            else:
                result = await call
                assert json.loads(result.content[0].text)["success"] is True
                assert state.calls == 12
                assert state.tool_calls == [("manage_prefabs", 12)]
                assert max(state.observations) <= 1
            assert not state.hub._pending and not state.hub._retained_results
        fixture.close()
asyncio.run(scenario())
"""
    source = source.replace("MODE", repr(mode)).replace("CANCEL", repr(cancel))
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR", "TEMP", "TMP"):
        env[key] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
