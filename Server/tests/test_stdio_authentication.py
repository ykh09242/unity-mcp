"""Authenticated stdio negotiation against wholly owned ephemeral TCP peers."""

import socket
import json
import secrets
import struct
import threading
from contextlib import contextmanager

import pytest

import transport.legacy.unity_connection as module
from core.config import config
from transport.legacy.stdio_auth import authentication_proof
from transport.legacy.stdio_auth import StdioAuthenticationError


@contextmanager
def greeting_peer(greeting):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    failures = []

    def serve():
        try:
            with listener.accept()[0] as peer:
                peer.settimeout(1)
                peer.sendall(greeting)
                try:
                    peer.recv(1024)
                except socket.timeout:
                    pass
        except OSError as exc:
            failures.append(exc)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        thread.join(3)
        listener.close()
        assert not thread.is_alive()
        assert not failures


@pytest.mark.parametrize("legacy_opt_in", [False, True])
def test_failed_v2_authentication_never_downgrades_or_retries_command(legacy_opt_in):
    generation, challenge = "a" * 32, "b" * 64
    banner = f"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER={generation} CHALLENGE={challenge}\n".encode(
        "ascii"
    )
    reads = []

    def unavailable(server_generation):
        reads.append(server_generation)
        return None

    with greeting_peer(banner) as port:
        conn = module.UnityConnection(
            port=port, auth_token_provider=unavailable, allow_legacy_auth=legacy_opt_in
        )
        try:
            with pytest.raises(StdioAuthenticationError, match="unavailable"):
                conn.send_command("manage_gameobject", {"action": "create"}, max_attempts=3)
            assert reads == [generation] and conn.session_generation is None and conn.sock is None
        finally:
            conn.disconnect()


@pytest.mark.parametrize(
    "greeting",
    [
        b"WELCOME UNITY-MCP 1 FRAMING=1\n",
        b"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER="
        + b"a" * 32
        + b" CHALLENGE="
        + b"b" * 64
        + b"\n",
    ],
)
def test_default_client_rejects_peer_without_verified_auth(monkeypatch, greeting):
    # Given an owned peer that cannot provide mutual launch authentication.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.delenv("UNITY_MCP_STDIO_ALLOW_LEGACY", raising=False)
    monkeypatch.setattr(module, "read_stdio_token", lambda generation: None, raising=False)
    with greeting_peer(greeting) as port:
        connection = module.UnityConnection(host="127.0.0.1", port=port)
        try:
            # When the default client negotiates this fresh socket.
            connected = connection.connect()
            # Then no unverified socket is published as usable.
            assert connected is False
            assert connection.sock is None
        finally:
            connection.disconnect()


TOKEN = "owned-test-token-" + "x" * 32


def read_frame(peer):
    def exact(count):
        data = bytearray()
        while len(data) < count:
            chunk = peer.recv(count - len(data))
            assert chunk
            data.extend(chunk)
        return bytes(data)

    length = struct.unpack(">Q", exact(8))[0]
    assert 1 <= length <= 1024
    return json.loads(exact(length))


@contextmanager
def authenticated_peer(reply_kind="valid", connections=1):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(connections)
    listener.settimeout(2)
    observed = []
    failures = []
    generation = secrets.token_hex(16)

    def serve():
        try:
            for _ in range(connections):
                with listener.accept()[0] as peer:
                    peer.settimeout(2)
                    challenge = secrets.token_hex(32)
                    banner = f"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER={generation} CHALLENGE={challenge}\n"
                    peer.sendall(banner.encode("ascii"))
                    request = read_frame(peer)
                    observed.append(request)
                    nonce = request["client_nonce"]
                    assert request["proof"] == authentication_proof(
                        TOKEN, ["client", generation, challenge, nonce]
                    )
                    session = secrets.token_hex(16)
                    reply = dict(
                        type="authenticated",
                        version=2,
                        session_id=session,
                        proof=authentication_proof(
                            TOKEN, ["server", generation, challenge, nonce, session]
                        ),
                    )
                    if reply_kind == "forged":
                        reply["proof"] = "0" * 64
                    if reply_kind == "wrong_session":
                        reply["session_id"] = "invalid"
                    if reply_kind == "refused":
                        reply = dict(type="error", error="rejected")
                    payload = json.dumps(reply).encode("ascii")
                    if reply_kind == "oversized":
                        peer.sendall(struct.pack(">Q", 1025))
                    else:
                        peer.sendall(struct.pack(">Q", len(payload)) + payload)
                    peer.recv(1024)
        except OSError as exc:
            failures.append(exc)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield listener.getsockname()[1], observed
    finally:
        thread.join(3)
        listener.close()
        assert not thread.is_alive()
        assert not failures


@pytest.mark.parametrize("reply_kind", ["valid", "forged", "wrong_session", "refused", "oversized"])
def test_mutual_proof_required_before_connection_publication(monkeypatch, reply_kind):
    # Given an owned launch credential and a fresh challenge, even with legacy opt-in.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    with authenticated_peer(reply_kind) as (port, requests):
        connection = module.UnityConnection(
            host="127.0.0.1",
            port=port,
            auth_token_provider=lambda generation: TOKEN,
            allow_legacy_auth=True,
        )
        try:
            # When the server offers its reciprocal proof.
            connected = connection.connect()
            # Then only a valid proof publishes an authenticated generation.
            assert connected is (reply_kind == "valid")
            assert bool(connection.session_generation) is (reply_kind == "valid")
            assert len(requests) == 1
            assert TOKEN not in json.dumps(requests)
        finally:
            connection.disconnect()


def test_reconnect_changes_authenticated_generation_on_same_connection_object(monkeypatch):
    # Given one owned listener and one reusable connection object.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    with authenticated_peer(connections=2) as (port, requests):
        connection = module.UnityConnection(
            host="127.0.0.1", port=port, auth_token_provider=lambda generation: TOKEN
        )
        assert connection.connect()
        first = connection.session_generation
        # When a real reconnect authenticates against the same listener generation.
        connection.disconnect()
        assert connection.session_generation is None
        assert connection.connect()
        second = connection.session_generation
        connection.disconnect()
        # Then shared/cache identity changes and client proofs are fresh.
        assert first != second
        assert first.split(":")[0] == second.split(":")[0]
        assert requests[0]["client_nonce"] != requests[1]["client_nonce"]


def test_old_banner_requires_deliberate_legacy_opt_in(monkeypatch):
    # Given an owned v1 compatibility peer and explicit opt-in.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    with greeting_peer(b"WELCOME UNITY-MCP 1 FRAMING=1\n") as port:
        connection = module.UnityConnection(host="127.0.0.1", port=port, allow_legacy_auth=True)
        try:
            # When the old peer negotiates its original framing contract.
            assert connection.connect()
            # Then it remains unverified and cannot supply shared/cache identity.
            assert connection.use_framing
            assert connection.session_generation is None
        finally:
            connection.disconnect()
