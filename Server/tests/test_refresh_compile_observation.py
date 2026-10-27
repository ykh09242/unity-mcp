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
