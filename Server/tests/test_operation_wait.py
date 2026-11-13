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
        ("build", "cancelled", "terminal", True),
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


@pytest.mark.parametrize("current_build", ["build-child", None])
def test_cancelled_batch_waits_for_native_child_settlement(current_build):
    data = {
        "job_id": "batch-job",
        "result": "cancelled",
        "current_build": current_build,
        "builds": [{"job_id": "build-child", "result": "building" if current_build else "failed"}],
    }
    result = wait_module.project_observation("build", "batch-job", {"success": True, "data": data})
    assert result["data"]["phase"] == ("finalizing" if current_build else "terminal")
    assert result["data"]["terminal"] is (current_build is None)
    assert result["data"]["job_status"] == "cancelled"
    assert result["data"]["job"] is data


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled", "timed_out", "interrupted"])
def test_finished_scenario_cleanup_failure_retains_outcome_and_requires_recovery(state):
    data = {
        "job_id": "a" * 32,
        "status": state,
        "phase": "finished",
        "runner_resources_released": False,
        "cleanup_error": "Runner release failed.",
        "report_error": "Report could not be saved.",
    }
    result = wait_module.project_observation(
        "scenario", data["job_id"], {"success": True, "data": data}
    )
    assert result["success"] is True
    assert result["data"]["phase"] == "terminal"
    assert result["data"]["terminal"] is True
    assert result["data"]["job_status"] == state
    assert result["data"]["job"] is data
    summary = result["data"]["summary"]
    assert summary["settled"] is False
    assert summary["runner_resources_released"] is False
    assert summary["cleanup_error"] == data["cleanup_error"]
    assert summary["report_error"] == data["report_error"]
    recovery = summary["recovery"]
    assert recovery["job_id"] == data["job_id"]
    assert recovery["phase"] == "cleanup_failed"
    assert recovery["reason"] == "scenario_resources_unreleased"
    assert recovery["mode"] == "restart_required"
    assert "Inspect the job's cleanup and report errors" in recovery["message"]
    assert "restart the Editor before starting native work" in recovery["message"]


@pytest.mark.parametrize("phase", ["finalizing", None])
def test_unreleased_scenario_without_finished_evidence_remains_nonterminal(phase):
    data = {
        "job_id": "job",
        "status": "failed",
        "phase": phase,
        "runner_resources_released": False,
    }
    result = wait_module.project_observation("scenario", "job", {"success": True, "data": data})
    assert result["data"]["phase"] == "finalizing"
    assert result["data"]["terminal"] is False
    assert "summary" not in result["data"]


@pytest.mark.asyncio
async def test_wait_reads_cancelled_batch_until_current_build_settles(monkeypatch):
    target = object()
    monkeypatch.setattr(wait_module, "capture_target", AsyncMock(return_value=target))
    monkeypatch.setattr(wait_module, "POLL_INTERVAL", 0)
    fetch = AsyncMock(
        side_effect=[
            {
                "success": True,
                "data": {"job_id": "batch", "result": "cancelled", "current_build": "child"},
            },
            {
                "success": True,
                "data": {"job_id": "batch", "result": "cancelled", "current_build": None},
            },
        ]
    )
    monkeypatch.setattr(wait_module, "fetch_status", fetch)
    result = await wait_module.wait_for_operation(AsyncMock(), "build", "batch", 60)
    assert result["data"]["terminal"] is True
    assert result["data"]["job_status"] == "cancelled"
    assert result["data"]["job"]["current_build"] is None
    assert fetch.await_count == 2
    assert all(call.args == (target, "build", "batch") for call in fetch.await_args_list)


@pytest.mark.asyncio
async def test_single_status_wait_retains_cancelled_batch_child_then_observes_settlement(
    monkeypatch,
):
    monkeypatch.setattr(wait_module, "capture_target", AsyncMock(return_value=object()))
    fetch = AsyncMock(
        side_effect=[
            {
                "success": True,
                "data": {"job_id": "batch", "result": "cancelled", "current_build": "child"},
            },
            {
                "success": True,
                "data": {"job_id": "batch", "result": "cancelled", "current_build": None},
            },
        ]
    )
    monkeypatch.setattr(wait_module, "fetch_status", fetch)
    first = await wait_module.wait_for_operation(AsyncMock(), "build", "batch", 0)
    assert first["data"]["phase"] == "finalizing"
    assert first["data"]["terminal"] is False
    assert first["data"]["job"]["current_build"] == "child"
    fetch.assert_awaited_once()
    second = await wait_module.wait_for_operation(AsyncMock(), "build", "batch", 0)
    assert second["data"]["terminal"] is True
    assert second["data"]["job_status"] == "cancelled"
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_wait_returns_finished_scenario_recovery_without_polling_again(monkeypatch):
    monkeypatch.setattr(wait_module, "capture_target", AsyncMock(return_value=object()))
    job_id = "a" * 32
    data = {
        "job_id": job_id,
        "status": "failed",
        "phase": "finished",
        "runner_resources_released": False,
    }
    fetch = AsyncMock(return_value={"success": True, "data": data})
    monkeypatch.setattr(wait_module, "fetch_status", fetch)
    result = await asyncio.wait_for(
        wait_module.wait_for_operation(AsyncMock(), "scenario", job_id, 60), timeout=1
    )
    assert result["data"]["terminal"] is True
    assert result["data"]["job_id"] == job_id
    assert result["data"]["summary"]["recovery"]["mode"] == "restart_required"
    assert result["data"]["wait_status"] == "observed"
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_initial_completion_preserves_finished_scenario_recovery(monkeypatch):
    from services import job_completion

    monkeypatch.setattr(wait_module, "capture_target", AsyncMock(return_value=object()))
    monkeypatch.setattr(job_completion, "authorize_remote_operation", AsyncMock())
    job_id = "a" * 32
    data = {
        "job_id": job_id,
        "status": "failed",
        "phase": "finished",
        "runner_resources_released": False,
    }
    fetch = AsyncMock(return_value={"success": True, "data": data})
    monkeypatch.setattr(wait_module, "fetch_status", fetch)
    receipt = {"success": True, "data": {"job_id": job_id, "status": "running"}}
    result = await job_completion.complete_job(AsyncMock(), object(), "scenario", receipt, 60)
    assert result["data"]["job_id"] == job_id
    assert result["data"]["status"] == "running"
    completion = result["data"]["completion"]
    assert completion["status"] == "failed"
    assert completion["complete"] is True
    assert completion["summary"]["runner_resources_released"] is False
    assert completion["summary"]["settled"] is False
    assert completion["summary"]["recovery"]["mode"] == "restart_required"
    assert completion["summary"]["recovery"]["job_id"] == job_id
    fetch.assert_awaited_once()


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


@pytest.mark.parametrize("operation", ["list_packages", "search_packages"])
@pytest.mark.parametrize("enveloped", [False, True])
def test_failed_package_query_is_a_terminal_observation_with_original_error(operation, enveloped):
    data = {"job_id": "job", "operation": operation, "status": "failed"}
    native = {
        "success": False,
        "error": "Owned query could not reach the registry.",
        "code": "owned_query_failure",
        "data": data,
    }
    response = {"status": "success", "result": native} if enveloped else native
    result = wait_module.project_observation("package", "job", response)
    assert result["success"] is True
    assert result["data"]["phase"] == "terminal"
    assert result["data"]["terminal"] is True
    assert result["data"]["job_status"] == "failed"
    assert result["data"]["job"] == {
        **data,
        "error": native["error"],
        "code": native["code"],
    }
    assert data == {"job_id": "job", "operation": operation, "status": "failed"}


@pytest.mark.parametrize(
    "kind,data",
    [
        ("package", None),
        ("package", {"operation": "list_packages", "status": "failed"}),
        ("package", {"job_id": "foreign", "operation": "list_packages", "status": "failed"}),
        ("package", {"job_id": "job", "operation": "list_packages", "status": "unknown"}),
        ("package", {"job_id": "job", "operation": "list_packages", "status": "succeeded"}),
        ("package", {"job_id": "job", "operation": "invalid_action", "status": "failed"}),
        ("package", {"job_id": "job", "operation": [], "status": "failed"}),
        ("package", {"job_id": "job", "operation": {}, "status": "failed"}),
        ("scenario", {"job_id": "job", "operation": "list_packages", "status": "failed"}),
    ],
)
def test_query_observation_does_not_turn_unknown_or_invalid_requests_into_completion(kind, data):
    response = {"success": False, "error": "Native request failed.", "data": data}
    assert wait_module.project_observation(kind, "job", response) is response


@pytest.mark.asyncio
async def test_wait_stops_at_identified_failed_package_query_without_replaying(monkeypatch):
    monkeypatch.setattr(wait_module, "capture_target", AsyncMock(return_value=object()))
    fetch = AsyncMock(
        return_value={
            "success": False,
            "error": "Owned list query failed.",
            "data": {"job_id": "job", "operation": "list_packages", "status": "failed"},
        }
    )
    monkeypatch.setattr(wait_module, "fetch_status", fetch)
    result = await wait_module.wait_for_operation(AsyncMock(), "package", "job", 60)
    assert result["success"] is True
    assert result["data"]["terminal"] is True
    assert result["data"]["job"]["error"] == "Owned list query failed."
    fetch.assert_awaited_once()


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
