"""Readiness distinguishes missing heartbeats from confirmed editor activity."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from models import MCPResponse
from services.resources import editor_state
from services.tools.preflight import preflight


@pytest.mark.parametrize("phase", ["idle", "asset_import", "playmode_transition"])
def test_old_heartbeat_requests_inspection_without_claiming_a_modal(monkeypatch, phase):
    # Given an editor that has not published a snapshot for thirty seconds.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 100_000)
    snapshot = {
        "observed_at_unix_ms": 69_999,
        "activity": {"phase": phase, "since_unix_ms": 60_000},
    }
    # When the ordinary resource derives readiness.
    result = editor_state._enrich_advice_and_staleness(deepcopy(snapshot))
    # Then automation gets an inspection action, without a fabricated modal diagnosis.
    assert result["advice"]["recommended_next_action"] == "inspect_editor"
    assert result["diagnostics"]["status"] == "unresponsive"
    assert result["diagnostics"]["modal_detection"] == "unavailable"
    assert "modal_dialog" in result["diagnostics"]["possible_causes"]
    assert result["advice"]["ready_for_tools"] is False


def test_short_stale_heartbeat_retains_retry_advice(monkeypatch):
    # Given normal background throttling rather than a prolonged stall.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 100_000)
    # When the last heartbeat is three seconds old.
    result = editor_state._enrich_advice_and_staleness({"observed_at_unix_ms": 97_000})
    # Then normal retry remains appropriate.
    assert result["advice"]["recommended_next_action"] == "retry_later"
    assert result["diagnostics"]["status"] == "stale"


def test_long_import_with_fresh_heartbeat_is_not_reported_as_unresponsive(monkeypatch):
    # Given a long import that still publishes fresh editor heartbeats.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 100_000)
    snapshot = {
        "observed_at_unix_ms": 100_000,
        "assets": {"is_updating": True},
        "activity": {"phase": "asset_import", "since_unix_ms": 1_000},
    }
    # When deriving advice.
    result = editor_state._enrich_advice_and_staleness(snapshot)
    # Then it is an ongoing operation with inspection advice, not a confirmed hang.
    assert result["diagnostics"]["status"] == "prolonged_activity"
    assert result["diagnostics"]["activity_age_ms"] == 99_000
    assert result["advice"]["ready_for_tools"] is False
    assert "asset_import" in result["advice"]["blocking_reasons"]


def test_fresh_idle_editor_does_not_get_stall_advice(monkeypatch):
    # Given a long-lived idle phase with a current heartbeat.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 100_000)
    # When deriving readiness.
    result = editor_state._enrich_advice_and_staleness(
        {"observed_at_unix_ms": 100_000, "activity": {"phase": "idle", "since_unix_ms": 1}}
    )
    # Then elapsed idle time is not mistaken for a blocked operation.
    assert result["diagnostics"]["status"] == "responsive"
    assert result["advice"]["ready_for_tools"] is True


@pytest.mark.asyncio
async def test_preflight_does_not_refresh_an_unresponsive_editor(monkeypatch):
    # Given canonical state that calls for manual inspection, not another import.
    import importlib

    preflight_module = importlib.import_module("services.tools.preflight")
    refresh_module = importlib.import_module("services.tools.refresh_unity")
    monkeypatch.setattr(preflight_module, "_in_pytest", lambda: False)
    state = MCPResponse(
        success=True,
        data={
            "assets": {"external_changes_dirty": True},
            "diagnostics": {"status": "unresponsive", "modal_detection": "unavailable"},
        },
    )
    monkeypatch.setattr(
        editor_state, "get_editor_state_authoritative", AsyncMock(return_value=state)
    )
    refresh = AsyncMock()
    monkeypatch.setattr(refresh_module, "refresh_unity", refresh)
    # When a mutation asks to refresh dirty assets.
    result = await preflight(AsyncMock(), refresh_if_dirty=True)
    # Then it returns actionable diagnostics before side effects.
    assert result.success is False
    assert result.error == "editor_unresponsive"
    assert result.hint == "inspect_editor"
    refresh.assert_not_awaited()
