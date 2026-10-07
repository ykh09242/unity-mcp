"""Negotiated gzip through the real plugin ASGI endpoint and shared budgets."""

import gzip
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from test_plugin_transport_architecture import client, register, start_command, barrier
from transport.large_result_assembler import CHUNK_PAYLOAD_BYTES, MAGIC
from transport.plugin_hub import PluginHub
from transport.result_gzip import GZIP_WORKING_BYTES

FEATURES = ["large_result_v1", "large_result_gzip_v1"]


def encoded(command_id, raw=None):
    result = {"success": True, "data": "한글😀" * 120000, "large_integer": 9007199254740993}
    raw = (
        raw
        or json.dumps(
            {"type": "command_result", "id": command_id, "result": result}, ensure_ascii=False
        ).encode()
    )
    wire = gzip.compress(raw, mtime=0)
    chunks = [
        MAGIC
        + command_id.encode()
        + offset.to_bytes(4, "big")
        + wire[offset : offset + CHUNK_PAYLOAD_BYTES]
        for offset in range(0, len(wire), CHUNK_PAYLOAD_BYTES)
    ]
    start = {
        "type": "result_start",
        "id": command_id,
        "total_bytes": len(wire),
        "chunk_count": len(chunks),
        "encoding": "gzip",
        "decoded_bytes": len(raw),
    }
    return result, start, chunks


def test_real_negotiated_gzip_result_keeps_normal_payload_and_releases_raw_budget(client):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=FEATURES)
        assert set(ack["capabilities"]) == set(FEATURES)
        task, cid = start_command(client, wire, sid)
        expected, start, chunks = encoded(cid)
        wire.send_json(start)
        barrier(client, wire, sid)
        assert PluginHub._raw_results
        for chunk in chunks:
            wire.send_bytes(chunk)
        assert task.result(timeout=2) == expected
        assert PluginHub._raw_results == {}
        assert PluginHub._large_results.retained_bytes == 0


@pytest.mark.parametrize("capabilities", [["large_result_v1"], ["large_result_gzip_v1"]])
def test_gzip_requires_both_negotiated_capabilities_before_allocation(client, capabilities):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, ack = register(wire, capabilities=capabilities)
        assert "large_result_gzip_v1" not in ack["capabilities"]
        task, cid = start_command(client, wire, sid)
        _, start, _ = encoded(cid)
        wire.send_json(start)
        with pytest.raises(WebSocketDisconnect):
            wire.receive_json()
        assert task.result(timeout=2)["success"] is False
    assert PluginHub._raw_results == {}


def test_gzip_working_and_decoded_peak_are_reserved_before_allocation(client, monkeypatch):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        task, cid = start_command(client, wire, sid)
        _, start, chunks = encoded(cid)
        required = 5 * start["decoded_bytes"] + 4096 + GZIP_WORKING_BYTES
        monkeypatch.setattr(PluginHub, "MAX_RETAINED_RESULT_BYTES_PER_SESSION", required - 1)
        wire.send_json(start)
        assert task.result(timeout=2)["data"]["reason"] == "result_capacity"
        assert PluginHub._raw_results == {}
        for chunk in chunks:
            wire.send_bytes(chunk)
        barrier(client, wire, sid)
        assert PluginHub._large_results.retained_bytes == 0


@pytest.mark.parametrize("kind", ["crc", "trailing", "invalid_utf8", "wrong_envelope_id"])
def test_bad_compressed_payload_closes_offender_and_releases_peak_charge(client, kind):
    with client.websocket_connect("/plugin", headers={"x-api-key": "alice"}) as wire:
        sid, _, _ = register(wire, capabilities=FEATURES)
        task, cid = start_command(client, wire, sid)
        raw = None
        if kind == "invalid_utf8":
            raw = (
                b'{"type":"command_result","id":"'
                + cid.encode()
                + b'","result":{"data":"\xff'
                + b"x" * 1048576
                + b'"}}'
            )
        elif kind == "wrong_envelope_id":
            raw = json.dumps(
                {"type": "command_result", "id": "different-id", "result": {"data": "x" * 1048576}}
            ).encode()
        _, start, chunks = encoded(cid, raw)
        if kind == "crc":
            chunks[-1] = chunks[-1][:-8] + bytes([chunks[-1][-8] ^ 1]) + chunks[-1][-7:]
        elif kind == "trailing":
            chunks[-1] += b"extra"
            start["total_bytes"] += 5
        wire.send_json(start)
        for chunk in chunks:
            wire.send_bytes(chunk)
        with pytest.raises(WebSocketDisconnect):
            wire.receive_json()
        assert task.result(timeout=2)["success"] is False
    assert PluginHub._raw_results == {}
