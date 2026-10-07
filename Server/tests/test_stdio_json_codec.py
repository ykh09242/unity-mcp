"""Response codec contracts through the framed transport and retry helper."""

import json
import math
import re
import socket
import struct
import sys
from unittest.mock import Mock

import pytest

from core.config import config
import transport.legacy.unity_connection as uc


class ReplySocket:
    """An inert fragmented wire boundary retaining send/close evidence."""

    def __init__(self, response):
        self.pending = struct.pack(">Q", len(response)) + response
        self.timeout = 1.25
        self.sent = []
        self.closed = False

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value

    def setblocking(self, value):
        self.timeout = None if value else 0.0

    def recv(self, count, flags=0):
        if flags:
            raise BlockingIOError()
        count = min(count, 7)
        result, self.pending = self.pending[:count], self.pending[count:]
        return result

    def sendall(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


@pytest.fixture
def reply_connection(monkeypatch):
    prior_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(sys.int_info.default_max_str_digits)

    def deny(*args, **kwargs):
        raise AssertionError("Codec tests must not use discovery or network")

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "command_total_timeout", 2.0)
    monkeypatch.setattr(uc, "read_status_file", lambda target_hash=None: None)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(uc.stdio_port_registry, "get_instances", deny)
    monkeypatch.setattr(uc.time, "sleep", deny)

    def create(response):
        sock = ReplySocket(response)
        conn = uc.UnityConnection(
            port=1111, instance_id="Owned@a11ce005", sock=sock, use_framing=True
        )
        monkeypatch.setattr(uc, "get_unity_connection", lambda instance_id=None: conn)
        return conn, sock

    try:
        yield create
    finally:
        sys.set_int_max_str_digits(prior_limit)


@pytest.mark.parametrize("command", ["ping", "manage_scene"])
def test_valid_reply_avoids_stdlib_decode_through_actual_helper(
    reply_connection, monkeypatch, command
):
    # Given a valid framed reply and the existing configured digit limit.
    result = {"message": "pong"} if command == "ping" else {"name": "Owned 한글 😀", "id": 123}
    conn, sock = reply_connection(json.dumps({"status": "success", "result": result}).encode())
    stdlib_decode = Mock(wraps=json.loads)
    monkeypatch.setattr(uc.json, "loads", stdlib_decode)

    # When the real retry helper dispatches and receives the response.
    actual, used = uc._send_command_with_retry(command, {}, max_retries=0, retry_on_reload=False)

    # Then the fast decoder retains response, provenance and finite timeout.
    assert actual == result and used is conn
    assert stdlib_decode.call_count == 0
    assert len(sock.sent) == 2 and sock.timeout == 1.25 and not sock.closed


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":NaN,"b":Infinity,"c":-Infinity,"d":1e400}',
        b'{"a":-0.0,"b":0.0}',
        b'{"a":18446744073709551615}',
        b'{"a":18446744073709551616}',
        b'{"a":-18446744073709551616}',
        b'{"a":' + b"9" * 200 + b"}",
        '{"a":"한글 😀 \\u2028"}'.encode(),
        b'{"a":"\\ud83d\\ude00"}',
        b'{"a":"\\ud800"}',
        b'{"a":1,"a":2}',
        b'{"a":1.234567890123456789e-200}',
        b'{"a":5e-324,"b":2.2250738585072012e-308}',
        b"[1,2]",
        b"null",
    ],
)
def test_accepted_json_reply_preserves_stdlib_values(reply_connection, raw):
    # Given representative accepted scalar/container edge cases.
    wire = b'{"status":"success","result":' + raw + b"}"
    expected = json.loads(wire.decode("utf-8"))["result"]
    _, sock = reply_connection(wire)

    # When the transport receives and decodes the actual framed response.
    actual = uc.send_command_with_retry("manage_scene", {}, max_retries=0, retry_on_reload=False)

    # Then every floating bit, integer and Unicode string retains its value.
    def exact(value):
        if isinstance(value, float):
            return ("float", value.hex(), math.copysign(1, value))
        if isinstance(value, dict):
            return {key: exact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [exact(item) for item in value]
        return (type(value).__name__, value)

    assert exact(actual) == exact(expected)
    assert len(sock.sent) == 2 and sock.timeout == 1.25 and not sock.closed


@pytest.mark.parametrize(
    "wire",
    [
        b'{"status',
        b'{"status":"success"}trailing',
        b"",
        b"[]",
        b"null",
        b'{"a":"\xff"}',
        b'{"a":"\x00"}',
        b'\xef\xbb\xbf{"status":"success"}',
        '{"status":"success"}'.encode("utf-16"),
        '{"status":"success"}'.encode("utf-32"),
    ],
)
def test_invalid_reply_keeps_unknown_outcome_and_does_not_replay(reply_connection, wire):
    # Given an invalid complete reply after a dispatched command.
    conn, sock = reply_connection(wire)
    try:
        json.loads(wire.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        expected_error = str(error)
    else:
        expected_error = "Unity response must be a JSON object"
    if not wire:
        # A zero-length frame is a heartbeat; the exhausted peer then closes.
        expected_error = "Connection closed before reading expected bytes"

    # When retries are enabled in the actual transport.
    response = conn.send_command("manage_gameobject", {"action": "modify"}, max_attempts=2)

    # Then classification, socket disposal and single dispatch remain intact.
    assert response.success is False
    assert response.error == expected_error
    assert response.hint == "inspect_state_before_retry"
    assert response.data == {"reason": "outcome_unknown", "command": "manage_gameobject"}
    assert len(sock.sent) == 2 and sock.closed and conn.sock is None


@pytest.mark.parametrize("limit", [640, sys.int_info.default_max_str_digits, 0])
@pytest.mark.parametrize("digits", [1000, 5000])
def test_nested_bigints_preserve_configured_digit_limit(reply_connection, limit, digits):
    # Given nested bigints and an explicitly configured process integer limit.
    wire = b'{"status":"success","result":{"nested":[{"integer":' + b"9" * digits + b"}]}}"
    conn, sock = reply_connection(wire)
    sys.set_int_max_str_digits(limit)
    try:
        expected = json.loads(wire.decode("utf-8"))["result"]
    except ValueError as error:
        expected_error = str(error)
    else:
        expected_error = None

    # When the real helper decodes the complete reply.
    if expected_error is None:
        actual = uc.send_command_with_retry(
            "manage_scene", {}, max_retries=0, retry_on_reload=False
        )
        assert actual == expected
    else:
        with pytest.raises(ValueError, match=re.escape(expected_error)):
            uc.send_command_with_retry("manage_scene", {}, max_retries=0, retry_on_reload=False)

    # Then the unchanged stdlib exception/socket contract and limit are retained.
    assert sys.get_int_max_str_digits() == limit
    assert len(sock.sent) == 2 and sock.timeout == 1.25 and not sock.closed
    assert conn.sock is sock
