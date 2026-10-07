"""Refresh waits for the blocking advice emitted by the canonical state resource."""

import asyncio
from types import SimpleNamespace

import pytest

from services.resources import editor_state, project_info
from services.state.external_changes_scanner import ExternalChangesScanner, ExternalChangesState
from services.tools import refresh_unity as refresh_module

from .test_helpers import DummyContext


@pytest.mark.asyncio
@pytest.mark.parametrize("compile_mode", ["none", "request"])
@pytest.mark.parametrize(
    "busy_polls,stale,expected_success,expected_polls",
    [(1, False, True, 2), (1, True, True, 2), (None, False, False, 240), (0, True, True, 1)],
)
async def test_refresh_waits_for_canonical_asset_refresh(
    monkeypatch, busy_polls, stale, expected_success, expected_polls, compile_mode
):
    instance = "ReadinessFixture@12345678"
    ctx = DummyContext()
    await ctx.set_state("unity_instance", instance)
    scanner = ExternalChangesScanner()
    scanner._states[instance] = ExternalChangesState(dirty=True)
    monkeypatch.setattr(refresh_module, "external_changes_scanner", scanner)
    monkeypatch.setattr(editor_state, "external_changes_scanner", scanner)
    monkeypatch.setattr(refresh_module, "_in_pytest", lambda: False)
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 10_000)
    elapsed = 0.0
    polls = 0
    refreshes = 0

    async def advance(seconds):
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(refresh_module, "time", SimpleNamespace(monotonic=lambda: elapsed))
    monkeypatch.setattr(refresh_module, "asyncio", SimpleNamespace(sleep=advance, wait_for=asyncio.wait_for))

    async def transport(send, target, command, params, **kwargs):
        nonlocal polls, refreshes
        assert target == instance
        if command == "refresh_unity":
            refreshes += 1
            assert kwargs == {"retry_on_reload": False}
            assert params["wait_for_ready"] is (compile_mode == "request")
            return {"success": True, "message": "Refresh requested"}
        if command == "get_project_info":
            return {"success": True, "data": {}}
        assert command == "get_editor_state"
        polls += 1
        assert scanner._states[instance].dirty is True
        return {
            "success": True,
            "data": {
                "observed_at_unix_ms": 5_000 if stale else 10_000,
                "unity": {"instance_id": instance},
                "assets": {"refresh": {"is_refresh_in_progress": busy_polls is None or polls <= busy_polls}},
            },
        }

    monkeypatch.setattr(refresh_module.unity_transport, "send_with_unity_instance", transport)
    monkeypatch.setattr(project_info, "send_with_unity_instance", transport)
    response = await refresh_module.refresh_unity(ctx, compile=compile_mode, wait_for_ready=True)
    assert response.success is expected_success
    assert polls == expected_polls
    assert refreshes == 1
    assert scanner._states[instance].dirty is (not expected_success)
    if not expected_success:
        assert response.data == {"timeout": True, "wait_seconds": 60.0}
