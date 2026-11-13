"""Connection resilience when a domain reload leaves the Unity socket half-open."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from core.config import config
import transport.legacy.unity_connection as uc
from models.models import UnityInstanceInfo
from tests.timing import watchdog_timeout
from transport.legacy.port_discovery import PortDiscovery
from transport.legacy.stdio_port_registry import StdioPortRegistry
from transport.legacy.unity_connection import UnityConnection


def _start_silent_bridge():
    """Accept and handshake, then never answer (a wedged half-open bridge)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def serve():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                client, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            accepted.append(client)
            try:
                client.sendall(b"WELCOME UNITY-MCP 1 FRAMING=1\n")
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    return srv, port, stop, accepted


def _write_reloading_status(tmp_path):
    status_dir = Path(tmp_path) / ".unity-mcp"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "unity-mcp-status-deadbeef.json").write_text(
        json.dumps({"reloading": True, "reason": "reloading", "project_path": "x"})
    )


@pytest.fixture
def silent_bridge(monkeypatch, tmp_path):
    srv, port, stop, accepted = _start_silent_bridge()
    monkeypatch.setattr(uc.Path, "home", lambda: Path(tmp_path))
    monkeypatch.setattr(uc.stdio_port_registry, "get_port", lambda instance_id=None: port)
    monkeypatch.setattr(uc.stdio_port_registry, "get_instance", lambda instance_id: None)
    target = UnityInstanceInfo(
        id="Repro@deadbeef",
        name="Repro",
        hash="deadbeef",
        path="/Repro/Assets",
        port=port,
        status="running",
    )
    pool = uc.UnityConnectionPool()
    monkeypatch.setattr(pool, "resolve_instance", lambda _: target)
    monkeypatch.setattr(uc, "_unity_connection_pool", pool)
    monkeypatch.setattr(config, "connection_timeout", 1.0)
    monkeypatch.setattr(config, "command_total_timeout", 2.0, raising=False)
    try:
        yield port
    finally:
        stop.set()
        try:
            srv.close()
        except OSError:
            pass
        for client in accepted:
            try:
                client.close()
            except OSError:
                pass


def test_send_command_against_wedged_socket_is_bounded(silent_bridge, monkeypatch):
    """A connection_timeout longer than the total budget must not let a single blocking
    recv overrun the ceiling: the deadline caps each recv, so the call still stops near
    command_total_timeout rather than connection_timeout."""
    monkeypatch.setattr(config, "connection_timeout", 5.0)
    conn = UnityConnection(port=silent_bridge, instance_id="Repro@deadbeef", allow_legacy_auth=True)
    start = time.monotonic()
    response = conn.send_command("get_editor_state", {})
    assert response.success is False
    assert response.data["reason"] == "outcome_unknown"
    assert response.hint == "inspect_state_before_retry"
    assert "timeout" in response.error.lower() or "deadline" in response.error.lower()
    assert time.monotonic() - start < 4.0


def test_send_command_with_retry_is_bounded_by_deadline(silent_bridge, tmp_path, monkeypatch):
    """The reload-wait loop honours command_total_timeout, not just max_wait_s."""
    conn = UnityConnection(port=silent_bridge, instance_id="Repro@deadbeef", allow_legacy_auth=True)
    monkeypatch.setattr(uc, "get_unity_connection", lambda instance_id=None: conn)
    _write_reloading_status(tmp_path)

    start = time.monotonic()
    resp = uc.send_command_with_retry("get_editor_state", {}, instance_id="Repro@deadbeef")
    assert time.monotonic() - start < 6.0
    assert resp.data["reason"] == "reloading"


def test_reload_signal_drops_socket_for_fresh_reconnect(silent_bridge, tmp_path):
    """A 'reloading' status drops the socket so the next command reconnects."""
    conn = UnityConnection(port=silent_bridge, instance_id="Repro@deadbeef", allow_legacy_auth=True)
    assert conn.connect() is True
    assert conn.sock is not None
    _write_reloading_status(tmp_path)

    response = conn.send_command("get_editor_state", {})
    assert conn.sock is None
    assert response.data["reason"] == "reloading"


def test_connection_failure_refreshes_cached_port_before_backoff(monkeypatch) -> None:
    instance_id = "Repro@deadbeef"
    discovered_port = 6400
    discoveries = []

    def discover_instances() -> list[UnityInstanceInfo]:
        discoveries.append(discovered_port)
        return [
            UnityInstanceInfo(
                id=instance_id,
                name="Repro",
                path="/tmp/Repro/Assets",
                hash="deadbeef",
                port=discovered_port,
                status="running",
            )
        ]

    registry = StdioPortRegistry()
    monkeypatch.setattr(
        "transport.legacy.stdio_port_registry.PortDiscovery.discover_all_unity_instances",
        discover_instances,
    )
    monkeypatch.setattr(config, "port_registry_ttl", 60.0)

    assert registry.get_port(instance_id) == 6400
    discovered_port = 6401

    monkeypatch.setattr(uc, "stdio_port_registry", registry)
    conn = UnityConnection(port=6400, instance_id=instance_id)
    attempted_ports: list[int] = []
    ports_before_backoff: list[int] = []

    def fail_connect(connect_timeout: float | None = None, deadline: float | None = None) -> bool:
        attempted_ports.append(conn.port)
        return False

    monkeypatch.setattr(conn, "connect", fail_connect)
    monkeypatch.setattr(uc.time, "sleep", lambda _seconds: ports_before_backoff.append(conn.port))

    with pytest.raises(ConnectionError, match="Could not connect to Unity"):
        conn.send_command("get_editor_state", {}, max_attempts=1)

    assert attempted_ports == [6400, 6401]
    assert ports_before_backoff == [6401]
    assert discoveries == [6400, 6401]  # Initial cache fill and one retry refresh.


@pytest.fixture
def failed_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(uc, "read_status_file", lambda _: None)
    registry = StdioPortRegistry()
    monkeypatch.setattr(uc, "stdio_port_registry", registry)
    conn = UnityConnection(port=6400, instance_id="Repro@deadbeef")
    attempts = []
    monkeypatch.setattr(conn, "connect", lambda *a, **kw: attempts.append(conn.port) or False)
    return conn, registry, attempts


def test_terminal_connection_failure_never_starts_discovery(failed_connection, monkeypatch):
    conn, registry, attempts = failed_connection
    monkeypatch.setattr(registry, "get_instances", lambda **_: pytest.fail("terminal refresh"))
    with pytest.raises(ConnectionError, match="Could not connect"):
        conn.send_command("get_editor_state", {}, max_attempts=0)
    assert attempts == [6400]


@pytest.mark.parametrize("cancel", [False, True])
def test_retry_worker_exits_while_registry_lock_remains_held(
    failed_connection, monkeypatch, cancel
):
    conn, registry, attempts = failed_connection
    waiting, cancelled = threading.Event(), threading.Event()
    original_get = registry.get_instances

    def get(**kwargs):
        waiting.set()
        return original_get(**kwargs)

    monkeypatch.setattr(registry, "get_instances", get)
    monkeypatch.setattr(
        PortDiscovery, "discover_all_unity_instances", lambda: pytest.fail("late scan")
    )
    with ThreadPoolExecutor(max_workers=1) as executor, registry._lock:
        deadline = time.monotonic() + (10 if cancel else 0.15)
        worker = executor.submit(
            conn.send_command, "get_editor_state", {}, 1, deadline, _cancelled=cancelled
        )
        assert waiting.wait(watchdog_timeout(1))
        if cancel:
            cancelled.set()
        with pytest.raises(
            uc.CommandResumeAbandoned if cancel else TimeoutError,
            match=None if cancel else "total deadline",
        ):
            worker.result(timeout=watchdog_timeout(1))
        assert attempts == [6400]
        assert registry._last_refresh is None


@pytest.mark.parametrize("cancel", [False, True])
def test_retry_probe_uses_original_budget_and_leaves_no_partial_cache(
    failed_connection, monkeypatch, tmp_path, cancel
):
    conn, registry, attempts = failed_connection
    # Two status files exercise the real discovery loop after a slow first listener.
    for name, key, port in [("Repro", "deadbeef", 6400), ("Other", "cafebabe", 6401)]:
        (tmp_path / f"unity-mcp-status-{key}.json").write_text(
            json.dumps({"project_path": f"/{name}/Assets", "unity_port": port}),
            encoding="utf-8",
        )
    monkeypatch.setattr(PortDiscovery, "CONNECT_TIMEOUT", 2.0)
    clock = [100.0]
    monkeypatch.setattr(uc.time, "monotonic", lambda: clock[0])
    deadline = clock[0] + 0.1
    cancelled = threading.Event()
    dial_timeouts, receive_timeouts = [], []

    class SilentPeer:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        def settimeout(self, timeout):
            receive_timeouts.append(timeout)

        def recv(self, _):
            clock[0] += receive_timeouts[-1]
            if cancel:
                cancelled.set()
            raise socket.timeout()

    peer = SilentPeer()

    def dial(address, timeout):
        dial_timeouts.append(timeout)
        return peer

    monkeypatch.setattr(socket, "create_connection", dial)
    with pytest.raises(uc.CommandResumeAbandoned if cancel else TimeoutError):
        conn.send_command("get_editor_state", {}, 1, deadline, _cancelled=cancelled)
    assert dial_timeouts == [pytest.approx(0.1)]
    assert peer.closed and receive_timeouts and clock[0] <= deadline
    if cancel:
        assert clock[0] < deadline
    assert attempts == [6400]
    assert registry._last_refresh is None
