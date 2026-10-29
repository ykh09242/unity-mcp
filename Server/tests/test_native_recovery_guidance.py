"""Recovery diagnostics explain ownership without permitting mutation replay."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models.capabilities import CapabilityConnection, CapabilityLimits
from services import capabilities, doctor
from services.resources import editor_state
from services.tools import get_compile_job, refresh_unity
from tests.test_compile_coordination_contract import COMPILE_ID, NATIVE_IDLE, native_evidence


def recovery(mode="restart_required"):
    return {
        "source": "compile",
        "phase": "started",
        "reason": "pipeline_identity_missing",
        "mode": mode,
        "message": "The pipeline identity needed to prove native completion was lost.",
        "job_id": COMPILE_ID,
        "generation": "c" * 32,
    }


def blocked_state(detail=None, reason="recovery_required"):
    native = {"blocked": True, "reason": reason, "retryable": False}
    if detail is not None:
        native["recovery"] = detail
    return {
        "observed_at_unix_ms": 5000,
        "compilation": {"is_compiling": False},
        "assets": {"is_updating": False, "external_changes_scan_complete": True},
        "tests": {"is_running": False},
        "native_coordination": native,
    }


@pytest.mark.parametrize("detail", [None, {"mode": "restart_required"}, recovery("unknown")])
def test_legacy_or_incomplete_recovery_never_infers_restart(monkeypatch, detail):
    # Given: a native owner is blocked without affirmative restart evidence.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 5000)
    state = blocked_state(detail)
    state["native_coordination"]["recovery_action"] = "inspect_native_work_then_restart_editor"
    # When: canonical advice is derived.
    result = editor_state._enrich_advice_and_staleness(state)
    # Then: blocking is retained and the user is asked to inspect, not restart.
    assert result["advice"]["ready_for_tools"] is False
    assert result["native_coordination"]["retryable"] is False
    assert result["advice"]["recommended_next_action"] == "inspect_native_work"


def test_recovery_evidence_survives_state_and_capabilities(monkeypatch):
    # Given: the native owner supplies bounded evidence explaining a required restart.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 5000)
    state = editor_state._enrich_advice_and_staleness(blocked_state(recovery()))
    connection = CapabilityConnection(transport="stdio", status="connected", instance_id="A@hash")
    limits = CapabilityLimits(
        query_timeout_seconds=5,
        query_cleanup_grace_seconds=1,
        query_max_elapsed_seconds=6,
        response_bytes=65536,
        response_depth=16,
        response_nodes=1000,
    )
    observations = {name: capabilities._Observation() for name in capabilities._SOURCES}
    observations["get_editor_state"] = capabilities._observation({"success": True, "data": state})
    # When: capability discovery summarizes the state.
    result = capabilities._compose(connection, limits, observations)
    # Then: the actual evidence survives the bounded summaries.
    assert result.readiness.native_coordination.recovery.model_dump(exclude_none=True) == recovery()
    assert result.readiness.next_action == "inspect_native_work_then_restart_editor"
    assert state["native_coordination"]["recovery"] == recovery()


@pytest.mark.asyncio
async def test_doctor_preserves_recovery_without_reconstructing_restart(monkeypatch):
    # Given: observed legacy recovery has no affirmative restart proof.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 5000)
    state = editor_state._enrich_advice_and_staleness(blocked_state())
    observations = {name: capabilities._Observation() for name in capabilities._SOURCES}
    observations["get_editor_state"] = capabilities._observation({"success": True, "data": state})
    result = capabilities._compose(
        CapabilityConnection(transport="stdio", status="connected", instance_id="A@hash"),
        CapabilityLimits(
            query_timeout_seconds=5,
            query_cleanup_grace_seconds=1,
            query_max_elapsed_seconds=6,
            response_bytes=65536,
            response_depth=16,
            response_nodes=1000,
        ),
        observations,
    )
    monkeypatch.setattr(
        doctor,
        "_capture_project_capabilities",
        AsyncMock(return_value=SimpleNamespace(capabilities=result, observations=observations)),
    )
    # When: doctor produces its receipt.
    receipt = await doctor.query_doctor(None)
    # Then: it retains the blocked owner and safe action.
    check = receipt.checks["readiness"]
    assert check.next_action == "inspect_native_work"
    assert check.native_coordination.reason == "recovery_required"


@pytest.mark.asyncio
async def test_compile_optional_recovery_and_future_siblings_keep_compiler_outcome(monkeypatch):
    # Given: a newer peer supplies additive diagnostics outside the strict ledger.
    native = {**copy.deepcopy(NATIVE_IDLE), "recovery": recovery(), "future": "ignored"}
    native.update(
        phase="recovery_required",
        reason="recovery_required",
        retryable=False,
        recovery_action="inspect_native_work_then_restart_editor",
    )
    monkeypatch.setattr(
        get_compile_job.unity_transport,
        "send_job_observation",
        AsyncMock(
            return_value={
                "success": True,
                "data": native_evidence("failed"),
                "native_coordination": native,
            }
        ),
    )
    # When: the same compile identity is observed.
    result = await get_compile_job.read_compile_job("A@hash", COMPILE_ID)
    # Then: diagnostics survive without erasing or promoting compiler evidence.
    assert result["outcome"] == "failed"
    assert result["native_coordination"]["recovery"] == recovery()
    assert "future" not in result["native_coordination"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail,action",
    [(None, "inspect_native_work"), (recovery(), "inspect_native_work_then_restart_editor")],
)
async def test_refresh_keeps_recovery_receipt_without_replaying(monkeypatch, detail, action):
    # Given: the refresh was accepted and readiness finds an unreleased native owner.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 5000)
    state = editor_state._enrich_advice_and_staleness(blocked_state(detail))
    sender = AsyncMock(return_value={"success": True, "data": {"refresh_triggered": True}})
    monkeypatch.setattr(
        refresh_unity, "get_unity_instance_from_context", AsyncMock(return_value="A@hash")
    )
    monkeypatch.setattr(
        refresh_unity,
        "external_changes_scanner",
        SimpleNamespace(capture_dirty_state=lambda _: None),
    )
    monkeypatch.setattr(refresh_unity.unity_transport, "send_with_unity_instance", sender)
    monkeypatch.setattr(refresh_unity, "_in_pytest", lambda: False)
    monkeypatch.setattr(
        editor_state,
        "get_editor_state_authoritative",
        AsyncMock(return_value={"success": True, "data": state}),
    )
    # When: only readiness is observed after the accepted refresh.
    result = await refresh_unity.refresh_unity(None, timeout_seconds=1)
    # Then: the result distinguishes recovery from timeout and never replays refresh.
    assert result.success is False
    assert result.data["timeout"] is False
    assert result.data["recovery_action"] == action
    assert result.data["native_coordination"]["reason"] == "recovery_required"
    if detail:
        assert result.data["native_coordination"]["recovery"] == detail
    assert sender.await_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "x" * 129),
        ("phase", ""),
        ("reason", "C:/private/reason"),
        ("mode", "restart"),
        ("message", "x" * 513),
        ("message", "bad\nmessage"),
        ("job_id", "x" * 129),
        ("generation", 1),
    ],
)
def test_invalid_recovery_cannot_recommend_restart_or_clear_blocking(monkeypatch, field, value):
    # Given: bounded ownership diagnostics contain a malformed optional detail.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 5000)
    detail = recovery()
    detail[field] = value
    # When: the canonical native state is enriched.
    result = editor_state._enrich_advice_and_staleness(blocked_state(detail))
    # Then: native blocking remains while invalid restart evidence is discarded.
    assert result["advice"]["ready_for_tools"] is False
    assert result["advice"]["recommended_next_action"] == "inspect_native_work"
    assert "recovery" not in result["native_coordination"]


@pytest.mark.asyncio
async def test_live_owner_recovery_can_finish_by_observation_without_restart(monkeypatch):
    # Given: a recoverable live owner finishes during the existing readiness budget.
    monkeypatch.setattr(editor_state, "_now_unix_ms", lambda: 5000)
    state = blocked_state(recovery("recoverable"), reason="native_job_active")
    state["native_coordination"].update(retryable=True, recovery_action="wait_for_pipeline_finish")
    blocked = editor_state._enrich_advice_and_staleness(state)
    read = AsyncMock(
        side_effect=[
            {"success": True, "data": blocked},
            {"success": True, "data": {"advice": {"ready_for_tools": True}}},
        ]
    )
    monkeypatch.setattr(refresh_unity, "_in_pytest", lambda: False)
    monkeypatch.setattr(editor_state, "get_editor_state_authoritative", read)
    monkeypatch.setattr(refresh_unity.asyncio, "sleep", AsyncMock())
    # When: only the read-only readiness observer runs.
    ready, _ = await refresh_unity.wait_for_editor_ready(None, timeout_s=1)
    # Then: transient ownership can clear naturally without restart or new work.
    assert ready
    assert read.await_count == 2
    assert blocked["advice"]["recommended_next_action"] == "wait_for_pipeline_finish"
    assert blocked["native_coordination"]["retryable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "wait_for_compilation_start",
        "wait_for_pipeline_finish",
        "wait_for_editor_quiescence",
        "wait_for_test_cleanup",
    ],
)
async def test_compile_waiting_diagnostics_preserve_failed_compiler_outcome(monkeypatch, action):
    # Given: completed compiler evidence coexists with live native cleanup.
    native = {
        **NATIVE_IDLE,
        "phase": "finished",
        "reason": "native_job_active",
        "retryable": True,
        "recovery_action": action,
        "recovery": recovery("recoverable"),
    }
    monkeypatch.setattr(
        get_compile_job.unity_transport,
        "send_job_observation",
        AsyncMock(
            return_value={
                "success": True,
                "data": native_evidence("failed"),
                "native_coordination": native,
            }
        ),
    )
    # When: that compilation is observed by its existing identity.
    result = await get_compile_job.read_compile_job("A@hash", COMPILE_ID)
    # Then: waiting guidance cannot certify success or erase failure diagnostics.
    assert result["outcome"] == "failed"
    assert result["native_coordination"]["recovery_action"] == action
    assert result["native_coordination"]["recovery"]["mode"] == "recoverable"
