"""Bounded framed buffers without a final immutable payload copy."""
import struct
import socket

import pytest

from transport.legacy.unity_connection import FRAMED_MAX, UnityConnection, _UnityProtocolError


class IntoPeer:
    def __init__(self, payload, chunk=7):
        self.payload = memoryview(payload)
        self.offset = 0
        self.chunk = chunk
        self.requests = []

    def recv_into(self, destination):
        self.requests.append(len(destination))
        size = min(len(destination), self.chunk, len(self.payload) - self.offset)
        destination[:size] = self.payload[self.offset:self.offset + size]
        self.offset += size
        return size


def connection():
    instance = object.__new__(UnityConnection)
    instance.use_framing = True
    return instance


def test_partial_frame_uses_owned_mutable_payload_and_bounded_receives():
    # Given a framed reply arriving in partial receives.
    payload = b'{"data":"' + b'x' * 200_000 + b'"}'
    peer = IntoPeer(struct.pack('>Q', len(payload)) + payload, chunk=6000)
    # When the transport receives the frame.
    result = connection().receive_full_response(peer)
    # Then no final payload copy is required and each receive is slab bounded.
    assert isinstance(result, bytearray)
    assert result == payload
    assert max(peer.requests) <= 65_536


def test_declared_large_frame_does_not_allocate_or_receive_full_size():
    # Given a maximum-size declaration followed by EOF.
    peer = IntoPeer(struct.pack('>Q', FRAMED_MAX), chunk=8)
    # When the transport attempts to receive the body.
    with pytest.raises(ConnectionError):
        connection().receive_full_response(peer)
    # Then the first attempted payload receive is bounded independently of it.
    assert peer.requests[-1] <= 65_536


def test_oversized_frame_is_rejected_before_payload_receive():
    peer = IntoPeer(struct.pack('>Q', FRAMED_MAX + 1), chunk=8)
    with pytest.raises(_UnityProtocolError):
        connection().receive_full_response(peer)
    assert peer.requests == [8]


@pytest.mark.parametrize('when', ['before', 'after'])
def test_deadline_is_checked_around_each_receive(monkeypatch, when):
    instance = connection()
    peer = IntoPeer(b'abc', chunk=1)
    events = []
    def before(sock, deadline):
        assert sock is peer and deadline == 123
        events.append('before')
        if when == 'before':
            raise TimeoutError('owned deadline')
    def after(deadline):
        events.append('after')
        raise TimeoutError('owned deadline')
    monkeypatch.setattr(instance, '_set_socket_deadline', before)
    monkeypatch.setattr(instance, '_check_deadline', after)
    with pytest.raises(TimeoutError):
        instance._read_exact_buffer(peer, 3, deadline=123)
    assert peer.offset == (0 if when == 'before' else 1)
    assert events == (['before'] if when == 'before' else ['before', 'after'])


def test_receive_keeps_original_socket_when_connection_reference_changes(monkeypatch):
    instance = connection()
    original = IntoPeer(b'abcdef', chunk=2)
    replacement = IntoPeer(b'new')
    instance.sock = original
    def checked(deadline):
        instance.sock = replacement
    monkeypatch.setattr(instance, '_check_deadline', checked)
    assert instance._read_exact_buffer(original, 6) == b'abcdef'
    assert replacement.offset == 0


def test_recv_only_peer_remains_compatible():
    class LegacyPeer:
        def __init__(self):
            self.data = b'abcdef'
        def recv(self, count):
            part, self.data = self.data[:min(count, 2)], self.data[min(count, 2):]
            return part
    assert connection()._read_exact(LegacyPeer(), 6) == b'abcdef'


def test_socket_timeout_keeps_framed_timeout_classification():
    class SlowPeer(IntoPeer):
        def recv_into(self, destination):
            raise socket.timeout('owned timeout')
    with pytest.raises(TimeoutError, match='Timeout receiving Unity response'):
        connection().receive_full_response(SlowPeer(b''))
