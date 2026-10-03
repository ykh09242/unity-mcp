import asyncio
from unittest.mock import AsyncMock

import pytest

import services.custom_tool_service as module
from models.models import ToolDefinitionModel
from services.custom_tool_service import CustomToolService


@pytest.fixture
def service():
    # Polling and normalization do not need HTTP route registration.
    class Mcp:
        def custom_route(self, *args, **kwargs):
            return lambda fn: fn
    return CustomToolService(Mcp())


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_success", [None, True, False])
async def test_custom_tool_error_status_is_reported_as_failure(service, monkeypatch, explicit_success):
    response = {"_mcp_status": "error", "error": "Build failed", "data": {"job_id": "job-1"}}
    if explicit_success is not None:
        response["success"] = explicit_success
    monkeypatch.setattr(service, "get_tool_definition", AsyncMock(return_value=ToolDefinitionModel(
        name="build", requires_polling=True,
    )))
    send = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "send_with_unity_instance", send)

    result = await service.execute_tool("project", "build", "Project@hash", {})

    assert result.success is False
    assert result.error == "Build failed"
    assert result.data == {"job_id": "job-1"}
    send.assert_awaited_once()


@pytest.mark.parametrize("response", [
    {"success": True, "data": {"value": 1}},
    {"_mcp_status": "complete", "data": {"value": 1}},
    {"value": 1},
])
def test_custom_tool_successful_and_raw_responses_are_preserved(service, response):
    result = service._normalize_response(response)
    assert result.success is True
    assert result.data == {"value": 1}


@pytest.mark.asyncio
async def test_custom_tool_polling_stops_at_deadline_without_an_extra_request(service, monkeypatch):
    now = [0.0]
    sleeps = []

    async def advance_time(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance_time)
    send = AsyncMock(return_value={"_mcp_status": "complete"})
    monkeypatch.setattr(module, "send_with_unity_instance", send)

    result = await service._poll_until_complete(
        "build", "Project@hash", {},
        {"_mcp_status": "pending", "_mcp_poll_interval": 5}, "status",
        max_poll_seconds=1,
    )

    assert result.success is False
    assert "Timeout" in result.message
    assert sleeps == [1.0]
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_tool_polling_propagates_cancellation(service, monkeypatch):
    sleep = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    send = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", send)

    with pytest.raises(asyncio.CancelledError):
        await service._poll_until_complete(
            "build", "Project@hash", {}, {"_mcp_status": "pending"}, "status",
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_tool_deadline_cancels_a_blocked_status_request(service, monkeypatch):
    cancelled = []

    async def blocked_status(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(module, "send_with_unity_instance", blocked_status)

    result = await asyncio.wait_for(service._poll_until_complete(
        "build", "Project@hash", {},
        {"_mcp_status": "pending", "_mcp_poll_interval": 0.1}, "status",
        max_poll_seconds=0.15,
    ), timeout=0.5)

    assert result.success is False
    assert "Timeout" in result.message
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_custom_tool_polling_threads_user_and_job_parameters(service, monkeypatch):
    send = AsyncMock(return_value={"_mcp_status": "complete", "data": {"job_id": "job-1"}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    params = {"action": "start", "job_id": "job-1"}

    result = await service._poll_until_complete(
        "build", "Project@hash", params,
        {"_mcp_status": "pending", "_mcp_poll_interval": 0.1}, "status", user_id="user-a",
    )

    assert result.success is True
    assert send.call_args.args[3] == {"action": "status", "job_id": "job-1"}
    assert send.call_args.kwargs == {"user_id": "user-a"}
    assert params["action"] == "start"
