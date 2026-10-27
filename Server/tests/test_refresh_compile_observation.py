"""A finished compile remains visible when an independent readiness barrier times out."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools import refresh_unity as refresh
from tests.test_compile_job_contracts import native_job


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failed"])
async def test_readiness_timeout_keeps_latest_finished_compile(monkeypatch, outcome):
    now = [0.0]
    commands = []
    compile_id = None

    async def advance(seconds):
        now[0] += seconds

    async def send(_sender, instance, command, params, **options):
        nonlocal compile_id
        assert instance == "Retest@1234567890abcdef"
        commands.append(command)
        if command == "refresh_unity":
            assert options == {"retry_on_reload": False}
            compile_id = params["compile_id"]
            job = native_job(compile_id, "unknown")
            job.update(state="running", collection_complete=False, finished_at_unix_ms=None)
            return {"success": True, "data": {"compilation": job, "refresh_triggered": True}}
        assert command == "get_compile_job"
        assert params["compile_id"] == compile_id
        return {"success": True, "data": native_job(compile_id, outcome)}

    monkeypatch.setattr(refresh, "_in_pytest", lambda: False)
    monkeypatch.setattr(refresh, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(
        refresh, "asyncio", SimpleNamespace(sleep=advance, wait_for=asyncio.wait_for)
    )
    monkeypatch.setattr(
        refresh,
        "get_unity_instance_from_context",
        AsyncMock(return_value="Retest@1234567890abcdef"),
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(
        refresh.editor_state,
        "get_editor_state_authoritative",
        AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "advice": {
                        "ready_for_tools": False,
                        "blocking_reasons": ["native_test_cleanup"],
                    }
                },
            }
        ),
    )

    result = await refresh.refresh_unity(None, compile="request", timeout_seconds=1)

    assert result.success is False
    assert result.data["compilation"]["state"] == "finished"
    assert result.data["compilation"]["outcome"] == outcome
    assert result.data["compilation"]["compile_id"] == compile_id
    assert result.data["source_revision_confirmed"] is False
    assert result.data["readiness"]["ready"] is False
    assert result.data["readiness"]["blocking_reasons"] == ["native_test_cleanup"]
    assert result.data["refresh_triggered"] is True
    assert commands == ["refresh_unity", "get_compile_job"]
    assert now[0] == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["observation_unavailable", "tracking_unsupported", "not_found"])
async def test_unavailable_followup_preserves_finished_native_evidence(monkeypatch, reason):
    from services.tools.get_compile_job import unknown_compile_job

    async def send(_sender, _instance, command, params, **options):
        if command == "refresh_unity":
            return {
                "success": True,
                "data": {"compilation": native_job(params["compile_id"], "failed")},
            }
        return {"success": True, "data": unknown_compile_job(params["compile_id"], reason)}

    monkeypatch.setattr(
        refresh,
        "get_unity_instance_from_context",
        AsyncMock(return_value="Retest@1234567890abcdef"),
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    result = await refresh.refresh_unity(None, compile="request", timeout_seconds=1)
    assert result.success
    assert result.data["compilation"]["outcome"] == "failed"
    assert result.data["compilation_observation_error"] == reason


@pytest.mark.asyncio
async def test_slow_compile_observation_is_cancelled_inside_the_total_budget(monkeypatch):
    cancelled = asyncio.Event()
    reads = []

    async def send(_sender, _instance, command, params, **options):
        return {
            "success": True,
            "data": {"compilation": native_job(params["compile_id"], "failed")},
        }

    async def blocked(*args):
        reads.append(asyncio.current_task())
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(refresh, "_in_pytest", lambda: False)
    monkeypatch.setattr(
        refresh,
        "get_unity_instance_from_context",
        AsyncMock(return_value="Retest@1234567890abcdef"),
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(refresh, "read_compile_job", blocked)
    monkeypatch.setattr(
        refresh.editor_state,
        "get_editor_state_authoritative",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"advice": {"ready_for_tools": False, "blocking_reasons": ["compiling"]}},
            }
        ),
    )
    start = asyncio.get_running_loop().time()
    task = asyncio.create_task(refresh.refresh_unity(None, compile="request", timeout_seconds=1))
    result = await task
    assert asyncio.get_running_loop().time() - start < 2
    assert not result.success and result.data["timeout"]
    assert result.data["compilation"]["outcome"] == "failed"
    assert cancelled.is_set() and all(task.done() for task in reads)


@pytest.mark.asyncio
async def test_external_cancellation_drains_compile_observation(monkeypatch):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def send(_sender, _instance, command, params, **options):
        return {
            "success": True,
            "data": {"compilation": native_job(params["compile_id"], "failed")},
        }

    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(refresh, "_in_pytest", lambda: False)
    monkeypatch.setattr(
        refresh,
        "get_unity_instance_from_context",
        AsyncMock(return_value="Retest@1234567890abcdef"),
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(refresh, "read_compile_job", blocked)
    monkeypatch.setattr(
        refresh.editor_state,
        "get_editor_state_authoritative",
        AsyncMock(return_value={"success": True, "data": {"advice": {"ready_for_tools": False}}}),
    )
    task = asyncio.create_task(refresh.refresh_unity(None, compile="request", timeout_seconds=10))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def coordination(phase):
    return {
        "scope": "owned_request_pipeline_events",
        "phase": phase,
        "reason": None if phase == "no_owned_reservation" else "native_job_active",
        "retryable": phase != "no_owned_reservation",
        "recovery_action": None,
        "completion_requires": "matching_pipeline_finish_and_editor_quiescence",
    }


@pytest.fixture
def summary_peer(monkeypatch):
    peer = SimpleNamespace(
        now=0.0,
        commands=[],
        outcomes=["success"],
        coordination=["no_owned_reservation"],
        states=[(True, "idle")],
        change_job=lambda job: job,
        native_success=True,
        cancelled=asyncio.Event(),
    )

    async def advance(seconds):
        peer.now += seconds

    async def state(_):
        ready, phase = peer.states[0]
        if len(peer.states) > 1:
            peer.states.pop(0)
        return {
            "success": True,
            "data": {
                "activity": {"phase": phase},
                "advice": {
                    "ready_for_tools": ready,
                    "blocking_reasons": [] if ready else [phase],
                    "warnings": [],
                },
            },
        }

    async def send(_sender, _instance, command, params, **options):
        peer.commands.append(command)
        if command == "refresh_unity":
            assert options == {"retry_on_reload": False}
            peer.compile_id = params.get("compile_id")
            job = native_job(peer.compile_id, "unknown") if peer.compile_id else None
            if job is not None:
                job.update(state="running", collection_complete=False, finished_at_unix_ms=None)
                job = peer.change_job(job)
            return {
                "success": peer.native_success,
                "error": None if peer.native_success else "tests_running",
                "data": {
                    "compilation": job,
                    "refresh_triggered": peer.native_success,
                    "compile_requested": peer.compile_id is not None,
                    "compilation_trigger": "request",
                    "compilation_goal_satisfied": False,
                    "compile_started": True,
                    "native_coordination": coordination("started"),
                    "resulting_state": "compiling",
                    "hint": "If Unity enters compilation/domain reload, poll editor/state.",
                },
            }
        assert command == "get_compile_job"
        assert params == {"compile_id": peer.compile_id}
        outcome = peer.outcomes.pop(0)
        if outcome == "blocked":
            try:
                await asyncio.Future()
            finally:
                peer.cancelled.set()
        if outcome == "unavailable":
            return {"success": False, "error": "temporarily unavailable"}
        job = peer.change_job(native_job(peer.compile_id, outcome))
        return {
            "success": True,
            "data": job,
            "native_coordination": coordination(peer.coordination.pop(0)),
        }

    monkeypatch.setattr(refresh, "_in_pytest", lambda: False)
    monkeypatch.setattr(refresh, "time", SimpleNamespace(monotonic=lambda: peer.now))
    monkeypatch.setattr(
        refresh, "asyncio", SimpleNamespace(sleep=advance, wait_for=asyncio.wait_for)
    )
    monkeypatch.setattr(
        refresh,
        "get_unity_instance_from_context",
        AsyncMock(return_value="Retest@1234567890abcdef"),
    )
    monkeypatch.setattr(
        refresh, "external_changes_scanner", SimpleNamespace(capture_dirty_state=lambda _: None)
    )
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    peer.state_reader = AsyncMock(side_effect=state)
    monkeypatch.setattr(refresh.editor_state, "get_editor_state_authoritative", peer.state_reader)
    return peer


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failed", "unknown", "no_assemblies"])
async def test_ready_refresh_reconciles_initial_summaries(summary_peer, outcome):
    if outcome == "no_assemblies":
        outcome = "unknown"
        summary_peer.change_job = lambda job: {**job, "assembly_count": 0}
    summary_peer.outcomes = [outcome]
    result = await refresh.refresh_unity(None, compile="request", timeout_seconds=5)

    assert result.success
    assert result.data["resulting_state"] == "idle"
    assert result.data["compilation_goal_satisfied"] is True
    assert result.data["compilation"]["outcome"] == outcome
    assert result.data["readiness"]["ready"] is True
    assert result.data["source_revision_confirmed"] is False
    assert result.data["native_coordination"] == coordination("no_owned_reservation")
    assert result.data["native_coordination"] == result.data["compilation"]["native_coordination"]
    assert "poll" not in result.data["hint"].lower()
    assert result.data["refresh_triggered"] and result.data["compile_requested"]
    assert result.data["compile_started"] and result.data["compilation_trigger"] == "request"
    assert summary_peer.commands == ["refresh_unity", "get_compile_job"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"state": "requested", "started_at_unix_ms": None},
        {"attribution": "ambiguous", "candidate_generations": [3, 4]},
        {"attribution": "unconfirmed"},
        {"session_id": None},
        {"generation": None},
        {"candidate_generations": []},
        {"requested_at_unix_ms": 4},
    ],
)
async def test_ready_editor_does_not_invent_a_matching_compile_cycle(summary_peer, change):
    summary_peer.outcomes = ["unknown"]
    summary_peer.change_job = lambda job: {**job, **change}
    result = await refresh.refresh_unity(None, compile="request", timeout_seconds=5)

    assert result.success and result.data["readiness"]["ready"]
    assert result.data["compilation_goal_satisfied"] is False
    assert result.data["compilation"]["outcome"] == "unknown"
    assert result.data["source_revision_confirmed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [True, False])
async def test_ready_transition_reobserves_finished_job_coordination(summary_peer, available):
    summary_peer.states = [(False, "domain_reload"), (True, "idle")]
    summary_peer.outcomes = ["success", "success" if available else "unavailable"]
    summary_peer.coordination = ["finished", "no_owned_reservation"]
    result = await refresh.refresh_unity(None, compile="request", timeout_seconds=5)

    assert summary_peer.commands == ["refresh_unity", "get_compile_job", "get_compile_job"]
    assert result.success and result.data["resulting_state"] == "idle"
    assert result.data["compilation"]["outcome"] == "success"
    assert result.data["compilation_goal_satisfied"] is True
    assert result.data["native_coordination"] == coordination(
        "no_owned_reservation" if available else "finished"
    )
    if not available:
        assert result.data["compilation_observation_error"] == "tracking_unsupported"
        assert result.data["compilation_observation_age_seconds"] == 0.25
    assert summary_peer.now == 0.25


@pytest.mark.asyncio
async def test_final_compile_observation_timeout_preserves_already_observed_readiness(summary_peer):
    summary_peer.states = [(False, "domain_reload"), (True, "idle")]
    summary_peer.outcomes = ["success", "blocked"]
    summary_peer.coordination = ["finished"]
    result = await refresh.refresh_unity(None, compile="request", timeout_seconds=1)

    assert result.success and result.data["readiness"]["ready"]
    assert result.data["resulting_state"] == "idle"
    assert result.data["compilation"]["outcome"] == "success"
    assert result.data["native_coordination"] == coordination("finished")
    assert result.data["compilation_observation_error"] == "observation_unavailable"
    assert summary_peer.cancelled.is_set()
    assert summary_peer.commands == ["refresh_unity", "get_compile_job", "get_compile_job"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["idle", "playmode"])
async def test_asset_refresh_summary_uses_observed_editor_phase(summary_peer, phase):
    summary_peer.states = [(True, phase)]
    result = await refresh.refresh_unity(None, compile="none", timeout_seconds=5)
    assert result.success and result.data["resulting_state"] == phase
    assert summary_peer.commands == ["refresh_unity"]
    assert "poll" not in result.data["hint"].lower()


@pytest.mark.asyncio
async def test_timeout_summary_keeps_latest_editor_barrier(summary_peer):
    summary_peer.states = [(False, "asset_import")]
    result = await refresh.refresh_unity(None, compile="request", timeout_seconds=1)
    assert not result.success and result.data["timeout"]
    assert result.data["resulting_state"] == "asset_import"
    assert result.data["compilation_goal_satisfied"] is True
    assert result.data["compilation"]["outcome"] == "success"
    assert result.data["readiness"]["blocking_reasons"] == ["asset_import"]
    assert summary_peer.commands == ["refresh_unity", "get_compile_job"]
    assert summary_peer.now == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("native_success", [True, False])
async def test_nonwaiting_or_rejected_refresh_does_not_invent_ready_state(
    summary_peer, native_success
):
    summary_peer.native_success = native_success
    result = await refresh.refresh_unity(
        None, compile="request", wait_for_ready=not native_success, timeout_seconds=5
    )
    assert result.success is native_success
    assert result.data["resulting_state"] == "compiling"
    assert "readiness" not in result.data
    assert "poll" in result.data["hint"]
    assert summary_peer.commands == ["refresh_unity"]
    summary_peer.state_reader.assert_not_awaited()
