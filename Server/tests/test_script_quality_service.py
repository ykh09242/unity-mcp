"""Owner fences and once-only dispatch for optional analysis jobs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.script_quality.service import ScriptQualityService


@pytest.fixture
def quality(monkeypatch):
    import services.script_quality.service as module

    scope = SimpleNamespace(key=("local", "project-a"))
    target = SimpleNamespace(scope=scope, is_current=lambda: True, authorize_public=None)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Editor@project-a")
    )
    monkeypatch.setattr(module, "capture_target", AsyncMock(return_value=target))
    send = AsyncMock(return_value={"success": True, "data": {"state": "queued"}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    return ScriptQualityService(), target, send


@pytest.mark.asyncio
async def test_job_is_bound_to_authenticated_project_and_principal(quality):
    service, target, send = quality
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    job_id = response["data"]["job_id"]
    assert len(job_id) == 32
    target.scope.key = ("another-user", "project-a")
    hidden = await service.execute(None, "status", {"job_id": job_id})
    assert hidden["code"] == "analysis_not_found"
    assert send.await_count == 1
    target.scope.key = ("local", "another-project")
    hidden = await service.execute(None, "cancel", {"job_id": job_id})
    assert hidden["code"] == "analysis_not_found"
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_uncertain_start_preserves_id_and_never_retries(quality):
    service, _, send = quality
    send.side_effect = TimeoutError
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    assert response["code"] == "analysis_transport_unknown"
    assert len(response["job_id"]) == 32
    assert send.await_count == 1
    assert send.call_args.kwargs["retry_on_reload"] is False


@pytest.mark.asyncio
async def test_changed_target_is_refused_before_dispatch(quality):
    service, target, send = quality
    target.is_current = lambda: False
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    assert response["code"] == "analysis_target_changed"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_operations_do_not_install_refresh_or_broaden_scope(quality):
    service, _, send = quality
    await service.execute(None, "capabilities", {})
    args = send.call_args.args
    assert args[2:] == ("manage_script_quality", {"action": "capabilities"})
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_explicit_selection_never_dispatches(quality):
    service, _, send = quality
    response = await service.execute(None, "analyze", {})
    assert response["code"] == "invalid_script_quality_request"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_error_envelope_cannot_settle_an_unknown_native_job(quality):
    service, _, send = quality
    send.return_value = {
        "success": False,
        "code": "command_outcome_unknown",
        "error": "Connection closed after dispatch",
    }
    response = await service.execute(None, "analyze", {"paths": ["Assets/A.cs"]})
    assert service.jobs[response["job_id"]].terminal is False
