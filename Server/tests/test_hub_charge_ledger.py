"""Actual Hub admission and assembler callbacks retain their ledger generation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from models.response_limits import ResponseOwner, response_size
from transport.charge_ledger import ChargeLedger
from transport.large_result_assembler import (
    LargeResultProtocolError,
    THRESHOLD_BYTES,
    CHUNK_PAYLOAD_BYTES,
)
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry

COMMAND_ID = "11111111-2222-4333-8444-555555555555"


@pytest_asyncio.fixture
async def hub():
    class OwnedHub(PluginHub):
        _registry = None
        _connections = {}
        _pending = {}
        _retained_results = ChargeLedger()
        _raw_results = ChargeLedger()
        _ping_tasks = {}
        _last_pong = {}
        _admitted = {}

    OwnedHub.configure(PluginRegistry())
    websocket = SimpleNamespace(state=SimpleNamespace(plugin_generation="owned"), close=AsyncMock())
    OwnedHub._connections["session"] = websocket
    try:
        yield OwnedHub.__new__(OwnedHub), websocket
    finally:
        OwnedHub._large_results.discard_owner("owned")
        for entry in OwnedHub._pending.values():
            entry["future"].cancel()


def pending(hub, command_id=COMMAND_ID, owner=None):
    future = asyncio.get_running_loop().create_future()
    type(hub)._pending[command_id] = {
        "future": future,
        "session_id": "session",
        "user_id": "alice",
        "response_owner": owner,
    }
    return future


@pytest.mark.asyncio
async def test_actual_capacity_does_not_iterate_reservations(hub, monkeypatch):
    endpoint, _ = hub
    cls = type(endpoint)
    for number in range(256):
        cls._retained_results[str(number)] = {
            "bytes": 1,
            "user_id": "alice",
            "session_id": "session",
        }

    def forbidden():
        raise AssertionError("admission must use indexed totals")

    monkeypatch.setattr(cls._retained_results, "values", forbidden)
    monkeypatch.setattr(cls._raw_results, "values", forbidden)
    assert cls._has_result_capacity({"user_id": "alice", "session_id": "session"}, 1)


@pytest.mark.asyncio
async def test_old_assembler_release_cannot_release_replacement_raw_reservation(hub):
    endpoint, _ = hub
    cls = type(endpoint)
    pending(endpoint)
    old_assembler, old_ledger = cls._large_results, cls._raw_results
    chunks = (THRESHOLD_BYTES + CHUNK_PAYLOAD_BYTES - 1) // CHUNK_PAYLOAD_BYTES
    assert old_assembler.begin("owned", COMMAND_ID, THRESHOLD_BYTES, chunks)
    cls.configure(PluginRegistry())
    assert cls._large_results.begin("owned", COMMAND_ID, THRESHOLD_BYTES, chunks)
    replacement_charge = cls._raw_results.total_bytes
    old_assembler.discard("owned", COMMAND_ID)
    assert old_ledger.total_bytes == 0
    assert cls._raw_results.total_bytes == replacement_charge
    with pytest.raises(LargeResultProtocolError, match="result_capacity"):
        old_assembler.begin("owned", COMMAND_ID, THRESHOLD_BYTES, chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", "user", "session"])
async def test_concurrent_results_obey_the_same_budget_until_owner_release(hub, monkeypatch, scope):
    endpoint, websocket = hub
    cls = type(endpoint)
    result = {"success": True, "data": "owned"}
    charge = response_size(result)
    limit = {
        "global": "MAX_RETAINED_RESULT_BYTES",
        "user": "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "session": "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
    }[scope]
    monkeypatch.setattr(cls, limit, charge)
    first_owner, second_owner = ResponseOwner(), ResponseOwner()
    first = pending(endpoint, "first", first_owner)
    second = pending(endpoint, "second", second_owner)
    await asyncio.gather(
        endpoint._handle_command_result(websocket, CommandResultMessage(id="first", result=result)),
        endpoint._handle_command_result(
            websocket, CommandResultMessage(id="second", result=result)
        ),
    )
    assert first.result() == result
    assert second.result()["data"]["reason"] == "result_capacity"
    assert cls._retained_results.total_bytes == charge
    assert first_owner.reserve_copy() is None
    first_owner.release()
    assert cls._retained_results.total_bytes == 0
    replacement = pending(endpoint, "second", second_owner)
    await endpoint._handle_command_result(
        websocket, CommandResultMessage(id="second", result=result)
    )
    assert replacement.result() == result
    second_owner.release()
    assert cls._retained_results.total_bytes == 0


@pytest.mark.asyncio
async def test_captured_source_and_copy_release_ignore_replacement_ledger(hub):
    endpoint, websocket = hub
    cls = type(endpoint)
    owner = ResponseOwner()
    future = pending(endpoint, owner=owner)
    await endpoint._handle_command_result(
        websocket, CommandResultMessage(id=COMMAND_ID, result={"success": True})
    )
    assert future.result()["success"]
    old_ledger = cls._retained_results
    copy = owner.reserve_copy()
    assert copy is not None and len(old_ledger) == 2
    replacement = ChargeLedger(
        {COMMAND_ID: {"bytes": 10, "user_id": "bob", "session_id": "replacement"}}
    )
    cls._retained_results = replacement
    assert owner.reserve_copy() is None
    owner.release()
    copy.release()
    assert old_ledger.total_bytes == 0 and replacement.total_bytes == 10


@pytest.mark.asyncio
async def test_replaced_source_entry_cannot_admit_an_old_source_copy(hub):
    endpoint, websocket = hub
    cls = type(endpoint)
    owner = ResponseOwner()
    pending(endpoint, owner=owner)
    await endpoint._handle_command_result(
        websocket, CommandResultMessage(id=COMMAND_ID, result={"success": True})
    )
    cls._retained_results[COMMAND_ID] = {"bytes": 10, "user_id": "bob", "session_id": "replacement"}
    assert owner.reserve_copy() is None
    owner.release()


@pytest.mark.asyncio
async def test_old_pending_ledger_cannot_insert_into_replacement(hub):
    endpoint, websocket = hub
    cls = type(endpoint)
    future = pending(endpoint)
    old_ledger = cls._retained_results
    cls._pending[COMMAND_ID]["result_ledger"] = old_ledger
    cls._retained_results = ChargeLedger()
    await endpoint._handle_command_result(
        websocket, CommandResultMessage(id=COMMAND_ID, result={"success": True})
    )
    assert future.result()["data"]["reason"] == "result_capacity"
    assert not old_ledger and not cls._retained_results


@pytest.mark.asyncio
async def test_actual_ownerless_send_cleanup_only_releases_captured_ledger(hub):
    endpoint, websocket = hub
    cls = type(endpoint)
    started, cleanup, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    messages = []

    async def send(message):
        messages.append(message)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup.set()
            await finish.wait()

    websocket.send_json = send
    task = asyncio.create_task(cls.send_command("session", "owned", {}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        command_id = messages[0]["id"]
        old_ledger = cls._retained_results
        await endpoint._handle_command_result(
            websocket, CommandResultMessage(id=command_id, result={"success": True})
        )
        await asyncio.wait_for(cleanup.wait(), 2)
        assert old_ledger.total_bytes > 0
        replacement = ChargeLedger(
            {command_id: {"bytes": 30, "user_id": "bob", "session_id": "replacement"}}
        )
        cls._retained_results = replacement
        finish.set()
        assert (await asyncio.wait_for(task, 2))["success"]
        assert old_ledger.total_bytes == 0 and replacement.total_bytes == 30
    finally:
        finish.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
