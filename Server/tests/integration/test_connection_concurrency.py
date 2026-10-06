"""Real socket regressions for shared connection lifetime and replay safety."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import struct
import threading
import time

import pytest

from core.config import config
from models.models import UnityInstanceInfo
import transport.legacy.unity_connection as uc
from transport.legacy.unity_connection import UnityConnection


@pytest.fixture
def isolated_connection(monkeypatch, tmp_path):
    monkeypatch.setattr(uc.Path, "home", lambda: Path(tmp_path))
    monkeypatch.setattr(config, "command_total_timeout", 2.0)
    monkeypatch.setattr(uc.stdio_port_registry, "get_instances", lambda **kwargs: [])
    monkeypatch.setattr(uc.stdio_port_registry, "get_port", lambda instance_id=None: 6400)
    monkeypatch.setattr(uc.time, "sleep", lambda seconds: None)
    # Synthetic socket fixtures intentionally retain the v1 framing contract.
    conn = UnityConnection(port=6400, allow_legacy_auth=True)
    conn.sock, peer = socket.socketpair()
    conn.sock.settimeout(1.0)
    peer.settimeout(1.0)
    conn.use_framing = True
    try:
        yield conn, peer
    finally:
        conn.disconnect()
        peer.close()


def _read_frame(peer):
    def read_exact(size):
        result = bytearray()
        while len(result) < size:
            chunk = peer.recv(size - len(result))
            if not chunk:
                raise EOFError("Client closed before complete command")
            result.extend(chunk)
        return bytes(result)
    size = struct.unpack(">Q", read_exact(8))[0]
    return read_exact(size)


def _write_response(peer, response):
    payload = json.dumps(response).encode()
    peer.sendall(struct.pack(">Q", len(payload)) + payload)


@pytest.mark.parametrize("action", ["disconnect", "peer_check"])
def test_lifecycle_waits_for_completed_mutation_response(isolated_connection, monkeypatch, action):
    conn, peer = isolated_connection
    response_read = threading.Event()
    release_response = threading.Event()
    action_started = threading.Event()
    action_finished = threading.Event()
    receive = conn.receive_full_response

    def pause_after_response(*args, **kwargs):
        response = receive(*args, **kwargs)
        response_read.set()
        assert release_response.wait(2), "Controller failed to release completed response"
        return response

    monkeypatch.setattr(conn, "receive_full_response", pause_after_response)

    def complete_mutation():
        assert json.loads(_read_frame(peer))["type"] == "manage_gameobject"
        _write_response(peer, {"status": "success", "result": {"success": True}})
        if action == "peer_check":
            peer.close()  # A real FIN makes the next liveness check discard the socket.

    def lifecycle_action():
        action_started.set()
        if action == "disconnect":
            conn.disconnect()
        else:
            conn._ensure_live_connection()
        action_finished.set()

    with ThreadPoolExecutor(max_workers=3) as executor:
        bridge = executor.submit(complete_mutation)
        mutation = executor.submit(conn.send_command, "manage_gameobject", {}, 0)
        assert response_read.wait(2)
        lifecycle = executor.submit(lifecycle_action)
        assert action_started.wait(2)
        closed_during_response = action_finished.wait(0.1)
        release_response.set()
        result = mutation.result(timeout=2)
        lifecycle.result(timeout=2)
        bridge.result(timeout=2)
    assert result == {"success": True}
    assert not closed_during_response, "Lifecycle changed the socket before response timeout restoration"


def test_liveness_check_preserves_finite_socket_timeout(isolated_connection):
    conn, _ = isolated_connection
    conn.sock.settimeout(0.75)
    conn._ensure_live_connection()
    assert conn.sock.gettimeout() == 0.75


def test_closed_socket_reconnects_before_dispatching_mutation(isolated_connection):
    conn, stale_peer = isolated_connection
    stale_peer.close()
    commands = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        conn.port = listener.getsockname()[1]

        def serve():
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(2)
                peer.sendall(b"WELCOME UNITY-MCP 1 FRAMING=1\n")
                commands.append(json.loads(_read_frame(peer)))
                _write_response(peer, {"status": "success", "result": {"success": True}})

        with ThreadPoolExecutor(max_workers=1) as executor:
            bridge = executor.submit(serve)
            result = conn.send_command("manage_gameobject", {"action": "modify"}, max_attempts=0)
            bridge.result(timeout=2)
    assert result == {"success": True}
    assert commands == [{"type": "manage_gameobject", "params": {"action": "modify"}}]


def test_unity_error_does_not_reconnect_and_replay_mutation(isolated_connection, monkeypatch):
    conn, peer = isolated_connection
    reconnects = []
    monkeypatch.setattr(conn, "connect", lambda *args, **kwargs: reconnects.append(1) or False)
    with ThreadPoolExecutor(max_workers=1) as executor:
        def respond():
            _read_frame(peer)
            _write_response(peer, {"status": "error", "error": "Mutation rejected"})
        bridge = executor.submit(respond)
        with pytest.raises(Exception, match="Mutation rejected"):
            conn.send_command("manage_gameobject", {}, max_attempts=1)
        bridge.result(timeout=2)
    assert reconnects == []
    assert conn.sock is not None, "Application rejection closed a healthy protocol connection"


def test_parallel_instance_resolution_shares_one_discovery_scan(monkeypatch):
    pool = uc.UnityConnectionPool()
    selected = UnityInstanceInfo(id="Owned@abcdef", name="Owned", path="/Owned/Assets",
                                 hash="abcdef", port=6400, status="running")
    scans = []
    started = threading.Barrier(4)
    scanning = threading.Event()
    release_scan = threading.Event()

    def discover():
        scans.append(1)
        if len(scans) == 1:
            scanning.set()
            assert release_scan.wait(2)
            return [selected]
        # A parallel probe can fail while the first probe/Editor is busy.
        return []

    monkeypatch.setattr(uc.PortDiscovery, "discover_all_unity_instances", discover)

    def resolve():
        started.wait(timeout=2)
        try:
            return pool._resolve_instance_id(selected.id, pool.discover_all_instances())
        except ConnectionError as error:
            return error

    with ThreadPoolExecutor(max_workers=3) as executor:
        requests = [executor.submit(resolve) for _ in range(3)]
        started.wait(timeout=2)
        assert scanning.wait(2)
        # This interval represents controlled discovery latency, not polling the race.
        time.sleep(0.05)
        release_scan.set()
        results = [request.result(timeout=2) for request in requests]
    assert len(scans) == 1, f"Parallel resolution performed {len(scans)} redundant scans"
    assert results == [selected] * 3


def test_queued_command_expires_before_dispatch(isolated_connection):
    conn, peer = isolated_connection
    with ThreadPoolExecutor(max_workers=1) as executor:
        with conn._io_lock:
            start = time.monotonic()
            queued = executor.submit(conn.send_command, "get_editor_state", {}, 0, start + 0.1)
            with pytest.raises(TimeoutError, match="deadline waiting"):
                queued.result(timeout=1)
            assert time.monotonic() - start < 0.5
    peer.setblocking(False)
    with pytest.raises(BlockingIOError):
        peer.recv(1)


def test_rediscovered_port_update_expires_while_lifecycle_holds_lock(isolated_connection, monkeypatch):
    conn, _ = isolated_connection
    conn.disconnect()
    start_holder = threading.Event()
    holder_acquired = threading.Event()
    release_holder = threading.Event()
    monkeypatch.setattr(conn, "connect", lambda *args, **kwargs: False)
    monkeypatch.setattr(uc.stdio_port_registry, "get_port", lambda instance_id=None: 6401)

    def hold_lifecycle_lock():
        assert start_holder.wait(2)
        with conn._io_lock:
            holder_acquired.set()
            assert release_holder.wait(2)

    def discover(**kwargs):
        start_holder.set()
        assert holder_acquired.wait(2)
        return []

    monkeypatch.setattr(uc.stdio_port_registry, "get_instances", discover)
    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(hold_lifecycle_lock)
        start = time.monotonic()
        command = executor.submit(conn.send_command, "get_editor_state", {}, 0, start + 0.1)
        try:
            with pytest.raises(TimeoutError, match="deadline waiting"):
                command.result(timeout=0.36)
            assert conn.port == 6400, "Expired call changed the port"
        finally:
            release_holder.set()
            holder.result(timeout=2)


def test_lost_mutation_reply_does_not_dispatch_again(isolated_connection, monkeypatch):
    conn, peer = isolated_connection
    reconnects = []
    monkeypatch.setattr(conn, "connect", lambda *args, **kwargs: reconnects.append(1) or False)
    with ThreadPoolExecutor(max_workers=1) as executor:
        def drop_after_mutation():
            _read_frame(peer)
            peer.close()
        bridge = executor.submit(drop_after_mutation)
        response = conn.send_command("manage_gameobject", {}, max_attempts=1)
        bridge.result(timeout=2)
    assert response.success is False
    assert "Connection closed" in response.error
    assert response.hint == "inspect_state_before_retry"
    assert response.data == {"reason": "outcome_unknown", "command": "manage_gameobject"}
    assert reconnects == []


@pytest.mark.parametrize("corruption", ["oversized_frame", "invalid_json", "invalid_utf8", "invalid_shape"])
def test_malformed_reply_discards_socket_then_next_call_connects_fresh(isolated_connection, corruption):
    conn, peer = isolated_connection
    commands = []

    def corrupt_response():
        commands.append(json.loads(_read_frame(peer)))
        if corruption == "oversized_frame":
            peer.sendall(struct.pack(">Q", uc.FRAMED_MAX + 1) + b'{"status')
        else:
            payload = {"invalid_json": b'{"status', "invalid_utf8": b'\xff', "invalid_shape": b'[]'}[corruption]
            peer.sendall(struct.pack(">Q", len(payload)) + payload)

    with ThreadPoolExecutor(max_workers=1) as executor:
        bridge = executor.submit(corrupt_response)
        response = conn.send_command("manage_gameobject", {"action": "modify"}, max_attempts=2)
        bridge.result(timeout=2)
    assert response.success is False
    assert response.data == {"reason": "outcome_unknown", "command": "manage_gameobject"}
    assert response.hint == "inspect_state_before_retry"
    assert conn.sock is None, "Malformed reply left its framing bytes in a reusable connection"
    try:
        assert peer.recv(1) == b"", "Malformed response socket was not closed"
    except (ConnectionResetError, ConnectionAbortedError):
        # Closing with unread framing bytes can produce a reset on Windows.
        pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        conn.port = listener.getsockname()[1]

        def healthy_response():
            fresh_peer, _ = listener.accept()
            with fresh_peer:
                fresh_peer.settimeout(2)
                fresh_peer.sendall(b"WELCOME UNITY-MCP 1 FRAMING=1\n")
                commands.append(json.loads(_read_frame(fresh_peer)))
                _write_response(fresh_peer, {"status": "success", "result": {"success": True}})

        with ThreadPoolExecutor(max_workers=1) as executor:
            bridge = executor.submit(healthy_response)
            result = conn.send_command("get_editor_state", {}, max_attempts=0)
            bridge.result(timeout=2)
    assert result == {"success": True}
    assert commands == [
        {"type": "manage_gameobject", "params": {"action": "modify"}},
        {"type": "get_editor_state", "params": {}},
    ]
