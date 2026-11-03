import json
import socket

import pytest

from transport.legacy.unity_connection import UnityConnection


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
