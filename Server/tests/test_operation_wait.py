"""Read-only observation preserves native state and one captured target."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from services.tools import wait_for_operation as wait_module


@pytest.mark.parametrize(
    "kind,state,phase,terminal",
    [
        ("build", "pending", "waiting", False),
        ("build", "building", "running", False),
        ("build", "succeeded", "terminal", True),
        ("build", "failed", "terminal", True),
        ("build", "interrupted", "terminal", True),
        ("package", "running", "running", False),
        ("package", "unknown", "unknown", False),
        ("package", "succeeded", "terminal", True),
        ("scenario", "running", "running", False),
        ("scenario", "cancelled", "terminal", True),
    ],
)
def test_owner_states_are_projected_without_inventing_completion(kind, state, phase, terminal):
    data = {"job_id": "job", "result" if kind == "build" else "status": state}
    result = wait_module.project_observation(kind, "job", {"success": True, "data": data})
    assert result["data"]["phase"] == phase
    assert result["data"]["terminal"] is terminal
    assert result["data"]["job"] is data
    assert result["success"] is True


def test_finalizing_is_nonterminal_even_with_terminal_pending_outcome():
    data = {
        "job_id": "job",
        "status": "running",
        "phase": "finalizing",
        "pending_status": "succeeded",
        "runner_resources_released": False,
    }
    result = wait_module.project_observation("scenario", "job", {"success": True, "data": data})
    assert result["data"]["phase"] == "finalizing"
    assert result["data"]["terminal"] is False


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"job_id": "foreign", "status": "succeeded"},
        {"job_id": "job", "status": "new_unknown_state"},
    ],
)
def test_missing_mismatched_and_unrecognized_evidence_cannot_claim_completion(data):
    result = wait_module.project_observation("package", "job", {"success": True, "data": data})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_wait_captures_once_and_only_polls_existing_job(monkeypatch):
    target = SimpleNamespace(instance="Fixture@hash", generation="one")
    capture = AsyncMock(return_value=target)
    fetch = AsyncMock(
        side_effect=[
            {"success": True, "data": {"job_id": "job", "status": "running"}},
            {"success": True, "data": {"job_id": "job", "status": "failed"}},
        ]
    )
    monkeypatch.setattr(wait_module, "capture_target", capture)
    monkeypatch.setattr(wait_module, "fetch_status", fetch)
    monkeypatch.setattr(wait_module, "POLL_INTERVAL", 0)
    result = await wait_module.wait_for_operation(AsyncMock(), "package", "job", 1)
    assert result["success"] is True and result["data"]["terminal"]
    assert result["data"]["job_status"] == "failed"
    capture.assert_awaited_once()
    assert fetch.await_count == 2
    assert all(c.args == (target, "package", "job") for c in fetch.await_args_list)


@pytest.mark.asyncio
async def test_expiry_preserves_last_observation_without_cancelling_native_job(monkeypatch):
    monkeypatch.setattr(wait_module, "capture_target", AsyncMock(return_value=object()))
    fetch = AsyncMock(
        return_value={"success": True, "data": {"job_id": "job", "result": "building"}}
    )
    monkeypatch.setattr(wait_module, "fetch_status", fetch)
    monkeypatch.setattr(wait_module, "POLL_INTERVAL", 0.02)
    token = wait_module.test_job_wait_deadline.set(asyncio.get_running_loop().time() + 0.01)
    try:
        result = await wait_module.wait_for_operation(AsyncMock(), "build", "job", 1)
    finally:
        wait_module.test_job_wait_deadline.reset(token)
    assert result["data"]["wait_status"] == "expired"
    assert result["data"]["job_status"] == "building" and not result["data"]["terminal"]
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_package_outcome_returns_once_without_waiting_for_guessed_success(
    monkeypatch,
):
    monkeypatch.setattr(wait_module, "capture_target", AsyncMock(return_value=object()))
    fetch = AsyncMock(
        return_value={"success": True, "data": {"job_id": "job", "status": "unknown"}}
    )
    monkeypatch.setattr(wait_module, "fetch_status", fetch)
    result = await wait_module.wait_for_operation(AsyncMock(), "package", "job", 60)
    assert result["data"]["phase"] == "unknown" and not result["data"]["terminal"]
    fetch.assert_awaited_once()
