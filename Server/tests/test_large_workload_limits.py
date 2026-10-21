"""Workload admission must agree across transport entry points."""

import math

import pytest

from cli.utils.config import CLIConfig
from core.config import config
from transport.legacy import unity_connection
from transport.plugin_hub import PluginHub


def test_default_command_budget_leaves_room_for_large_project_work(monkeypatch):
    monkeypatch.delenv("UNITY_MCP_TIMEOUT", raising=False)
    assert CLIConfig().timeout == 300
    assert CLIConfig.from_env().timeout == 300
    assert PluginHub._command_timeouts("manage_scene", {}) == (300, 300)
    assert PluginHub._command_timeouts("get_editor_state", {}) == (2, 2)


@pytest.mark.parametrize("key", ["timeout_seconds", "timeoutSeconds"])
def test_explicit_long_budget_reaches_both_transports(monkeypatch, key):
    monkeypatch.setattr(unity_connection.time, "monotonic", lambda: 10)
    monkeypatch.setattr(config, "command_total_timeout", 600)
    assert PluginHub._command_timeouts("manage_scene", {key: 7200}) == (7200, 7205)
    assert unity_connection._command_deadline("manage_scene", {key: 7200}) == 7215


@pytest.mark.parametrize("value", [None, True, "bad", float("inf"), float("nan")])
def test_invalid_duration_cannot_make_a_command_unbounded(value):
    budgets = PluginHub._command_timeouts("manage_scene", {"timeout_seconds": value})
    assert budgets == (300, 300)
    assert all(math.isfinite(x) for x in budgets)


def test_explicit_duration_has_finite_day_ceiling():
    assert PluginHub._command_timeouts("manage_scene", {"timeout_seconds": 10**9}) == (86400, 86405)


def test_stdio_long_request_extends_socket_wait_without_replay(tmp_path):
    from tests.test_stdio_command_deadline import _run

    _run(
        """
config.command_total_timeout = 600
sock = ClockedSocket(clock, [(1200, header), (0, payload)], timeout=300)
conn.sock = sock
module.read_status_file = lambda *args: None
result = conn.send_command("manage_scene", {"timeoutSeconds": 7200}, max_attempts=0)
assert result["ok"], result
assert clock.now == 1200
assert len(sock.sent) == 2
assert sock.timeout == 300
""",
        tmp_path,
    )
