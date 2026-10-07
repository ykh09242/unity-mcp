"""Phase 7 cancellation and gzip contracts over owned in-process ASGI sockets."""

import asyncio
from concurrent.futures import CancelledError
import gzip
import importlib
import json
from threading import Event

import pytest
from starlette.websockets import WebSocketDisconnect

from transport.large_result_assembler import CHUNK_PAYLOAD_BYTES, MAGIC
from transport.plugin_hub import PluginHub
from transport.result_gzip import GZIP_WORKING_BYTES
from test_plugin_transport_architecture import (
    barrier,
    begin,
    client,
    payload_and_frames,
    register,
    start_command,
)

CANCEL = "command_cancel_v1"
GZIP = "large_result_gzip_v1"
LARGE = "large_result_v1"


def receive(client, wire):
    """Bound the ASGI receive so a missing control frame cannot hang the suite."""
    future = client.portal.start_task_soon(wire._send_rx.receive)
    try:
        message = future.result(timeout=2)
    except BaseException:
        future.cancel()
        raise
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(message.get("code", 1000), message.get("reason", ""))
    return json.loads(message["text"])


def compressed(command_id):
    result = {"success": True, "data": {"owned": "multiline\n" + "성공" * 200000}}
    decoded = json.dumps(
        {"type": "command_result", "id": command_id, "result": result}, ensure_ascii=False
    ).encode()
    return result, decoded, gzip.compress(decoded, mtime=0)


def frames(command_id, payload):
    return [
        MAGIC
        + command_id.encode("ascii")
        + offset.to_bytes(4, "big")
        + payload[offset : offset + CHUNK_PAYLOAD_BYTES]
        for offset in range(0, len(payload), CHUNK_PAYLOAD_BYTES)
    ]


def gzip_start(wire, command_id, payload, decoded, **overrides):
    message = {
        "type": "result_start",
        "id": command_id,
        "total_bytes": len(payload),
        "chunk_count": len(frames(command_id, payload)),
        "encoding": "gzip",
        "decoded_bytes": len(decoded),
    }
    message.update(overrides)
    wire.send_json(message)


def assert_released():
    assert PluginHub._pending == {}
    assert PluginHub._raw_results == {}
    assert PluginHub._retained_results == {}
    assert PluginHub._large_results.retained_bytes == 0


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_negotiated_cancel_uses_original_id_and_releases_partial_transfer(
    client, monkeypatch, reason
):
    # Given a negotiated command with a partially received owned result.
    if reason == "timeout":
        monkeypatch.setattr(PluginHub, "COMMAND_TIMEOUT", 0.5)
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=[LARGE, CANCEL])
        assert CANCEL in ack["capabilities"]
        done = Event()
        task, cid = start_command(client, wire, sid, done=done)
        _, payload, chunks = payload_and_frames(cid)
        begin(wire, cid, payload, chunks)
        wire.send_bytes(chunks[0])
        barrier(client, wire, sid)
        assert PluginHub._raw_results
        # When the caller abandons it or its original deadline expires.
        if reason == "cancel":
            task.cancel()
        with pytest.raises(CancelledError if reason == "cancel" else TimeoutError):
            task.result(timeout=2)
        assert done.wait(2)
        # Then the wire receives exactly the same command ID and all accounting is free.
        assert receive(client, wire) == {"type": "cancel", "id": cid}
        assert_released()
        for chunk in chunks[1:]:
            wire.send_bytes(chunk)
        barrier(client, wire, sid)
        assert_released()


def test_capability_off_cancellation_does_not_send_control(client):
    # Given an old peer with a pending command.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire)
        assert CANCEL not in ack["capabilities"]
        done = Event()
        task, _ = start_command(client, wire, sid, done=done)
        # When the caller cancels.
        task.cancel()
        assert done.wait(2)
        # Then the next received message is a new execute rather than a cancel frame.
        barrier(client, wire, sid)
        assert_released()


def test_old_generation_cleanup_and_late_result_cannot_affect_replacement(client):
    # Given a command owned by a generation that is replaced for the same principal/project.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as old:
        old_sid, _, ack = register(old, capabilities=[LARGE, GZIP, CANCEL])
        assert set(ack["capabilities"]) == {LARGE, GZIP, CANCEL}
        old_task, old_id = start_command(client, old, old_sid)
        old_generation = PluginHub._connections[old_sid].state.plugin_generation
        with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as replacement:
            sid, _, _ = register(replacement, capabilities=[LARGE, GZIP, CANCEL])
            assert PluginHub._connections[sid].state.plugin_generation != old_generation
            assert old_task.result(timeout=2)["success"] is False
            # When late old-result bytes arrive on the replacement and stale cancellation runs.
            active, active_id = start_command(client, replacement, sid)
            old_task.cancel()
            _, decoded, payload = compressed(old_id)
            gzip_start(replacement, old_id, payload, decoded)
            for frame in frames(old_id, payload):
                replacement.send_bytes(frame)
            replacement.send_json(
                {
                    "type": "command_result",
                    "id": old_id,
                    "result": {"success": True, "data": "stale"},
                }
            )
            barrier(client, replacement, sid)
            # Then the current command remains pending and its result is authoritative.
            assert not active.done()
            assert PluginHub._raw_results == {}
            replacement.send_json(
                {
                    "type": "command_result",
                    "id": active_id,
                    "result": {"success": True, "data": "current"},
                }
            )
            assert active.result(timeout=2) == {"success": True, "data": "current"}
            assert_released()


def test_blocked_cancel_send_has_bounded_cleanup_and_frees_capacity(client, monkeypatch):
    # Given a negotiated pending transfer and a controlled blocked cancel write.
    monkeypatch.setattr(PluginHub, "CANCEL_TIMEOUT", 0.05)
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=[LARGE, CANCEL])
        assert CANCEL in ack["capabilities"]
        done, entered, released = Event(), Event(), Event()
        task, cid = start_command(client, wire, sid, done=done)
        _, payload, chunks = payload_and_frames(cid)
        begin(wire, cid, payload, chunks)
        wire.send_bytes(chunks[0])
        barrier(client, wire, sid)
        websocket = PluginHub._connections[sid]
        original_send = websocket.send_json

        async def blocked(message, *args, **kwargs):
            if message.get("type") == "cancel":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    released.set()
            else:
                await original_send(message, *args, **kwargs)

        monkeypatch.setattr(websocket, "send_json", blocked)
        # When the caller cancels while that write cannot finish.
        task.cancel()
        assert entered.wait(2)
        # Then accounting was released before IO and cleanup terminates by its bound.
        client.portal.call(assert_released)
        assert done.wait(2)
        assert released.wait(2)
        assert_released()
        barrier(client, wire, sid)


def test_changed_generation_before_cleanup_suppresses_stale_cancel_and_releases_original(
    client, monkeypatch
):
    # Given a command admitted under one authenticated socket generation.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=[LARGE, CANCEL])
        assert CANCEL in ack["capabilities"]
        done = Event()
        task, cid = start_command(client, wire, sid, done=done)
        _, payload, chunks = payload_and_frames(cid)
        begin(wire, cid, payload, chunks)
        wire.send_bytes(chunks[0])
        barrier(client, wire, sid)
        assert PluginHub._raw_results
        # When the socket's generation changes before the caller's cleanup resumes.
        websocket = PluginHub._connections[sid]
        monkeypatch.setattr(websocket.state, "plugin_generation", "replacement-generation")
        task.cancel()
        assert done.wait(2)
        # Then captured old-generation accounting is freed and no stale cancel is emitted.
        assert_released()
        barrier(client, wire, sid)


def test_gzip_requires_base_capability_and_negotiates_supported_pair(client):
    # Given a peer advertising gzip without the binary framing capability.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        _, welcome, ack = register(wire, capabilities=[GZIP])
        # Then unsupported dependency combinations are not accepted.
        assert {LARGE, GZIP, CANCEL}.issubset(welcome["capabilities"])
        assert ack["capabilities"] == []
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        _, _, ack = register(wire, capabilities=[LARGE, GZIP])
        assert set(ack["capabilities"]) == {LARGE, GZIP}


def test_negotiated_gzip_decodes_complete_unicode_envelope_and_releases(client):
    # Given a pending command on a peer with both framing/compression capabilities.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=[LARGE, GZIP])
        assert GZIP in ack["capabilities"]
        task, cid = start_command(client, wire, sid)
        expected, decoded, payload = compressed(cid)
        # When actual gzip bytes pass over the ASGI binary wire.
        gzip_start(wire, cid, payload, decoded)
        barrier(client, wire, sid)
        generation = PluginHub._connections[sid].state.plugin_generation
        assert (
            PluginHub._raw_results[generation, cid]["bytes"]
            == 5 * len(decoded) + 4096 + GZIP_WORKING_BYTES
        )
        for frame in frames(cid, payload):
            wire.send_bytes(frame)
        # Then the decoded envelope, including complete Unicode data, is unchanged.
        assert task.result(timeout=2) == expected
        assert_released()


@pytest.mark.parametrize("features", [[], [LARGE]])
def test_gzip_stream_without_compression_negotiation_is_rejected(client, features):
    # Given a registered old or framing-only peer.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=features)
        assert GZIP not in ack["capabilities"]
        task, cid = start_command(client, wire, sid)
        _, decoded, payload = compressed(cid)
        # When it tries gzip metadata without accepted capability.
        gzip_start(wire, cid, payload, decoded)
        # Then no gzip result is accepted or allocated.
        with pytest.raises(WebSocketDisconnect) as failure:
            receive(client, wire)
        assert failure.value.code == 4400
        assert task.result(timeout=2)["success"] is False
        assert_released()


@pytest.mark.parametrize(
    "metadata",
    [
        {"encoding": "brotli"},
        {"encoding": 7},
        {"decoded_bytes": True},
        {"decoded_bytes": "1200000"},
        {"decoded_bytes": 1200000.5},
        {"decoded_bytes": None},
        {"decoded_bytes": 100},
        {"encoding": "identity", "decoded_bytes": 1200000},
    ],
)
def test_invalid_compression_start_is_closed_without_allocation(client, metadata):
    # Given malformed compression metadata on an active owned command.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=[LARGE, GZIP])
        assert GZIP in ack["capabilities"]
        task, cid = start_command(client, wire, sid)
        _, decoded, payload = compressed(cid)
        # When the strict start boundary receives it.
        gzip_start(wire, cid, payload, decoded, **metadata)
        # Then the offending socket closes and no transfer survives.
        with pytest.raises(WebSocketDisconnect) as failure:
            receive(client, wire)
        assert failure.value.code == 4400
        assert task.result(timeout=2)["success"] is False
        assert_released()


@pytest.mark.parametrize("kind", ["crc", "trailing", "truncated", "envelope_id"])
def test_invalid_gzip_stream_closes_only_owner_and_releases(client, kind):
    # Given an active gzip owner plus an unrelated healthy socket.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=[LARGE, GZIP])
        assert GZIP in ack["capabilities"]
        with client.websocket_connect("/plugin", headers={"x-api-key": "bob"}) as healthy:
            healthy_sid, _, _ = register(healthy, "healthy")
            task, cid = start_command(client, wire, sid)
            _, decoded, payload = compressed(
                "00000000-0000-0000-0000-000000000000" if kind == "envelope_id" else cid
            )
            if kind == "crc":
                payload = payload[:-8] + bytes([payload[-8] ^ 1]) + payload[-7:]
            elif kind == "trailing":
                payload += b"unowned trailing bytes"
            elif kind == "truncated":
                payload = payload[:-4]
            # When corrupt or mismatched data arrives through actual binary frames.
            gzip_start(wire, cid, payload, decoded)
            for frame in frames(cid, payload):
                wire.send_bytes(frame)
            # Then only that connection closes and all allocation is released.
            with pytest.raises(WebSocketDisconnect) as failure:
                receive(client, wire)
            assert failure.value.code == 4400
            assert task.result(timeout=2)["success"] is False
            assert_released()
            barrier(client, healthy, healthy_sid)


@pytest.mark.parametrize(
    "ceiling",
    [
        "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
        "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "MAX_RETAINED_RESULT_BYTES",
    ],
)
def test_compressed_capacity_includes_decoded_working_bytes_before_allocation(
    client, monkeypatch, ceiling
):
    # Given a compressed result whose decoded buffer fits but working space does not.
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=[LARGE, GZIP])
        assert GZIP in ack["capabilities"]
        task, cid = start_command(client, wire, sid)
        _, decoded, payload = compressed(cid)
        assert GZIP_WORKING_BYTES == 512 * 1024
        monkeypatch.setattr(PluginHub, ceiling, 5 * len(decoded) + 4096 + GZIP_WORKING_BYTES - 1)
        allocations = []

        def reject_allocation(size):
            allocations.append(size)
            raise AssertionError(
                "Capacity must be rejected before the decoded bytearray allocation"
            )

        assembler = importlib.import_module("transport.large_result_assembler")
        monkeypatch.setattr(assembler, "bytearray", reject_allocation, raising=False)
        # When metadata attempts reservation, before any compressed chunk arrives.
        gzip_start(wire, cid, payload, decoded)
        reply = task.result(timeout=2)
        # Then a capacity response leaves no allocation and this socket remains usable.
        assert reply["success"] is False
        assert reply["data"]["reason"] == "result_capacity"
        assert allocations == []
        assert_released()
        for frame in frames(cid, payload):
            wire.send_bytes(frame)
        barrier(client, wire, sid)
