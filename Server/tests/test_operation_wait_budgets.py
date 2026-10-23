"""Slow work retains an explicit finite budget across readiness boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools import refresh_unity as refresh


@pytest.mark.parametrize("value", [True, 0, -1, float("inf"), float("nan"), 86401, 10**400, "900"])
@pytest.mark.asyncio
async def test_invalid_refresh_budget_is_rejected_before_dispatch(monkeypatch, value):
    send = AsyncMock()
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    result = await refresh.refresh_unity(None, timeout_seconds=value)
    assert result.success is False
    assert "timeout_seconds" in result.error
    send.assert_not_awaited()


@pytest.mark.parametrize("dispatch_seconds,expected_ready", [(480, True), (901, False)])
@pytest.mark.asyncio
async def test_refresh_spends_one_budget_without_replaying_mutation(
    monkeypatch, dispatch_seconds, expected_ready
):
    now = [0.0]
    monkeypatch.setattr(refresh, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(
        refresh, "get_unity_instance_from_context", AsyncMock(return_value="Large@hash")
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    params_seen = []

    async def send(_sender, instance, command, params, **options):
        assert instance == "Large@hash" and command == "refresh_unity"
        assert options == {"retry_on_reload": False}
        params_seen.append(params)
        now[0] += dispatch_seconds
        return {"success": True, "message": "acknowledged"}

    ready = AsyncMock(return_value=(True, 0))
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", ready)
    result = await refresh.refresh_unity(None, timeout_seconds=900)
    assert result.success is expected_ready
    assert len(params_seen) == 1 and params_seen[0]["timeout_seconds"] == 900
    if expected_ready:
        ready.assert_awaited_once_with(None, timeout_s=420)
    else:
        ready.assert_not_awaited()
        assert result.data["timeout"] is True


@pytest.mark.asyncio
async def test_zero_remaining_readiness_budget_cannot_report_ready_in_tests():
    assert (await refresh.wait_for_editor_ready(None, timeout_s=0))[0] is False


@pytest.mark.parametrize("override", [None, "900"])
@pytest.mark.asyncio
async def test_selected_http_instance_has_large_reload_budget(monkeypatch, override):
    from transport import plugin_hub
    from transport.plugin_registry import PluginRegistry

    if override is None:
        monkeypatch.delenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", raising=False)
    else:
        monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", override)
    now = [0.0]
    registry = AsyncMock(spec=PluginRegistry)
    registry.get_session_id_by_hash.return_value = None
    registry.list_sessions.return_value = {}
    registry.change_event = asyncio.Event()

    async def expire(_event, timeout):
        now[0] += timeout
        return False

    registry.wait_for_change.side_effect = expire
    monkeypatch.setattr(plugin_hub.PluginHub, "_registry", registry)
    monkeypatch.setattr(plugin_hub.config, "http_remote_hosted", False)
    monkeypatch.setattr(plugin_hub, "time", SimpleNamespace(monotonic=lambda: now[0]))
    with pytest.raises(plugin_hub.NoUnitySessionError):
        await plugin_hub.PluginHub._resolve_session_id("Large@hash")
    assert now[0] == (300 if override is None else 900)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_nonfinite_recovery_override_retains_finite_default(monkeypatch, value):
    from transport.plugin_hub import _read_bounded_wait_env

    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", value)
    assert _read_bounded_wait_env("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", 300, 86400) == 300


def test_stdio_reload_does_not_exhaust_old_retry_count_or_twenty_second_cap(tmp_path):
    from tests.test_stdio_command_deadline import _run

    _run(
        """
config.command_total_timeout = 900
os.environ["UNITY_MCP_RELOAD_MAX_WAIT_S"] = "600"
class ReloadingConnection:
    def send_command(self, *_args, **_kwargs):
        if clock.now < 45:
            return {"success": False, "hint": "retry", "data": {"reason": "reloading"}}
        return {"success": True}
module.get_unity_connection = lambda _instance: ReloadingConnection()
module.time.sleep = lambda seconds: clock.advance(seconds, None)
result = module.send_command_with_retry("fixture_query", {})
assert result == {"success": True}, result
assert clock.now == 45
""",
        tmp_path,
    )


@pytest.mark.asyncio
async def test_expired_dispatch_reports_unknown_without_retry(monkeypatch):
    monkeypatch.setattr(
        refresh, "get_unity_instance_from_context", AsyncMock(return_value="Large@hash")
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    send = AsyncMock(side_effect=TimeoutError)
    ready = AsyncMock()
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", ready)
    result = await refresh.refresh_unity(None, timeout_seconds=900)
    assert result.success is False and result.data["reason"] == "outcome_unknown"
    assert result.hint == "inspect_state_before_retry"
    send.assert_awaited_once()
    ready.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_selection_does_not_dispatch_late_mutation(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(refresh, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def select(_ctx):
        now[0] = 901
        return "Large@hash"

    monkeypatch.setattr(refresh, "get_unity_instance_from_context", select)
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    send = AsyncMock()
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    result = await refresh.refresh_unity(None, timeout_seconds=900)
    assert result.success is False and result.data["phase"] == "selection"
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("override,delay", [(None, 45), ("1800", 900)])
async def test_default_mutation_readiness_survives_slow_reload(monkeypatch, override, delay):
    if override is None:
        monkeypatch.delenv("UNITY_MCP_EDITOR_READY_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("UNITY_MCP_EDITOR_READY_TIMEOUT", override)
    now = [0.0]
    monkeypatch.setattr(refresh, "_in_pytest", lambda: False)
    monkeypatch.setattr(refresh, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def ready(_ctx):
        now[0] = delay
        return {"data": {"advice": {"ready_for_tools": True}}}

    monkeypatch.setattr(refresh.editor_state, "get_editor_state_authoritative", ready)
    assert await refresh.wait_for_editor_ready(None) == (True, delay)


@pytest.mark.asyncio
async def test_unready_mutation_is_not_redispatched_after_readiness_budget(monkeypatch):
    refusal = {"success": False, "hint": "retry", "data": {"reason": "reloading"}}
    send = AsyncMock(return_value=refusal)
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(refresh, "wait_for_editor_ready", AsyncMock(return_value=(False, 300)))
    assert await refresh._send_mutation(None, "Large@hash", "manage_script", {}) == refusal
    send.assert_awaited_once()
