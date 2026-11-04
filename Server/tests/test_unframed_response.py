import json
import socket

import pytest

from transport.legacy.unity_connection import UnityConnection
from tests import test_stdio_json_codec

reply_connection = test_stdio_json_codec.reply_connection


class ChunkSocket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def recv(self, size):
        return next(self.chunks, b"")


@pytest.mark.parametrize(
    "chunks, expected",
    [
        ([b'{"content":"\xed', b'\x95\x9c"}'], {"content": "한"}),
        ([b'{"content":"say \\', b'"hello\\""}'], {"content": 'say "hello"'}),
        (
            [b'{"status":"success","result":{"message":"pong"', b"}}"],
            {"status": "success", "result": {"message": "pong"}},
        ),
    ],
)
def test_unframed_fragmented_json_round_trips(chunks, expected):
    connection = UnityConnection(port=1)
    assert json.loads(connection.receive_full_response(ChunkSocket(chunks))) == expected


def test_unframed_truncated_eof_is_reported():
    connection = UnityConnection(port=1)
    with pytest.raises(Exception, match="truncated unframed JSON"):
        connection.receive_full_response(ChunkSocket([b'{"content":"unfinished']))


def test_unframed_response_size_is_bounded(monkeypatch):
    monkeypatch.setattr("transport.legacy.unity_connection.FRAMED_MAX", 4)
    with pytest.raises(Exception, match="exceeds maximum size"):
        UnityConnection(port=1).receive_full_response(ChunkSocket([b'{"a":1}']))


def test_unframed_socket_timeout_is_preserved():
    class TimeoutSocket:
        def recv(self, size):
            raise socket.timeout()

    with pytest.raises(Exception, match="Timeout receiving Unity response"):
        UnityConnection(port=1).receive_full_response(TimeoutSocket())


def test_fragmented_unframed_response_decodes_complete_value_once(reply_connection, monkeypatch):
    import transport.legacy.unity_connection as uc

    wire = b'{"status":"success","result":{"text":"' + b"x" * 16_000 + b'"}}'
    conn, sock = reply_connection(wire)
    conn.use_framing = False
    sock.pending = wire
    original_native, original_stdlib = uc.from_json, json.loads
    native_sizes, stdlib_sizes = [], []

    def native(data, **kwargs):
        native_sizes.append(len(data))
        return original_native(data, **kwargs)

    def stdlib(data, **kwargs):
        stdlib_sizes.append(len(data))
        return original_stdlib(data, **kwargs)

    monkeypatch.setattr(uc, "from_json", native)
    monkeypatch.setattr(uc.json, "loads", stdlib)
    assert conn.send_command("manage_scene", {}, max_attempts=0) == {"text": "x" * 16_000}
    assert native_sizes == [len(wire)]
    assert stdlib_sizes == []


@pytest.mark.parametrize(
    "chunks, expected",
    [
        ([b"1", b"2", b"3", b" "], b"123 "),
        ([b"-1", b".2", b"e", b"+3"], b"-1.2e+3"),
        ([b"nu", b"ll"], b"null"),
        ([b"[", b'"[\\"', b'}]",', b"true]"], b'["[\\"}]",true]'),
        ([b'"a\\', b'\\b"'], b'"a\\\\b"'),
    ],
)
def test_unframed_root_boundary_survives_arbitrary_fragmentation(chunks, expected):
    raw = UnityConnection(port=1).receive_full_response(ChunkSocket(chunks))
    assert raw == expected
    json.loads(raw)


@pytest.mark.parametrize(
    "wire", [b'{"a":1}trailing', b'{"a":1}]', b'{"a":invalid}', b'{"a":"\xff"}']
)
def test_malformed_unframed_reply_does_not_replay(reply_connection, wire):
    conn, sock = reply_connection(wire)
    conn.use_framing = False
    sock.pending = wire

    def recv(count, flags=0):
        if flags:
            raise BlockingIOError()
        result, sock.pending = sock.pending[:count], sock.pending[count:]
        return result

    sock.recv = recv  # Keep trailing garbage in the observed slab, not future socket data.
    result = conn.send_command("manage_gameobject", {"action": "modify"}, max_attempts=2)
    assert result.data["reason"] == "outcome_unknown"
    assert len(sock.sent) == 1 and sock.closed
