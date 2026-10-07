from transport.legacy.unity_connection import UnityConnection
import sys
import json
import struct
import socket
import threading
import time
import select
from pathlib import Path
from types import SimpleNamespace

import pytest
import transport.legacy.unity_connection as connection_module

# locate server src dynamically to avoid hardcoded layout assumptions
ROOT = Path(__file__).resolve().parents[2]  # tests/integration -> tests -> Server
candidates = [
    ROOT / "src",
]
SRC = next((p for p in candidates if p.exists()), None)
if SRC is None:
    searched = "\n".join(str(p) for p in candidates)
    pytest.skip(
        "MCP for Unity server source not found. Tried:\n" + searched,
        allow_module_level=True,
    )
# Tests can now import directly from parent package


def start_dummy_server(greeting: bytes, respond_ping: bool = False):
    """Start a minimal TCP server for handshake tests."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    ready = threading.Event()

    def _run():
        ready.set()
        conn, _ = sock.accept()
        conn.settimeout(1.0)
        if greeting:
            conn.sendall(greeting)
        if respond_ping:
            try:
                # Read exactly n bytes helper
                def _read_exact(n: int) -> bytes:
                    buf = b""
                    while len(buf) < n:
                        chunk = conn.recv(n - len(buf))
                        if not chunk:
                            break
                        buf += chunk
                    return buf

                header = _read_exact(8)
                if len(header) == 8:
                    length = struct.unpack(">Q", header)[0]
                    payload = _read_exact(length)
                    if payload == b'{"type":"ping"}':
                        resp = b'{"type":"pong"}'
                        conn.sendall(struct.pack(">Q", len(resp)) + resp)
            except Exception:
                pass
        time.sleep(0.1)
        try:
            conn.close()
        except Exception:
            pass
        finally:
            sock.close()

    threading.Thread(target=_run, daemon=True).start()
    ready.wait()
    return port


def start_handshake_enforcing_server():
    """Server that drops connection if client sends data before handshake."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    ready = threading.Event()

    def _run():
        ready.set()
        conn, _ = sock.accept()
        # If client sends any data before greeting, disconnect (poll briefly)
        try:
            conn.setblocking(False)
            deadline = time.time() + 0.15  # short, reduces race with legitimate clients
            while time.time() < deadline:
                r, _, _ = select.select([conn], [], [], 0.01)
                if r:
                    try:
                        peek = conn.recv(1, socket.MSG_PEEK)
                    except BlockingIOError:
                        peek = b""
                    except Exception:
                        peek = b"\x00"
                    if peek:
                        conn.close()
                        sock.close()
                        return
            # No pre-handshake data observed; send greeting
            conn.setblocking(True)
            conn.sendall(b"WELCOME UNITY-MCP 1 FRAMING=1\n")
            time.sleep(0.1)
        finally:
            try:
                conn.close()
            finally:
                sock.close()

    threading.Thread(target=_run, daemon=True).start()
    ready.wait()
    return port


def test_handshake_requires_framing():
    port = start_dummy_server(b"MCP/0.1\n")
    conn = UnityConnection(host="127.0.0.1", port=port, allow_legacy_auth=True)
    assert conn.connect() is False
    assert conn.sock is None


def test_small_frame_ping_pong():
    port = start_dummy_server(b"WELCOME UNITY-MCP 1 FRAMING=1\n", respond_ping=True)
    conn = UnityConnection(host="127.0.0.1", port=port, allow_legacy_auth=True)
    try:
        assert conn.connect() is True
        assert conn.use_framing is True
        payload = b'{"type":"ping"}'
        conn.sock.sendall(struct.pack(">Q", len(payload)) + payload)
        resp = conn.receive_full_response(conn.sock)
        assert json.loads(resp.decode("utf-8"))["type"] == "pong"
    finally:
        conn.disconnect()


def test_unframed_data_disconnect():
    port = start_handshake_enforcing_server()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    sock.settimeout(1.0)
    sock.sendall(b"BAD")
    time.sleep(0.4)
    try:
        data = sock.recv(1024)
        assert data == b""
    except (ConnectionResetError, ConnectionAbortedError):
        # Some platforms raise instead of returning empty bytes when the
        # server closes the connection after detecting pre-handshake data.
        pass
    finally:
        sock.close()


@pytest.mark.parametrize("prefix_length", [0, 1, 27])
def test_zero_length_payload_heartbeat(monkeypatch, prefix_length):
    # A TCP read may contain the greeting newline and subsequent framed bytes.
    greeting = b"WELCOME UNITY-MCP 1 FRAMING=1\n"
    payload = b'{"type":"pong"}'
    frames = struct.pack(">Q", 0) + struct.pack(">Q", len(payload)) + payload
    wire = greeting + frames
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    sock.settimeout(2)
    port = sock.getsockname()[1]
    prefix_read, sent, finished = threading.Event(), threading.Event(), threading.Event()
    failures, observed = [], []

    def _run():
        try:
            with sock.accept()[0] as peer:
                peer.settimeout(2)
                if prefix_length:
                    peer.sendall(greeting[:prefix_length])
                    assert prefix_read.wait(2)
                peer.sendall(wire[prefix_length:])
                sent.set()
                assert finished.wait(2)
        except Exception as exc:
            failures.append(exc)
        finally:
            sock.close()

    original_connect = socket.create_connection

    class ObservedSocket:
        def __init__(self, actual):
            self.actual = actual
            self.first = True

        def __getattr__(self, name):
            return getattr(self.actual, name)

        def recv(self, count):
            # Preserve legal recv size; force the coalescing schedule independently
            # of OS packet splitting and CPU contention, using actual TCP bytes.
            expected = prefix_length if self.first and prefix_length else len(wire) - prefix_length
            if not (self.first and prefix_length):
                assert sent.wait(2)
            data = self.actual.recv(min(count, expected))
            if count >= expected:
                while len(data) < expected:
                    chunk = self.actual.recv(expected - len(data))
                    assert chunk
                    data += chunk
            observed.append(data)
            self.first = False
            prefix_read.set()
            return data

    def create_connection(*args, **kwargs):
        return ObservedSocket(original_connect(*args, **kwargs))

    monkeypatch.setattr(socket, "create_connection", create_connection)
    thread = threading.Thread(target=_run)
    thread.start()
    conn = UnityConnection(host="127.0.0.1", port=port, allow_legacy_auth=True)
    try:
        assert conn.connect() is True, (
            f"prefix={prefix_length}; recv_hex={[data.hex() for data in observed]}"
        )
        resp = conn.receive_full_response(conn.sock)
        assert resp == payload
    finally:
        conn.disconnect()
        finished.set()
        prefix_read.set()
        thread.join(3)
        assert not thread.is_alive()
        assert not failures


@pytest.mark.parametrize("outer_deadline", [None, 0.5])
def test_fragmented_greeting_reads_share_absolute_deadline(monkeypatch, outer_deadline):
    clock = SimpleNamespace(now=0.0)
    observed = []

    class FragmentedSocket:
        closed = False
        timeout = None

        def setsockopt(self, *args):
            pass

        def settimeout(self, timeout):
            self.timeout = timeout

        def gettimeout(self):
            return self.timeout

        def recv(self, count):
            observed.append(self.timeout)
            clock.now += 0.25
            return b"W"

        def close(self):
            self.closed = True

    peer = FragmentedSocket()
    monkeypatch.setattr(connection_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(connection_module.socket, "create_connection", lambda *args: peer)
    monkeypatch.setattr(connection_module.config, "handshake_timeout", 1.0)
    conn = UnityConnection(host="127.0.0.1", port=1, allow_legacy_auth=True)
    assert conn.connect(deadline=outer_deadline) is False
    budget = 1.0 if outer_deadline is None else outer_deadline
    assert observed == [budget - index * 0.25 for index in range(int(budget / 0.25))], (
        f"per_recv_timeouts={observed}; expected_budget={budget}"
    )
    assert clock.now == budget and peer.closed and conn.sock is None
