import asyncio
from unittest.mock import AsyncMock

import pytest

import services.custom_tool_service as module
from models.models import ToolDefinitionModel
from models.unity_response import normalize_unity_response
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


@pytest.mark.asyncio
async def test_pending_job_survives_retryable_poll_response(service, monkeypatch):
    # Given: an admitted job encounters a temporary unavailable-session response.
    now = [0.0]
    async def advance(delay):
        now[0] += delay
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    retry = normalize_unity_response(module.PluginHub._unavailable_retry_response("stale_connection"))
    send = AsyncMock(side_effect=[
        {"_mcp_status": "pending", "data": {"job_id": "job-1"}},
        retry,
        {"_mcp_status": "complete", "data": {"job_id": "job-1", "value": 42}},
    ])
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(service, "get_tool_definition", AsyncMock(return_value=ToolDefinitionModel(
        name="build", requires_polling=True, max_poll_seconds=30,
    )))

    # When: the service starts and polls the job.
    result = await service.execute_tool("project", "build", "Project@hash", {"action": "start"}, user_id="user-a")

    # Then: it polls the same job and tenant through completion and releases admission.
    assert result.success is True
    assert result.data == {"job_id": "job-1", "value": 42}
    assert [call.args[3] for call in send.call_args_list] == [
        {"action": "start"}, {"action": "status", "job_id": "job-1"},
        {"action": "status", "job_id": "job-1"},
    ]
    assert all(call.kwargs == {"user_id": "user-a"} for call in send.call_args_list)
    assert service._active_polls == 0


@pytest.mark.asyncio
async def test_retryable_poll_responses_keep_original_deadline(service, monkeypatch):
    # Given: every status request returns a transient retry envelope.
    now = [0.0]
    async def advance(delay):
        now[0] += delay
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    send = AsyncMock(return_value=module.PluginHub._unavailable_retry_response())
    monkeypatch.setattr(module, "send_with_unity_instance", send)

    # When: the existing pending job is polled with a three-second budget.
    result = await service._poll_until_complete(
        "build", "Project@hash", {}, {"_mcp_status": "pending", "data": {"job_id": "job-1"}},
        "status", user_id="user-a", max_poll_seconds=3,
    )

    # Then: retries do not renew that budget or produce a terminal transport result.
    assert not result.success
    assert "Timeout" in result.message
    assert now[0] == 3.0
    assert send.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"success": False, "error": "Tool failed"},
    {"success": False, "hint": "retry", "_mcp_status": "error", "error": "Tool failed"},
])
async def test_terminal_poll_error_stops_without_retry(service, monkeypatch, response):
    # Given: the job's status action reports a terminal tool failure.
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    send = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    # When: the pending job is polled.
    result = await service._poll_until_complete("build", "Project@hash", {}, {"_mcp_status": "pending"}, "status")
    # Then: the actual tool error is returned without another status request.
    assert not result.success
    assert result.error == "Tool failed"
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_initial_retry_failure_does_not_invent_started_job(service, monkeypatch):
    # Given: initial dispatch never confirms that a job started.
    retry = module.PluginHub._unavailable_retry_response()
    send = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    # When: that initial result reaches the polling service.
    result = await service._poll_until_complete("build", "Project@hash", {}, retry, "status")
    # Then: return its retry diagnostic instead of polling an unknown job.
    assert not result.success
    assert result.hint == "retry"
    send.assert_not_awaited()
