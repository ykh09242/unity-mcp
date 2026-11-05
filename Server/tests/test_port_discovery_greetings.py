"""Discovery cannot gain command or active-client replacement authority."""

import json
import os
import socket
import struct
from types import SimpleNamespace

import pytest

from transport.legacy import port_discovery as discovery
from transport.legacy import unity_connection as connections
from transport.legacy.stdio_auth import StdioAuthenticationError, authentication_proof
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
        json.dumps(
            {
                "project_path": "/Owned/Main/Assets",
                "unity_port": 6400,
                "listener_generation": "a" * 32,
            }
        ),
        encoding="utf-8",
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


@pytest.mark.parametrize("framed", [True, False])
@pytest.mark.parametrize("fragmented", [True, False])
@pytest.mark.parametrize(
    "reply, expected",
    [
        (b'{ "message" : "pong" }', True),
        (b'{"status": "success", "result": {"message": "pong"}}', True),
        (b'{"message":"pong"', False),
        (b'{"message":"pong"} trailing', False),
        (b'{"nested":{"message":"pong"}}', False),
        (b'{"status":"error","result":{"message":"pong"}}', False),
        (b'[ {"message":"pong"} ]', False),
        (b'{"message":"not pong"}', False),
    ],
)
def test_legacy_pong_is_parsed_as_bounded_json(monkeypatch, framed, fragmented, reply, expected):
    greeting = b"WELCOME UNITY-MCP 1" + (b" FRAMING=1" if framed else b"") + b"\n"
    body = struct.pack(">Q", len(reply)) + reply if framed else reply
    chunks = [bytes([value]) for value in body] if fragmented else [body]
    peer = ProbePeer([greeting, *chunks])
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    # An unframed stream is complete as soon as one whole JSON value arrives.
    expected = expected or (not framed and fragmented and reply.endswith(b" trailing"))
    assert discovery.PortDiscovery._try_probe_unity_mcp(6400) is expected
    assert peer.closed


@pytest.mark.parametrize("framed, limit", [(False, 512), (True, 10000)])
def test_legacy_pong_cannot_exceed_original_size_limit(monkeypatch, framed, limit):
    reply = b"{" + b" " * limit + b'"message":"pong"}'
    greeting = b"WELCOME UNITY-MCP 1" + (b" FRAMING=1" if framed else b"") + b"\n"
    peer = ProbePeer(
        [greeting, struct.pack(">Q", len(reply)), reply] if framed else [greeting, reply]
    )
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    assert discovery.PortDiscovery._try_probe_unity_mcp(6400) is False
    assert peer.chunks, "Oversized replies must be rejected without draining the peer"


def test_unframed_pong_fragments_share_the_greeting_deadline(monkeypatch):
    clock = [0.0]
    peer = ProbePeer([b"WELCOME UNITY-MCP 1\n", b'{"message":', b'"pong"}'])
    original_recv = peer.recv

    def delayed_receive(count):
        if peer.sent:
            clock[0] += 0.2
        return original_recv(count)

    peer.recv = delayed_receive
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    monkeypatch.setattr(discovery, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    assert discovery.PortDiscovery._try_probe_unity_mcp(6400) is False


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


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    token = "owned-discovery-test-credential-" + "x" * 32
    challenge, session = "b" * 64, "c" * 32
    host = SimpleNamespace(active=None, generation="a" * 32, peers=[], commands=[])

    class AuthenticatedPeer(ProbePeer):
        def __init__(self):
            self.generation = host.generation
            super().__init__([CURRENT.replace(b"a" * 32, self.generation.encode("ascii"))])
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
            request = {"type": "ping"} if payload == b"ping" else json.loads(payload)
            if request["type"] == "ping":
                assert host.active is self
                reply = {"status": "success", "result": {"message": "pong"}}
            elif request["type"] == "manage_scene":
                assert host.active is self and request["params"] == {"action": "get_active"}
                host.commands.append(self.generation)
                reply = {"status": "success", "result": {"editor": self.generation}}
            else:
                generation = self.generation
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
                    "capabilities": ["native_arbitration_v1"],
                    "arbitration_generation": "f" * 32,
                    "proof": authentication_proof(
                        token, ["server", generation, challenge, nonce, session]
                    ),
                }
            if "arbitration" in request:
                reply = {
                    "native_arbitration": {
                        **request["arbitration"],
                        "disposition": "started",
                        "retryable": False,
                    },
                    "payload": reply,
                }
            encoded = json.dumps(reply).encode("ascii")
            self.chunks.append(struct.pack(">Q", len(encoded)) + encoded)

    def connect(*_):
        peer = AuthenticatedPeer()
        host.peers.append(peer)
        return peer

    monkeypatch.setattr(discovery.socket, "create_connection", connect)
    monkeypatch.setattr(connections, "read_stdio_token", lambda _: token)
    monkeypatch.delenv("UNITY_MCP_DEFAULT_INSTANCE", raising=False)
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))

    def publish(name, project_hash, modified, **fields):
        path = tmp_path / f"unity-mcp-status-{project_hash}.json"
        path.write_text(
            json.dumps({"project_path": f"/Owned/{name}/Assets", "unity_port": 6400, **fields}),
            encoding="utf-8",
        )
        os.utime(path, (modified, modified))

    publish("Main", "deadbeef", 1, listener_generation=host.generation)
    host.publish = publish
    host.pool = UnityConnectionPool()
    monkeypatch.setattr(connections, "_unity_connection_pool", host.pool)
    yield host
    host.pool.disconnect_all()


def test_discovery_preserves_working_mutually_authenticated_client(bridge):
    connection = connections.UnityConnection(port=6400, instance_id="Main@deadbeef")
    try:
        assert connection.connect()
        assert connection.send_command("ping", {}) == {"message": "pong"}
        before = connection.authenticated_session
        pool = connections.UnityConnectionPool()
        pool._connections[connection.instance_id] = connection
        assert [instance.id for instance in pool.discover_all_instances()] == [
            connection.instance_id
        ]
        discovery_probe, active, probe = bridge.peers
        assert discovery_probe.closed and not discovery_probe.sent
        assert probe.closed and probe.sent == []
        assert bridge.active is active and not active.closed
        assert connection.authenticated_session is before
        assert connection.send_command("ping", {}) == {"message": "pong"}
    finally:
        connection.disconnect()


def scene(instance=None):
    return connections.send_command_with_retry(
        "manage_scene", {"action": "get_active"}, instance_id=instance
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"reason": "starting", "listener_generation": None},
        {"reason": "port_busy", "reloading": True},
        {"reason": "ready"},
        {"listener_generation": "d" * 32},
    ],
)
def test_newer_foreign_heartbeat_cannot_dispatch_to_bound_project(bridge, fields):
    bridge.publish("Other", "cafebabe", 2, **fields)
    with pytest.raises(ConnectionError, match="not found"):
        scene("Other@cafebabe")
    assert bridge.pool._connections == {} and not bridge.commands
    assert scene() == {"editor": bridge.generation}
    assert bridge.commands == [bridge.generation]


@pytest.mark.asyncio
async def test_operation_target_and_sender_only_admit_authenticated_project(bridge):
    from services.registry.operation_target import capture_target

    bridge.publish("Other", "cafebabe", 2, reason="starting")
    wrong = "Other@cafebabe"
    params = {"action": "get_active"}
    try:
        assert await capture_target(wrong) is None
        refused = await connections.async_send_command_with_retry(
            "manage_scene", params, instance_id=wrong
        )
        assert refused.success is False and wrong in refused.error
        assert not bridge.commands and bridge.pool._connections == {}
        target = await capture_target("Main@deadbeef")
        assert target.negotiated and target.is_current()
        assert target.transport_generation.startswith(bridge.generation + ":")
        reply = await connections.async_send_command_with_retry("manage_scene", params)
        assert reply == {"editor": bridge.generation}
        assert bridge.commands == [bridge.generation]
    finally:
        await bridge.pool.aclose()


def test_conflicting_claims_for_live_generation_fail_closed(bridge):
    bridge.publish("Other", "cafebabe", 2, listener_generation=bridge.generation)
    with pytest.raises(ConnectionError, match="No Unity Editor instances"):
        scene("Other@cafebabe")
    assert bridge.pool._connections == {} and not bridge.commands


@pytest.mark.parametrize("registry_constructor", [False, True])
def test_port_reused_after_discovery_never_authenticates_or_dispatches(
    bridge, registry_constructor
):
    if registry_constructor:
        connection = connections.UnityConnection(instance_id="Main@deadbeef")
    else:
        bridge.pool.resolve_instance("Main@deadbeef")
    bridge.generation = "d" * 32
    if registry_constructor:
        assert not connection.connect()
        assert connection.sock is None
    else:
        with pytest.raises(ConnectionError):
            scene("Main@deadbeef")
    assert bridge.pool._connections == {} and not bridge.commands
    assert all(peer.closed and not peer.sent for peer in bridge.peers)


@pytest.mark.parametrize("instance,port", [("Other@cafebabe", 6400), ("Main@deadbeef", 6401)])
def test_explicit_constructor_port_cannot_override_selected_project(bridge, instance, port):
    connection = connections.UnityConnection(port=port, instance_id=instance)
    with pytest.raises(StdioAuthenticationError):
        connection.send_command("manage_scene", {"action": "get_active"}, max_attempts=0)
    assert connection.sock is None
    assert not bridge.commands
    assert all(peer.closed and not peer.sent for peer in bridge.peers)


def test_implicit_constructor_without_matching_heartbeat_does_not_dial_raw_port(bridge, tmp_path):
    (tmp_path / "unity-mcp-status-deadbeef.json").unlink()
    with pytest.raises(ConnectionError, match="No Unity Editor instances"):
        connections.UnityConnection()
    assert not bridge.peers and not bridge.commands


def test_selected_authenticated_project_cannot_downgrade_to_legacy(bridge, monkeypatch):
    target = bridge.pool.resolve_instance("Main@deadbeef")
    monkeypatch.setenv("UNITY_MCP_STDIO_ALLOW_LEGACY", "1")
    peer = type(bridge.peers[0])()
    peer.chunks = [b"WELCOME UNITY-MCP 1 FRAMING=1\n"]
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *_: peer)
    with pytest.raises(ConnectionError):
        scene(target.id)
    assert peer.closed and not peer.sent
    assert bridge.pool._connections == {} and not bridge.commands


def test_legacy_selection_on_reused_port_cannot_authenticate_an_unrelated_v2_peer(
    bridge, monkeypatch
):
    bridge.publish("Main", "deadbeef", 2)
    monkeypatch.setenv("UNITY_MCP_STDIO_ALLOW_LEGACY", "1")
    pong = b'{"message":"pong"}'
    peer = ProbePeer([b"WELCOME UNITY-MCP 1 FRAMING=1\n", struct.pack(">Q", len(pong)) + pong])
    with monkeypatch.context() as patch:
        patch.setattr(discovery.socket, "create_connection", lambda *_: peer)
        target = bridge.pool.resolve_instance("Main@deadbeef")
    assert target.listener_generation is None
    with pytest.raises(ConnectionError):
        scene(target.id)
    assert bridge.pool._connections == {} and not bridge.commands
    assert all(peer.closed and not peer.sent for peer in bridge.peers)


def test_same_port_new_project_launch_replaces_authenticated_pool_socket(bridge):
    assert scene() == {"editor": bridge.generation}
    old = bridge.active
    bridge.generation = "e" * 32
    bridge.publish("Main", "deadbeef", 3, listener_generation=bridge.generation)
    bridge.pool.resolve_instance("Main@deadbeef", force_refresh=True)
    assert scene("Main@deadbeef") == {"editor": bridge.generation}
    assert old.closed and bridge.active is not old
    assert bridge.commands == ["a" * 32, "e" * 32]
