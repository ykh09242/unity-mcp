"""Discovery cannot gain command or active-client replacement authority."""

import json
import socket
import struct

import pytest

from transport.legacy import port_discovery as discovery
from transport.legacy.unity_connection import UnityConnectionPool

CURRENT = (
    b"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER="
    + b"a" * 32
    + b" CHALLENGE="
    + b"b" * 64
    + b"\n"
)


class ProbePeer:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def settimeout(self, timeout):
        assert timeout > 0

    def recv(self, count):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        if len(chunk) > count:
            self.chunks.insert(0, chunk[count:])
        return chunk[:count]

    def sendall(self, data):
        self.sent.append(data)


@pytest.mark.parametrize("fragmented", [False, True])
def test_current_greeting_discovers_without_sending_or_replacing_active_client(
    monkeypatch, tmp_path, fragmented
):
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    (tmp_path / "unity-mcp-status-deadbeef.json").write_text(
        json.dumps({"project_path": "/Owned/Main/Assets", "unity_port": 6400}), encoding="utf-8"
    )
    peer = ProbePeer([bytes([value]) for value in CURRENT] if fragmented else [CURRENT])
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    pool = UnityConnectionPool()
    active_client = object()
    pool._connections["Main@deadbeef"] = active_client
    assert [instance.id for instance in pool.discover_all_instances()] == ["Main@deadbeef"]
    assert pool._connections["Main@deadbeef"] is active_client
    assert peer.sent == []
    assert peer.closed


@pytest.mark.parametrize(
    "greeting",
    [
        CURRENT[:-1],
        CURRENT.replace(b"a" * 32, b"z" * 32),
        CURRENT.replace(b"HMAC-SHA256", b"NONE"),
        b"WELCOME UNITY-MCP 3 FRAMING=1\n",
        b"not-unity FRAMING=1\n",
        b"WELCOME UNITY-MCP 2 FRAMING=1\n",
        b"x" * 512 + b"\n",
        b"WELCOME UNITY-MCP 2 \xff\n",
    ],
)
def test_invalid_greeting_never_sends_a_fallback_ping(monkeypatch, greeting):
    peer = ProbePeer([greeting])
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    assert discovery.PortDiscovery._try_probe_unity_mcp(6400) is False
    assert peer.sent == []
    assert peer.closed


@pytest.mark.parametrize("greeting", [b"WELCOME UNITY-MCP 1 FRAMING=1\n", b"MCP/0.1 FRAMING=1\n"])
def test_fragmented_legacy_greeting_still_uses_framed_ping(monkeypatch, greeting):
    pong = b'{"message":"pong"}'
    peer = ProbePeer([bytes([value]) for value in greeting + struct.pack(">Q", len(pong)) + pong])
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    assert discovery.PortDiscovery._try_probe_unity_mcp(6400) is True
    assert peer.sent == [struct.pack(">Q", 4) + b"ping"]


def test_unframed_legacy_greeting_retains_plain_ping(monkeypatch):
    peer = ProbePeer([b"WELCOME UNITY-MCP 1\n", b'{"message":"pong"}'])
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    assert discovery.PortDiscovery._try_probe_unity_mcp(6400) is True
    assert peer.sent == [b"ping"]


def test_fragmented_greeting_cannot_restart_discovery_deadline(monkeypatch):
    clock = [0.0]
    peer = ProbePeer([CURRENT])
    original_recv = peer.recv

    def slow_recv(count):
        clock[0] += 0.1
        return original_recv(count)

    peer.recv = slow_recv
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    monkeypatch.setattr(
        discovery, "time", type("Clock", (), {"monotonic": lambda: clock[0]}), raising=False
    )
    assert discovery.PortDiscovery._try_probe_unity_mcp(6400) is False
    assert peer.sent == []
    assert clock[0] <= 0.4


def test_greeting_timeout_is_a_failed_probe(monkeypatch):
    peer = ProbePeer([CURRENT[:10], socket.timeout()])
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    assert discovery.PortDiscovery._try_probe_unity_mcp(6400) is False
    assert peer.sent == []


def test_discovery_preserves_working_mutually_authenticated_client(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from transport.legacy import unity_connection as connections
    from transport.legacy.stdio_auth import authentication_proof

    token = "owned-discovery-test-credential-" + "x" * 32
    generation, challenge, session = "a" * 32, "b" * 64, "c" * 32
    host = SimpleNamespace(active=None)

    class AuthenticatedPeer(ProbePeer):
        def __init__(self):
            super().__init__([CURRENT])
            self.pending = bytearray()
            self.timeout = 1.0

        def setsockopt(self, *_):
            pass

        def settimeout(self, timeout):
            self.timeout = timeout

        def gettimeout(self):
            return self.timeout

        def setblocking(self, _):
            pass

        def close(self):
            self.closed = True

        def recv(self, count, flags=0):
            assert not self.closed, "Existing authenticated socket was replaced"
            if flags == socket.MSG_PEEK:
                raise BlockingIOError
            return super().recv(count)

        def sendall(self, data):
            self.sent.append(data)
            self.pending.extend(data)
            if len(self.pending) < 8:
                return
            length = struct.unpack(">Q", self.pending[:8])[0]
            if len(self.pending) < length + 8:
                return
            payload = bytes(self.pending[8:])
            self.pending.clear()
            if payload == b"ping":
                assert host.active is self
                reply = {"status": "success", "result": {"message": "pong"}}
            else:
                request = json.loads(payload)
                nonce = request["client_nonce"]
                assert request["proof"] == authentication_proof(
                    token, ["client", generation, challenge, nonce]
                )
                if host.active is not None:
                    host.active.close()
                host.active = self
                reply = {
                    "type": "authenticated",
                    "version": 2,
                    "session_id": session,
                    "proof": authentication_proof(
                        token, ["server", generation, challenge, nonce, session]
                    ),
                }
            encoded = json.dumps(reply).encode("ascii")
            self.chunks.append(struct.pack(">Q", len(encoded)) + encoded)

    active, probe = AuthenticatedPeer(), AuthenticatedPeer()
    peers = iter([active, probe])
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: next(peers))
    monkeypatch.setattr(connections, "read_status_file", lambda *_: None)
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    (tmp_path / "unity-mcp-status-deadbeef.json").write_text(
        json.dumps({"project_path": "/Owned/Main/Assets", "unity_port": 6400}), encoding="utf-8"
    )
    connection = connections.UnityConnection(
        port=6400, instance_id="Main@deadbeef", auth_token_provider=lambda _: token
    )
    try:
        assert connection.connect()
        assert connection.send_command("ping", {}) == {"message": "pong"}
        before = connection.authenticated_session
        pool = connections.UnityConnectionPool()
        pool._connections[connection.instance_id] = connection
        assert [instance.id for instance in pool.discover_all_instances()] == [
            connection.instance_id
        ]
        assert probe.closed and probe.sent == []
        assert host.active is active and not active.closed
        assert connection.authenticated_session is before
        assert connection.send_command("ping", {}) == {"message": "pong"}
    finally:
        connection.disconnect()
