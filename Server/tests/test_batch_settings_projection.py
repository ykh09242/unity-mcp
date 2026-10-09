"""Batch settings share the required scalar without copying unrelated snapshots."""

import asyncio
import copy
import gc
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import weakref

import pytest
from starlette.websockets import WebSocketState

from core.config import config
from models.response_limits import ResponseOwner, response_owner, response_size
from models.unity_response import normalize_unity_response
from transport.charge_ledger import ChargeLedger
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub

module = importlib.import_module("services.tools.batch_execute")


@pytest.fixture
def settings(monkeypatch):
    state = SimpleNamespace(
        copies=[],
        refs=[],
        reads=0,
        batches=0,
        limit=3,
        entered=asyncio.Event(),
        release=asyncio.Event(),
        cancelled=asyncio.Event(),
        hub_at_batch=[],
        fail_before=False,
        fail_after=False,
        response_success=True,
    )
    state.release.set()

    class Metadata(list):
        def __deepcopy__(self, memo):
            state.copies.append(len(self))
            result = copy.deepcopy(list(self), memo)
            memo[id(self)] = result
            return result

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

    state.hub = OwnedHub
    endpoint = OwnedHub.__new__(OwnedHub)
    socket = SimpleNamespace(
        state=SimpleNamespace(plugin_generation="owned"),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
    )
    OwnedHub._connections["session"] = socket

    async def sender(message):
        success = True
        if message["name"] == "get_editor_state":
            state.reads += 1
            state.entered.set()
            try:
                await state.release.wait()
            except asyncio.CancelledError:
                state.cancelled.set()
                raise
            irrelevant = Metadata([{"value": "x" * 64} for _ in range(8192)])
            state.refs.append(weakref.ref(irrelevant))
            data = {
                "settings": {"batch_execute_max_commands": state.limit},
                "unrelated_inventory": irrelevant,
            }
            success = state.response_success
        else:
            assert message["name"] == "batch_execute"
            state.batches += 1
            state.hub_at_batch.append(
                sum(
                    ledger is OwnedHub._retained_results
                    for ledger, _ in response_owner.get().entries
                )
            )
            data = {"results": [{"success": True}]}
        await endpoint._handle_command_result(
            socket,
            CommandResultMessage(id=message["id"], result={"success": success, "data": data}),
        )

    socket.send_json = sender

    async def dispatch(_send, instance, name, params):
        assert instance == "Selected@fixture"
        if name == "get_editor_state" and state.fail_before:
            raise OSError("settings failed before response")
        result = normalize_unity_response(await OwnedHub.send_command("session", name, params))
        if name == "get_editor_state" and state.fail_after:
            raise OSError("settings failed after response acquisition")
        return result

    async def selector(_ctx):
        return "Selected@fixture"

    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(module, "send_with_unity_instance", dispatch)
    monkeypatch.setattr(module, "get_unity_instance_from_context", selector)
    module.invalidate_cached_max_commands()
    yield state
    module.invalidate_cached_max_commands()
    for entry in OwnedHub._pending.values():
        entry["future"].cancel()


def context():
    async def get_state(key):
        return {"user_id": "alice", "unity_session_id": "session"}.get(key)

    return SimpleNamespace(get_state=get_state)


def limit_hub(settings, monkeypatch, budget):
    for name in (
        "MAX_RETAINED_RESULT_BYTES",
        "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
    ):
        monkeypatch.setattr(settings.hub, name, budget)


@pytest.mark.asyncio
@pytest.mark.parametrize("tight", [False, True])
async def test_eight_batches_only_copy_scalar_and_keep_all_results(settings, monkeypatch, tight):
    if tight:
        charge = response_size({"data": {"unused": [{"value": "x" * 64} for _ in range(8192)]}})
        limit_hub(settings, monkeypatch, 2 * charge)
    settings.release.clear()
    owners = []

    async def caller():
        owner = ResponseOwner()
        owners.append(owner)
        token = response_owner.set(owner)
        try:
            return await module.batch_execute(context(), [{"tool": "read_console"}])
        finally:
            response_owner.reset(token)

    tasks = [asyncio.create_task(caller()) for _ in range(8)]
    try:
        await asyncio.wait_for(settings.entered.wait(), 2)
        await asyncio.sleep(0)
        settings.release.set()
        replies = await asyncio.gather(*tasks)
        assert all(reply["success"] for reply in replies)
        assert settings.reads == 1 and settings.batches == 8
        assert not settings.copies
        assert settings.hub_at_batch == [0] * 8
        assert len(settings.hub._retained_results) == 8
        # Each parent still owns its scalar SharedRead reservation and delivered batch result.
        assert all(len(owner.entries) == 2 and not owner.released for owner in owners)
        await asyncio.sleep(0)
        gc.collect()
        assert all(reference() is None for reference in settings.refs)
    finally:
        settings.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for owner in owners:
            owner.release()
    assert not settings.hub._retained_results


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, "3", None])
async def test_invalid_projection_preserves_uncached_default(settings, limit):
    settings.limit = limit
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert await module._get_max_commands_from_editor_state(context(), "Selected@fixture") == 25
        assert not module._cached_max_commands
        assert not settings.copies and not settings.hub._retained_results
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fail_before", "fail_after"])
async def test_rpc_exception_preserves_default_and_releases_raw_owner(settings, failure):
    setattr(settings, failure, True)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert await module._get_max_commands_from_editor_state(context(), "Selected@fixture") == 25
        assert not settings.hub._retained_results and not owner.entries
        assert not owner.released
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_actual_rpc_capacity_refusal_keeps_existing_default_policy(settings, monkeypatch):
    limit_hub(settings, monkeypatch, 256)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        # A settings RPC refusal has always used the uncached default. Only
        # SharedRead capacity failures have a separate fail-closed batch response.
        assert await module._get_max_commands_from_editor_state(context(), "Selected@fixture") == 25
        assert not module._cached_max_commands and not settings.hub._retained_results
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_valid_settings_projection_preserves_existing_success_flag_contract(settings):
    settings.response_success = False
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert await module._get_max_commands_from_editor_state(context(), "Selected@fixture") == 3
        assert not settings.copies and not settings.hub._retained_results
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("all_cancel", [False, True])
async def test_settings_cancellation_keeps_active_read_or_closes_final_owner(settings, all_cancel):
    settings.release.clear()
    owners = []

    async def caller():
        owner = ResponseOwner()
        owners.append(owner)
        token = response_owner.set(owner)
        try:
            return await module.batch_execute(context(), [{"tool": "read_console"}])
        finally:
            response_owner.reset(token)

    tasks = [asyncio.create_task(caller()) for _ in range(2)]
    try:
        await asyncio.wait_for(settings.entered.wait(), 2)
        await asyncio.sleep(0)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert not settings.cancelled.is_set()
        if all_cancel:
            tasks[1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[1]
            assert settings.cancelled.is_set()
            assert not settings.hub._retained_results and not settings.hub._pending
            assert all(not owner.entries and not owner.released for owner in owners)
        else:
            settings.release.set()
            assert (await tasks[1])["success"] is True
            assert settings.reads == 1 and settings.batches == 1
            assert settings.hub_at_batch == [0]
    finally:
        settings.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for owner in owners:
            owner.release()


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_sdk_batch_projects_settings_before_dispatch(tmp_path, mode):
    source = r"""import asyncio, json, sys
sys.path[:0] = ["src", "tests"]
from fastmcp import Client, FastMCP
from pytest import MonkeyPatch
import test_batch_settings_projection as regression
from transport.response_limit_middleware import ResponseLimitMiddleware

async def scenario():
    with MonkeyPatch.context() as monkeypatch:
        fixture = regression.settings.__wrapped__(monkeypatch)
        state = next(fixture)
        app = FastMCP("batch-settings-projection")
        app.add_middleware(ResponseLimitMiddleware())
        app.tool(regression.module.batch_execute)
        async with Client(app, mode=MODE) as client:
            result = await client.call_tool("batch_execute", {"commands":[{"tool":"read_console"}]})
            assert json.loads(result.content[0].text)["success"] is True
            assert not state.copies
            assert state.hub_at_batch == [0]
            assert state.reads == state.batches == 1
            assert not state.hub._retained_results and not state.hub._pending
        fixture.close()
asyncio.run(scenario())
"""
    source = source.replace("MODE", repr(mode))
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
