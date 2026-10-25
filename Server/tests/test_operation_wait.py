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


def test_failed_resolve_trigger_preserves_unknown_native_completion():
    data = {
        "job_id": "job",
        "operation": "resolve",
        "status": "failed",
        "resolve": {"completion": "unknown", "trigger_state": "failed"},
    }
    result = wait_module.project_observation("package", "job", {"success": True, "data": data})
    assert result["data"]["phase"] == "unknown"
    assert result["data"]["terminal"] is False
    assert result["data"]["job_status"] == "failed"
    assert result["data"]["job"] is data


@pytest.mark.parametrize("operation", ["list_packages", "search_packages"])
def test_fresh_package_query_completion_retains_identity_and_original_payload(operation):
    data = {
        "job_id": "job",
        "operation": operation,
        "status": "succeeded",
        "count": 1,
        "packages": [{"name": "com.fixture", "version": "1.0.0"}],
    }
    result = wait_module.project_observation("package", "job", {"success": True, "data": data})
    assert result["success"] is True
    assert result["data"]["terminal"] is True
    assert result["data"]["job"] is data


@pytest.mark.asyncio
async def test_http_target_uses_canonical_project_hash_and_rejects_foreign_identity(monkeypatch):
    monkeypatch.setattr(wait_module.config, "transport_mode", "http")
    monkeypatch.setattr(wait_module.config, "http_remote_hosted", False)
    ctx = AsyncMock()
    state = {"unity_session_id": "session", "operation_wait_generation": None}
    ctx.get_state.side_effect = lambda key: state.get(key)
    monkeypatch.setattr(
        wait_module, "get_unity_instance_from_context", AsyncMock(return_value="Main@owned-hash")
    )
    monkeypatch.setattr(
        wait_module.PluginHub,
        "_read_identity",
        AsyncMock(return_value=SimpleNamespace(generation="one", user_id=None)),
    )
    registry = SimpleNamespace(
        get_session=AsyncMock(return_value=SimpleNamespace(project_hash="owned-hash"))
    )
    monkeypatch.setattr(wait_module.PluginHub, "_registry", registry)
    target = await wait_module.capture_target(ctx)
    assert target == wait_module.ObservationTarget("Main@owned-hash", "session", "one", None)
    registry.get_session.return_value = SimpleNamespace(project_hash="foreign-hash")
    assert await wait_module.capture_target(ctx) is None
    registry.get_session.return_value = SimpleNamespace(project_hash="owned-hash")
    state["operation_wait_generation"] = "old"
    assert await wait_module.capture_target(ctx) is None


def test_generation_change_guides_fresh_observation_without_replaying_mutation():
    original = {
        "success": False,
        "error": "connection changed",
        "data": {"reason": "observation_generation_changed", "native_job_unchanged": True},
    }
    result = wait_module.project_observation("package", "job", original)
    assert result["success"] is False
    assert result["data"] is original["data"]
    assert "same job_id" in result["hint"]
    assert "do not restart or replay" in result["hint"]
