"""Batch job identity handoff through the actual generic polling consumer."""

from unittest.mock import AsyncMock

import pytest

import services.custom_tool_service as module
from models.models import ToolDefinitionModel
from services.custom_tool_service import CustomToolService


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [{}, {"job_id": None}])
@pytest.mark.parametrize(
    "final",
    [
        {"success": True, "data": {"job_id": "batch-new", "result": "succeeded", "completed": 2}},
        {
            "success": True,
            "data": {
                "job_id": "batch-new",
                "result": "failed",
                "completed": 2,
                "builds": [{"error": "fixture failure"}],
            },
        },
        {"success": True, "data": {"job_id": "batch-new", "result": "cancelled", "completed": 2}},
        {"success": False, "error": "No job found", "data": {"job_id": "batch-new"}},
    ],
)
async def test_initial_batch_response_job_id_targets_status_until_terminal(
    monkeypatch, caller, final
):
    class Mcp:
        def custom_route(self, *args, **kwargs):
            return lambda fn: fn

    service = CustomToolService(Mcp())
    monkeypatch.setattr(
        service,
        "get_tool_definition",
        AsyncMock(
            return_value=ToolDefinitionModel(
                name="manage_build", requires_polling=True, poll_action="status"
            )
        ),
    )
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    sent = []

    async def send(fn, instance, command, params, **kwargs):
        sent.append((dict(params), kwargs))
        if params["action"] == "batch":
            return {
                "success": True,
                "_mcp_status": "pending",
                "data": {"job_id": "batch-new", "total": 2},
            }
        if params.get("job_id") != "batch-new":
            # Actual no-ID ManageBuild status prefers the last completed child.
            return {"success": True, "data": {"job_id": "build-first", "result": "succeeded"}}
        if len(sent) == 2:
            return {
                "success": True,
                "_mcp_status": "pending",
                "data": {"job_id": "batch-new", "completed": 1},
            }
        return final

    monkeypatch.setattr(module, "send_with_unity_instance", send)
    original = {"action": "batch", "targets": ["windows64", "linux64"], **caller}
    result = await service.execute_tool(
        "project", "manage_build", "Project@hash", original, user_id="user-a"
    )
    assert len(sent) == 3
    assert result.data == final["data"]
    assert result.success is final["success"]
    assert result.error == final.get("error")
    assert all(
        params["job_id"] == "batch-new" and kwargs == {"user_id": "user-a"}
        for params, kwargs in sent[1:]
    )
    assert original == {"action": "batch", "targets": ["windows64", "linux64"], **caller}


@pytest.mark.asyncio
async def test_response_job_id_does_not_override_explicit_caller_id(monkeypatch):
    service = object.__new__(CustomToolService)
    send = AsyncMock(return_value={"success": True, "data": {"job_id": "caller-id"}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    original = {"action": "start", "job_id": "caller-id"}
    result = await service._poll_until_complete(
        "fixture",
        "Project@hash",
        original,
        {"_mcp_status": "pending", "data": {"job_id": "other-id"}},
        "status",
    )
    assert result.data["job_id"] == "caller-id"
    assert send.call_args.args[3] == {"action": "status", "job_id": "caller-id"}
    assert original == {"action": "start", "job_id": "caller-id"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "raw",
        {},
        {"job_id": None},
        {"job_id": ""},
        {"job_id": "   "},
        {"job_id": 0},
        {"job_id": False},
        {"job_id": ["id"]},
    ],
)
async def test_pending_without_valid_response_id_preserves_original_poll_contract(
    monkeypatch, data
):
    service = object.__new__(CustomToolService)
    send = AsyncMock(return_value={"success": True, "data": {"value": 0}})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    result = await service._poll_until_complete(
        "fixture",
        "Project@hash",
        {"action": "start", "enabled": False},
        {"_mcp_status": "pending", "data": data},
        "status",
    )
    assert result.data == {"value": 0}
    assert send.call_args.args[3] == {"action": "status", "enabled": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"success": False, "error": "Rejected", "data": {"job_id": "unused"}},
        {"success": True, "_mcp_status": "complete", "data": {"job_id": "unused", "value": False}},
    ],
)
async def test_terminal_initial_response_does_not_poll_or_change_payload(monkeypatch, response):
    service = object.__new__(CustomToolService)
    send = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    result = await service._poll_until_complete(
        "fixture", "Project@hash", {"action": "start"}, response, "status"
    )
    send.assert_not_awaited()
    assert result.success is response["success"]
    assert result.error == response.get("error")
    assert result.data == response["data"]
