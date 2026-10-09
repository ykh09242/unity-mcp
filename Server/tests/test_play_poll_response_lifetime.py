"""Play readiness keeps only its deliverable observation charged and reachable."""

import asyncio
import gc
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import weakref

import anyio
import pytest
from starlette.websockets import WebSocketState

from models.response_limits import ResponseOwner, response_owner, response_size
from models.unity_response import normalize_unity_response
import services.tools.manage_editor as module
from transport.charge_ledger import ChargeLedger
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub


class TrackedPayload(str):
    """Observe payload lifetime without changing the JSON contents."""


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
        status="succeeded",
        blocked_at=None,
        blocked=asyncio.Event(),
        payload_refs=[],
        observations=[],
        fail_at=None,
        malformed=None,
    )

    async def send_json(message):
        state.calls += 1
        state.observations.append(len(OwnedHub._retained_results))
        if state.calls == state.blocked_at:
            state.blocked.set()
            await asyncio.Event().wait()
        blob = TrackedPayload("x" * 131_072)
        state.payload_refs.append(weakref.ref(blob))
        data = {
            "job_id": message["params"]["job_id"],
            "status": state.status if state.calls == 12 else "running",
            "value": state.calls,
            "blob": blob,
        }
        if state.malformed is not None:
            data.update(state.malformed)
        await endpoint._handle_command_result(
            websocket,
            CommandResultMessage(id=message["id"], result={"success": True, "data": data}),
        )

    websocket.send_json = send_json

    async def dispatch(_send, _target, name, params, **_kwargs):
        result = normalize_unity_response(await OwnedHub.send_command("session", name, params))
        if state.calls == state.fail_at:
            raise OSError("connection lost after acquisition")
        return result

    async def sleep(_delay):
        await asyncio.sleep(0)

    monkeypatch.setattr(module, "send_with_unity_instance", dispatch)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Project@hash")
    )
    monkeypatch.setattr(
        module, "anyio", SimpleNamespace(sleep=sleep, move_on_after=anyio.move_on_after)
    )
    yield state
    for entry in OwnedHub._pending.values():
        entry["future"].cancel()


async def execute(timeout_seconds=30):
    return await module.manage_editor(
        None, "play", wait_until="first_frame", timeout_seconds=timeout_seconds
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "timed_out"])
async def test_play_polls_finish_under_two_response_budget(polling, monkeypatch, status):
    polling.status = status
    charge = response_size({"data": "x" * 131_072})
    for name in (
        "MAX_RETAINED_RESULT_BYTES",
        "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
    ):
        monkeypatch.setattr(polling.hub, name, 3 * charge)
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await execute()
        assert result["data"]["value"] == 12, result
        assert result["success"] is (status == "succeeded")
        if status != "succeeded":
            assert result["error"] == status
        assert max(polling.observations) <= 1
        assert len(polling.hub._retained_results) == len(owner.entries) == 1
        gc.collect()
        assert all(ref() is None for ref in polling.payload_refs[:-1])
        assert polling.payload_refs[-1]() is not None
    finally:
        response_owner.reset(token)
        owner.release()
    assert not polling.hub._retained_results


@pytest.mark.asyncio
async def test_play_deadline_delivers_last_observation(polling):
    polling.blocked_at = 3
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await execute(timeout_seconds=1)
        assert result["error"] == "play_readiness_timeout"
        assert result["data"]["last_response"]["data"]["value"] == 2
        assert len(polling.hub._retained_results) == len(owner.entries) == 1
        assert not polling.hub._pending
        gc.collect()
        assert polling.payload_refs[0]() is None
        assert polling.payload_refs[1]() is not None
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_external_cancel_releases_before_parent_delivery(polling):
    polling.blocked_at = 3
    owner = ResponseOwner()
    token = response_owner.set(owner)
    task = asyncio.create_task(execute())
    try:
        await asyncio.wait_for(polling.blocked.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not owner.entries and not owner.released
        assert not polling.hub._retained_results and not polling.hub._pending
        gc.collect()
        assert all(ref() is None for ref in polling.payload_refs)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_retry_diagnostic_discards_both_previous_results(polling):
    polling.fail_at = 2
    polling.blocked_at = 3
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        result = await execute(timeout_seconds=1)
        assert result["data"]["last_response"]["hint"] == "retry"
        assert not polling.hub._retained_results and not owner.entries
        gc.collect()
        assert all(ref() is None for ref in polling.payload_refs)
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed,error",
    [
        ({"status": "unknown"}, "invalid_play_readiness_response"),
        ({"job_id": "other"}, "play_readiness_job_mismatch"),
    ],
)
async def test_terminal_diagnostics_deliver_one_observation(polling, malformed, error):
    polling.malformed = malformed
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        assert (await execute())["error"] == error
        assert len(polling.hub._retained_results) == len(owner.entries) == 1
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_anyio_outer_cancellation_releases_play_results(polling):
    polling.blocked_at = 3
    owner = ResponseOwner()
    token = response_owner.set(owner)
    done = asyncio.Event()
    scopes = []

    async def caller():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await execute()
        done.set()

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(caller)
            await asyncio.wait_for(polling.blocked.wait(), 2)
            scopes[0].cancel()
            await asyncio.wait_for(done.wait(), 2)
        assert not owner.entries and not owner.released
        assert not polling.hub._retained_results and not polling.hub._pending
        gc.collect()
        assert all(ref() is None for ref in polling.payload_refs)
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.asyncio
async def test_play_preserves_sibling_response_owned_by_same_request(polling):
    owner = ResponseOwner()
    token = response_owner.set(owner)
    try:
        sibling = await polling.hub.send_command("session", "sibling", {"job_id": "sibling"})
        sibling_entry = owner.entries[0]
        result = await execute()
        assert result["data"]["value"] == 12
        assert sibling_entry in owner.entries
        assert len(owner.entries) == len(polling.hub._retained_results) == 2
        assert sibling["data"]["job_id"] == "sibling"
    finally:
        response_owner.reset(token)
        owner.release()


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_sdk_cancel_releases_play_results(tmp_path, mode):
    # Existing integration tests replace SDK modules during collection: isolate real SDK.
    source = r"""import asyncio, gc, sys
from unittest.mock import patch
sys.path[:0] = ["src", "tests"]
from fastmcp import Client, FastMCP
from pytest import MonkeyPatch
import test_play_poll_response_lifetime as regression
from transport.response_limit_middleware import ResponseLimitMiddleware

async def scenario():
    with MonkeyPatch.context() as monkeypatch:
        fixture = regression.polling.__wrapped__(monkeypatch)
        state = next(fixture)
        state.blocked_at = 3
        server = FastMCP("play-cancel-regression")
        server.add_middleware(ResponseLimitMiddleware())
        server.tool(regression.module.manage_editor)
        async with Client(server, mode=MODE) as client:
            call = asyncio.create_task(client.call_tool("manage_editor", {"action":"play", "wait_until":"first_frame"}))
            await asyncio.wait_for(state.blocked.wait(), 2)
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            for _ in range(100):
                if not state.hub._pending and not state.hub._retained_results:
                    break
                await asyncio.sleep(0.01)
            assert not state.hub._pending and not state.hub._retained_results
            gc.collect()
            assert all(ref() is None for ref in state.payload_refs)
        fixture.close()
asyncio.run(scenario())
"""
    source = source.replace("mode=MODE", "mode=" + repr(mode))
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "1",
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
