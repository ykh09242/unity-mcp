"""Malformed large-result buffers must die before asynchronous close cleanup."""

import asyncio
import gc
import gzip
import json
from types import SimpleNamespace
import weakref

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from core.config import config
from transport.charge_ledger import ChargeLedger
import transport.large_result_assembler as assembly
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry

CID = "11111111-2222-4333-8444-555555555555"
SIZE = 1024 * 1024


@pytest_asyncio.fixture
async def owned_hub(monkeypatch):
    class OwnedHub(PluginHub):
        _registry = None
        _connections = {}
        _pending = {}
        _retained_results = ChargeLedger()
        _ping_tasks = {}
        _last_pong = {}
        _admitted = {}

    monkeypatch.setattr(config, "http_remote_hosted", True)
    OwnedHub.configure(PluginRegistry())
    await OwnedHub._registry.register("owned", "Owned", "abc", "6000", user_id="alice")
    entered, release = asyncio.Event(), asyncio.Event()

    async def close(**kwargs):
        assert kwargs == {"code": 4400, "reason": "Invalid large result transfer"}
        entered.set()
        await release.wait()

    socket = SimpleNamespace(
        state=SimpleNamespace(
            plugin_generation="owned",
            plugin_session_id="owned",
            user_id="alice",
            plugin_features={"large_result_v1", "large_result_gzip_v1"},
        ),
        close=close,
    )
    OwnedHub._connections["owned"] = socket
    future = asyncio.get_running_loop().create_future()
    OwnedHub._pending[CID] = {"future": future, "session_id": "owned", "user_id": "alice"}
    references = []

    class ObservedBuffer(bytearray):
        """The actual bytearray operations remain inherited; only add weakref support."""

    def observe(size):
        buffer = ObservedBuffer(size)
        references.append(weakref.ref(buffer))
        return buffer

    monkeypatch.setattr(assembly, "bytearray", observe, raising=False)
    state = SimpleNamespace(
        hub=OwnedHub.__new__(OwnedHub),
        socket=socket,
        cls=OwnedHub,
        future=future,
        entered=entered,
        release=release,
        buffers=references,
    )
    try:
        yield state
    finally:
        release.set()
        await OwnedHub.on_disconnect(state.hub, socket, 4400)
        if future.done() and not future.cancelled():
            future.exception()
        await OwnedHub.shutdown()


async def failed_transfer(state, kind):
    expected = {"type": "command_result", "id": CID, "result": {"body": "x" * SIZE}}
    if kind == "wrong_id":
        expected["id"] = "wrong-id"
    elif kind == "wrong_type":
        expected["type"] = "other"
    raw = json.dumps(expected).encode()
    if kind == "invalid_utf8":
        raw = b"\xff" + raw[1:]
    elif kind == "invalid_json":
        raw = raw[:-1] + b"!"
    compressed = kind in {"crc", "bomb"}
    wire = gzip.compress(raw, mtime=0) if compressed else raw
    declared = len(raw)
    if kind == "crc":
        wire = wire[:-8] + bytes([wire[-8] ^ 1]) + wire[-7:]
    elif kind == "bomb":
        declared -= 1
    start = {
        "type": "result_start",
        "id": CID,
        "total_bytes": len(wire),
        "chunk_count": (len(wire) + assembly.CHUNK_PAYLOAD_BYTES - 1)
        // assembly.CHUNK_PAYLOAD_BYTES,
    }
    if compressed:
        start.update(encoding="gzip", decoded_bytes=declared)
    await state.hub._handle_large_result(state.socket, start)
    assert state.buffers and state.cls._raw_results.total_bytes > 0
    for offset in range(0, len(wire), assembly.CHUNK_PAYLOAD_BYTES):
        frame = assembly.MAGIC + CID.encode() + offset.to_bytes(4, "big")
        frame += wire[offset : offset + assembly.CHUNK_PAYLOAD_BYTES]
        await state.hub._handle_large_result(state.socket, frame)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["crc", "bomb", "invalid_utf8", "invalid_json", "wrong_id", "wrong_type"]
)
async def test_failure_releases_buffer_before_close_can_suspend(owned_hub, kind):
    state = owned_hub
    task = asyncio.create_task(failed_transfer(state, kind))
    try:
        await asyncio.wait_for(state.entered.wait(), 2)
        gc.collect()
        assert state.cls._raw_results.total_bytes == 0
        assert state.cls._large_results.retained_bytes == 0
        assert all(reference() is None for reference in state.buffers)
        # Invalid transfer settling still belongs to the disconnect/command path.
        assert not state.future.done()
        state.release.set()
        await task
        await state.hub.on_disconnect(state.socket, 4400)
        assert state.future.done() and state.future.exception() is not None
    finally:
        state.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["crc", "wrong_id"])
async def test_cancel_during_failed_transfer_close_does_not_retain_buffer(owned_hub, kind):
    state = owned_hub
    task = asyncio.create_task(failed_transfer(state, kind))
    try:
        await asyncio.wait_for(state.entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gc.collect()
        assert state.cls._raw_results.total_bytes == 0
        assert state.cls._large_results.retained_bytes == 0
        assert all(reference() is None for reference in state.buffers)
        assert not state.future.done()
        await state.hub.on_disconnect(state.socket, 4400)
        assert state.future.done() and state.future.exception() is not None
    finally:
        state.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_failure_close_keeps_other_generation_allocation_and_owner_charge(owned_hub):
    state = owned_hub
    other = "22222222-2222-4333-8444-555555555555"
    other_socket = SimpleNamespace(
        state=SimpleNamespace(plugin_generation="bob-generation"), close=AsyncMock()
    )
    state.cls._connections["bob-session"] = other_socket
    future = asyncio.get_running_loop().create_future()
    state.cls._pending[other] = {"future": future, "session_id": "bob-session", "user_id": "bob"}
    state.cls._large_results.begin("bob-generation", other, SIZE, 17)
    other_buffer = state.buffers[0]
    other_charge = state.cls._raw_results.total_bytes
    task = asyncio.create_task(failed_transfer(state, "crc"))
    try:
        await asyncio.wait_for(state.entered.wait(), 2)
        gc.collect()
        assert other_buffer() is not None and all(ref() is None for ref in state.buffers[1:])
        assert state.cls._raw_results.total_bytes == other_charge
        assert state.cls._large_results.retained_bytes == SIZE
        assert not future.done()
        state.release.set()
        await task
    finally:
        state.release.set()
        await asyncio.gather(task, return_exceptions=True)
        state.cls._large_results.discard_owner("bob-generation")
        state.cls._pending.pop(other, None)
        state.cls._connections.pop("bob-session", None)
        future.cancel()
