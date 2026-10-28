"""Final refresh history comes from the final observed compiler evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools import refresh_unity as refresh
from tests.test_compile_job_contracts import native_job


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failed"])
@pytest.mark.parametrize("session", ["b" * 32, "c" * 32])
async def test_final_success_history_matches_nested_and_direct_exposures(
    monkeypatch, previous, legacy, outcome, session
):
    # Given an initial native refresh snapshot with absent or older successful history.
    history = (
        {
            "session_id": "b" * 32,
            "generation": 2,
            "started_at_unix_ms": 0,
            "finished_at_unix_ms": 1,
            "assembly_count": 1,
            "warning_count": 0,
        }
        if previous
        else None
    )
    expected = {
        "session_id": "b" * 32,
        "generation": 3,
        "started_at_unix_ms": 2,
        "finished_at_unix_ms": 3,
        "assembly_count": 2,
        "warning_count": 0,
    }
    expected["session_id"] = session
    if outcome == "failed":
        expected = history if session == "b" * 32 else None
    commands = []

    async def send(_sender, _instance, command, params, **kwargs):
        commands.append(command)
        job = native_job(params["compile_id"], "success")
        if command == "refresh_unity":
            job.update(
                state="running",
                outcome="unknown",
                collection_complete=False,
                finished_at_unix_ms=None,
            )
            return {
                "success": True,
                "data": {"compilation": job, "last_successful_compilation": history},
            }
        assert command == "get_compile_job"
        job = native_job(params["compile_id"], outcome)
        job["session_id"] = session
        if legacy:
            job.pop("last_successful_compilation")
            job.pop("status")
            return {"success": True, "data": job}
        return {"success": True, "data": job, "last_successful_compilation": expected}

    monkeypatch.setattr(refresh, "_in_pytest", lambda: False)
    monkeypatch.setattr(
        refresh, "get_unity_instance_from_context", AsyncMock(return_value="Owned@fixture")
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(
        refresh.editor_state,
        "get_editor_state_authoritative",
        AsyncMock(return_value={"success": True, "data": {"advice": {"ready_for_tools": True}}}),
    )
    # When a waited refresh observes a successful final compilation.
    result = await refresh.refresh_unity(None, compile="request", timeout_seconds=2)
    # Then both exposed histories identify that success, including native v1 peers.
    assert result.success
    assert result.data["last_successful_compilation"] == expected
    assert result.data["compilation"]["last_successful_compilation"] == expected
    assert result.data["compilation"]["status"] == (
        "compiled" if outcome == "success" else "failed"
    )
    assert commands == ["refresh_unity", "get_compile_job"]
