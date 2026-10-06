"""Whole production transport calls with clocked inert socket boundaries."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

COMMON = r'''
import json
import os
from pathlib import Path
import socket
import struct
from unittest.mock import patch
import sys
home = Path(os.environ["APPDATA"]).parent / "owned-home"
(home / ".unity-mcp").mkdir(parents=True)
Path.home = lambda: home
sys.path.insert(0, "src")
def deny(*args, **kwargs):
    raise AssertionError("actual network/discovery/sleep forbidden")
socket.socket.connect = deny
socket.create_connection = deny
from core.config import config
from transport.legacy import unity_connection as module
config.http_remote_hosted = False
config.connection_timeout = 1.0
config.handshake_timeout = 1.0
config.require_framing = True
module.PortDiscovery.discover_all_unity_instances = deny
module.stdio_port_registry.get_instances = lambda **kw: []
module.stdio_port_registry.get_instance = lambda *a, **kw: None
module.stdio_port_registry.get_port = lambda *a, **kw: 1111
module.time.sleep = deny
class Clock:
    now = 0.0
    def advance(self, duration, timeout):
        if timeout is not None and duration > timeout:
            self.now += timeout
            raise socket.timeout("controlled per-operation timeout")
        self.now += duration
class ClockedSocket:
    def __init__(self, clock, reads, writes=(), timeout=1.0):
        self.clock, self.reads, self.writes = clock, list(reads), list(writes)
        self.timeout, self.closed = timeout, False
        self.sent, self.send_attempts, self.read_timeouts, self.write_timeouts = [], [], [], []
    def getblocking(self): return True
    def setblocking(self, value): pass
    def setsockopt(self, *a): pass
    def gettimeout(self): return self.timeout
    def settimeout(self, value): self.timeout = value
    def close(self): self.closed = True
    def recv(self, count, flags=0):
        if flags: raise BlockingIOError()
        self.read_timeouts.append(self.timeout)
        duration, data = self.reads.pop(0)
        self.clock.advance(duration, self.timeout)
        if len(data) > count: self.reads.insert(0, (0.0, data[count:]))
        return data[:count]
    def sendall(self, data):
        self.send_attempts.append(data)
        self.write_timeouts.append(self.timeout)
        duration = self.writes.pop(0) if self.writes else 0.0
        self.clock.advance(duration, self.timeout)
        self.sent.append(data)
clock = Clock()
module.time.monotonic = lambda: clock.now
payload = b'{"status":"success","result":{"ok":true}}'
header = struct.pack(">Q", len(payload))
# This fixture intentionally exercises the old framed protocol, not authentication.
conn = module.UnityConnection(port=1111, instance_id="Selected@owned", allow_legacy_auth=True)
conn.use_framing = True
'''


def _run(source, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    env.pop("UNITY_MCP_DEFAULT_INSTANCE", None)
    result = subprocess.run(
        [sys.executable, "-c", COMMON + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", [
    "header", "payload", "heartbeat", "legacy", "writes", "connect",
    "handshake", "below_floor", "final_at_deadline",
])
def test_command_rejects_cumulative_io_at_deadline(case, tmp_path):
    # Given each operation fits its initial timeout but their sum exhausts the budget.
    _run(f'''
case = {case!r}
budget = 0.01 if case == "below_floor" else 1.0
reads = [(0.0, header), (0.0, payload)]
writes, connect_duration = [], 0.0
if case == "header": reads = [(0.6, header[:4]), (0.6, header[4:]), (0.0, payload)]
if case == "payload": reads = [(0.0, header), (0.6, payload[:10]), (0.6, payload[10:])]
if case == "heartbeat": reads = [(0.6, bytes(8)), (0.6, header), (0.0, payload)]
if case == "legacy":
    conn.use_framing = False
    reads = [(0.6, payload[:10]), (0.6, payload[10:])]
if case == "writes": writes = [0.6, 0.6]
if case == "connect":
    connect_duration = 0.6
    reads.insert(0, (0.6, b"WELCOME UNITY-MCP 1 FRAMING=1\\n"))
if case == "handshake": reads = [(0.6, b"WELCOME UNITY-MCP 1 FRAM"), (0.6, b"ING=1\\n")] + reads
if case == "below_floor": reads = [(0.0, header), (0.04, payload)]
if case == "final_at_deadline": reads = [(0.0, header), (1.0, payload)]
sock = ClockedSocket(clock, reads, writes)
def create(endpoint, timeout):
    clock.advance(connect_duration, timeout)
    return sock
module.socket.create_connection = create
if case not in ("connect", "handshake"): conn.sock = sock
# When the complete production send_command runs.
try:
    result = conn.send_command("fixture_query", {{}}, max_attempts=2, deadline=budget)
except (TimeoutError, ConnectionError):
    assert case in ("connect", "handshake"), "Dispatched failure lost outcome-unknown response"
else:
    assert result.success is False, "Exhausted deadline returned success"
    assert result.data == {{"reason": "outcome_unknown", "command": "fixture_query"}}
    assert result.hint == "inspect_state_before_retry"
    assert result.error
# Then I/O ends within budget and the unusable socket is discarded.
assert clock.now <= budget + 1e-9
assert sock.closed and conn.sock is None
# Header/payload failure must never automatically dispatch the command again.
expected_attempts = 0 if case in ("connect", "handshake") else (1 if case == "legacy" else 2)
assert len(sock.send_attempts) == expected_attempts
''', tmp_path)


@pytest.mark.parametrize("mode", ["framed", "legacy", "handshake", "heartbeat", "ping", "blocking_socket"])
def test_command_preserves_within_budget_protocol_and_timeout(mode, tmp_path):
    _run(f'''
# Given a complete response that fits the shared command budget.
mode = {mode!r}
if mode == "ping":
    payload = b'{{"status":"success","result":{{"message":"pong"}}}}'
    header = struct.pack(">Q", len(payload))
reads = [(0.1, header[:4]), (0.1, header[4:]), (0.1, payload)]
if mode == "legacy":
    conn.use_framing = False
    reads = [(0.1, payload[:10]), (0.1, payload[10:])]
if mode == "heartbeat": reads.insert(0, (0.1, bytes(8)))
if mode == "handshake": reads.insert(0, (0.1, b"WELCOME UNITY-MCP 1 FRAMING=1\\n"))
original_timeout = None if mode == "blocking_socket" else 1.0
sock = ClockedSocket(clock, reads, [0.1, 0.1], timeout=original_timeout)
module.socket.create_connection = lambda endpoint, timeout: sock
if mode != "handshake": conn.sock = sock
# When the actual command frames, receives and parses the response.
result = conn.send_command("ping" if mode == "ping" else "fixture_query", {{}}, max_attempts=0, deadline=1.0)
# Then original wire behavior, connection reuse and timeout restoration survive.
assert result == ({{"message": "pong"}} if mode == "ping" else {{"ok": True}})
assert clock.now < 1.0 and not sock.closed
assert sock.timeout == original_timeout
if mode == "handshake": assert conn._needs_tool_resync is True
''', tmp_path)


@pytest.mark.parametrize("case", ["direct_receive", "direct_connect", "required_framing", "heartbeat_limit"])
def test_direct_protocol_calls_preserve_existing_contracts(case, tmp_path):
    _run(f'''
# Given the direct API without an absolute command deadline.
case = {case!r}
if case == "direct_receive":
    sock = ClockedSocket(clock, [(0.6, header), (0.6, payload)])
    assert conn.receive_full_response(sock, 4) == payload
    assert clock.now == 1.2 and sock.timeout == 1.0
if case == "direct_connect":
    sock = ClockedSocket(clock, [(0.6, b"WELCOME UNITY-MCP 1 FRAM"), (0.6, b"ING=1\\n")])
    module.socket.create_connection = lambda endpoint, timeout: sock
    assert conn.connect() is True
    assert clock.now == 1.2 and conn.use_framing and sock.timeout == 1.0
if case == "required_framing":
    sock = ClockedSocket(clock, [(0.0, b"LEGACY\\n")])
    module.socket.create_connection = lambda endpoint, timeout: sock
    assert conn.connect() is False and conn.sock is None and sock.closed
if case == "heartbeat_limit":
    config.max_heartbeat_frames = 2
    sock = ClockedSocket(clock, [(0.0, bytes(8)), (0.0, bytes(8))])
    try: conn.receive_full_response(sock)
    except TimeoutError as exc: assert str(exc) == "Timeout receiving Unity response"
    else: raise AssertionError("heartbeat limit lost")
''', tmp_path)


def test_retry_receive_limit_does_not_shorten_writes(tmp_path):
    _run('''
# Given the first header write fails before payload dispatch; retry writes exceed the receive limit.
config.connection_timeout = 5.0
class PreDispatchSocket(ClockedSocket):
    def sendall(self, data):
        self.send_attempts.append(data)
        raise ConnectionResetError("controlled reset before payload dispatch")
first = PreDispatchSocket(clock, [], timeout=5.0)
conn.sock = first
retry = ClockedSocket(clock, [(0.0, b"WELCOME UNITY-MCP 1 FRAMING=1\\n"), (0.0, header), (0.0, payload)], [1.2, 0.0], timeout=5.0)
module.socket.create_connection = lambda endpoint, timeout: retry
module.time.sleep = lambda seconds: clock.advance(seconds, None)
module.random.uniform = lambda low, high: 1.0
# When actual reconnect/backoff and framed send execute against one deadline.
result = conn.send_command("fixture_query", {}, max_attempts=1, deadline=5.0)
# Then only receive keeps its historical short retry limit.
assert result == {"ok": True} and clock.now < 5.0
assert retry.timeout == 5.0 and not retry.closed
assert first.closed and first.send_attempts == [struct.pack(">Q", len(b'{"type": "fixture_query", "params": {}}'))]
assert retry.write_timeouts[0] > 1.2
assert retry.read_timeouts[-2:] == [1.0, 1.0]
assert len(retry.send_attempts) == 2
''', tmp_path)


def test_first_receive_retains_configured_idle_limit_after_socket_peek(tmp_path):
    _run('''
# Given real setblocking semantics reset the finite timeout during the liveness peek.
config.connection_timeout = 5.0
class BlockingModeSocket(ClockedSocket):
    def getblocking(self): return self.timeout != 0.0
    def setblocking(self, value): self.timeout = None if value else 0.0
sock = BlockingModeSocket(clock, [(0.0, header), (6.0, payload)], timeout=5.0)
conn.sock = sock
# When a long overall deadline still receives a wedged first-attempt response.
result = conn.send_command("fixture_query", {}, max_attempts=2, deadline=90.0)
assert result.success is False
assert result.data == {"reason": "outcome_unknown", "command": "fixture_query"}
assert result.hint == "inspect_state_before_retry"
# Then the previous configured receive timeout remains effective and restored.
assert clock.now == 5.0 and sock.closed and sock.timeout == 5.0
assert sock.read_timeouts == [5.0, 5.0]
assert len(sock.send_attempts) == 2
''', tmp_path)


@pytest.mark.parametrize("budget,max_wait,recover,expected_wait", [
    (0.02, 20.0, False, 0.02), (0.1, 20.0, False, 0.1),
    (1.0, 0.03, False, 0.03), (1.0, 20.0, True, 0.25),
])
def test_reload_wait_respects_remaining_budgets(budget, max_wait, recover, expected_wait, tmp_path):
    _run(f'''
# Given an owned selected editor reports reloading.
config.command_total_timeout = {budget!r}
os.environ["UNITY_MCP_RELOAD_MAX_WAIT_S"] = {str(max_wait)!r}
status = home / ".unity-mcp/unity-mcp-status-owned.json"
status.write_text('{{"reloading":true}}', encoding="utf-8")
sock = ClockedSocket(clock, [(0.0, header), (0.0, payload)])
conn.sock = sock
module.get_unity_connection = lambda instance_id: conn
waits = []
def sleep(seconds):
    waits.append(seconds)
    clock.advance(seconds, None)
    if {recover!r}: status.write_text('{{"reloading":false}}', encoding="utf-8")
module.time.sleep = sleep
# Preflight closes an existing socket; recovered command must connect afresh.
module.socket.create_connection = lambda endpoint, timeout: ClockedSocket(clock, [(0.0, b"WELCOME UNITY-MCP 1 FRAMING=1\\n"), (0.0, header), (0.0, payload)])
# When the actual wrapper and actual send_command handle the reload.
result = module.send_command_with_retry("fixture_query", {{}}, instance_id=conn.instance_id, max_retries=1, retry_ms=250)
# Then bounded waits retain retry hints or recover normally.
assert waits == [{expected_wait!r}] and clock.now == {expected_wait!r}
if {recover!r}: assert result == {{"ok": True}}
else: assert result.success is False and result.hint == "retry" and result.data["reason"] == "reloading"
''', tmp_path)
