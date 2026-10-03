"""Complete legacy module entry points with inert framing/discovery boundaries."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


COMMON = '''
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time
from unittest.mock import patch
sys.path.insert(0, "src")
def deny(*args, **kwargs):
    raise AssertionError("real network/discovery forbidden")
socket.socket.connect = deny
from core.config import config
from models.models import UnityInstanceInfo
from transport.legacy import unity_connection as module
config.http_remote_hosted = False
module.PortDiscovery.discover_all_unity_instances = deny
module.stdio_port_registry.get_instances = deny
module.stdio_port_registry.get_instance = deny
module.stdio_port_registry.get_port = deny
home = Path(os.environ["APPDATA"]).parent / "owned-status-home"
(home / ".unity-mcp").mkdir(parents=True)
module.Path.home = lambda: home
class FramedSocket:
    def __init__(self, endpoint, handshake=False):
        self.endpoint = endpoint
        self.buffer = bytearray(b"FRAMING=1\\n" if handshake else b"")
        self.closed = False
        self.timeout = 1.0
        self.blocking = True
        self.requests = []
        self.on_close = lambda: None
    def setsockopt(self, *args):
        pass
    def getblocking(self):
        return self.blocking
    def setblocking(self, value):
        self.blocking = value
    def gettimeout(self):
        return self.timeout
    def settimeout(self, value):
        self.timeout = value
    def recv(self, count, flags=0):
        if flags:
            raise BlockingIOError()
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result
    def sendall(self, data):
        assert not self.closed
        if data.startswith(b"{"):
            self.requests.append(json.loads(data))
            payload = json.dumps({"status": "success", "result": {"endpoint": self.endpoint}}).encode()
            self.buffer.extend(struct.pack(">Q", len(payload)) + payload)
    def close(self):
        self.on_close()
        self.closed = True
created = []
def create_connection(endpoint, timeout):
    sock = FramedSocket(endpoint[1], handshake=True)
    created.append((endpoint, sock))
    return sock
module.socket.create_connection = create_connection
'''


def _run(source, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    env.pop("UNITY_MCP_DEFAULT_INSTANCE", None)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(COMMON) + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("instance_id,statuses,blocked", [
    ("Selected@aaaa1111", {"bbbb2222": True}, False),
    ("aaaa1111", {"aaaa1111": False, "bbbb2222": True}, False),
    ("aaaa1111", {"aaaa1111": True, "bbbb2222": False}, True),
    ("Selected@aaaa1111", {"aaaa1111": True, "bbbb2222": False}, True),
    ("Selected@aaaa1111", {"aaaa1111": False, "bbbb2222": True}, False),
    ("Parent@Selected@aaaa1111", {"aaaa1111": False, "bbbb2222": True}, False),
    (None, {"aaaa1111": False, "bbbb2222": True}, True),
    (None, {"aaaa1111": True, "bbbb2222": False}, False),
])
def test_preflight_uses_selected_status_only(tmp_path, instance_id, statuses, blocked):
    _run(f'''
        for index, (hash_id, reloading) in enumerate({statuses!r}.items()):
            path = home / ".unity-mcp" / f"unity-mcp-status-{{hash_id}}.json"
            path.write_text(json.dumps({{"reloading": reloading}}), encoding="utf-8")
            os.utime(path, (1000 + index, 1000 + index))
        conn = module.UnityConnection(port=1111, instance_id={instance_id!r})
        old = FramedSocket(1111)
        conn.sock, conn.use_framing = old, True
        def closed_under_lock():
            assert conn._io_lock.locked()
        old.on_close = closed_under_lock
        result = conn.send_command("fixture_query", {{"zero": 0}}, max_attempts=0)
        if {blocked!r}:
            assert result.success is False and result.hint == "retry"
            assert old.closed and conn.sock is None and old.requests == []
        else:
            assert result == {{"endpoint": 1111}}
            assert not old.closed and conn.sock is old and not created
            assert old.requests == [{{"type": "fixture_query", "params": {{"zero": 0}}}}]
    ''', tmp_path)


@pytest.mark.parametrize("changed", [False, True])
def test_cached_port_change_reconnects_with_framing(tmp_path, changed):
    _run(f'''
        pool = module.UnityConnectionPool()
        target = UnityInstanceInfo(id="Selected@aaaa1111", name="Selected", hash="aaaa1111",
                                   path="owned-fixture/Assets", port={2222 if changed else 1111}, status="running")
        pool._known_instances, pool._last_full_scan = {{target.id: target}}, time.time()
        conn = module.UnityConnection(port=1111, instance_id=target.id)
        old = FramedSocket(1111)
        conn.sock, conn.use_framing = old, True
        def closed_under_lock():
            assert conn._io_lock.locked()
        old.on_close = closed_under_lock
        pool._connections[target.id] = conn
        module._unity_connection_pool = pool
        actual = module.get_unity_connection("aaaa1111")
        assert actual is conn and conn.port == target.port
        assert old.closed is {changed!r}
        assert (conn.sock is None) is {changed!r}
        response = conn.send_command("fixture_query", {{}}, max_attempts=0)
        assert response == {{"endpoint": target.port}}
        assert conn.use_framing is True and conn._needs_tool_resync is {changed!r}
        if {changed!r}:
            assert len(created) == 1 and created[0][0] == (conn.host, 2222)
            assert old.requests == [] and conn.sock is created[0][1]
        else:
            assert not created and conn.sock is old
    ''', tmp_path)


def test_expired_deadline_stops_before_socket_io(tmp_path):
    _run('''
        conn = module.UnityConnection(port=1111, instance_id="Selected@aaaa1111")
        old = FramedSocket(1111)
        conn.sock, conn.use_framing = old, True
        try:
            conn.send_command("fixture_query", {}, deadline=time.monotonic() - 1)
        except TimeoutError as error:
            assert "exceeded total deadline" in str(error)
        else:
            raise AssertionError("deadline must be honored")
        assert not created and not old.requests and not old.closed
    ''', tmp_path)
