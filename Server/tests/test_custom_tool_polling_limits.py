"""Bound whole polling executions, including initial dispatch and sleeping tasks."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from core.config import config
from models.models import ToolDefinitionModel
import services.custom_tool_service as module
from services.custom_tool_service import CustomToolService


class _Mcp:
    def custom_route(self, *args, **kwargs):
        return lambda fn: fn


@pytest.fixture
def service():
    return CustomToolService(_Mcp())


@pytest.mark.parametrize("seconds", [0, 1, 600])
def test_polling_schema_accepts_bounded_seconds(seconds):
    assert ToolDefinitionModel(name="build", max_poll_seconds=seconds).max_poll_seconds == seconds


@pytest.mark.parametrize("seconds", [-1, 601, 10**100])
def test_polling_schema_rejects_out_of_range_seconds(seconds):
    with pytest.raises(ValidationError):
        ToolDefinitionModel(name="build", max_poll_seconds=seconds)


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [0, -1, 1, 600, 601, 10**100])
async def test_poll_execution_clamps_unvalidated_metadata(service, monkeypatch, seconds):
    # Given: a constructed/mutated internal definition bypasses schema validation.
    now = [0.0]

    async def advance(delay):
        now[0] += delay

    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    monkeypatch.setattr(
        module,
        "send_with_unity_instance",
        AsyncMock(return_value={"_mcp_status": "pending", "_mcp_poll_interval": 5}),
    )
    definition = ToolDefinitionModel.model_construct(
        name="build", requires_polling=True, max_poll_seconds=seconds
    )
    monkeypatch.setattr(service, "get_tool_definition", AsyncMock(return_value=definition))
    # When: the execution never completes.
    result = await service.execute_tool("project", "build", "Project@hash", {})
    # Then: even bypassed metadata cannot extend work past the server cap.
    assert not result.success
    assert now[0] == (1 if seconds == 1 else 600)
    assert service._active_polls == 0
    assert service._polls_by_session == service._polls_by_user == {}


@pytest.mark.asyncio
async def test_initial_dispatch_counts_toward_deadline(service, monkeypatch):
    # Given: a dispatch completes after its one-second execution deadline.
    now = [0.0]

    async def late_response(*args, **kwargs):
        now[0] = 1.1
        return {"_mcp_status": "complete", "data": {"value": 1}}

    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        service,
        "get_tool_definition",
        AsyncMock(
            return_value=ToolDefinitionModel(
                name="build", requires_polling=True, max_poll_seconds=1
            )
        ),
    )
    monkeypatch.setattr(module, "send_with_unity_instance", late_response)
    # When: the caller runs the tool.
    result = await service.execute_tool("project", "build", "Project@hash", {})
    # Then: a late success cannot restart or escape the execution deadline.
    assert not result.success
    assert "Timeout" in result.message
    assert service._active_polls == 0
    assert service._polls_by_session == service._polls_by_user == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,limit", [("session", 16), ("user", 32), ("global", 256)])
@pytest.mark.parametrize("phase", ["dispatch", "sleep"])
async def test_active_polling_capacity_is_retained_and_released(
    service, monkeypatch, scope, limit, phase
):
    # Given: admitted executions are blocked in initial dispatch or between polls.
    entered = asyncio.Queue()
    blocked = asyncio.Event()

    async def send(*args, **kwargs):
        if phase == "dispatch":
            entered.put_nowait(True)
            await blocked.wait()
        return {"_mcp_status": "pending"}

    async def sleep(delay):
        entered.put_nowait(True)
        await blocked.wait()

    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    monkeypatch.setattr(
        service,
        "get_tool_definition",
        AsyncMock(return_value=ToolDefinitionModel(name="build", requires_polling=True)),
    )
    tasks = []

    def identity(index):
        if scope == "session":
            return "user-a", "Project@hash"
        if scope == "user":
            return "user-a", f"Project@hash-{index}"
        return f"user-{index}", f"Project@hash-{index}"

    try:
        for index in range(limit):
            user, target = identity(index)
            tasks.append(
                asyncio.create_task(
                    service.execute_tool("project", "build", target, {}, user_id=user)
                )
            )
            await asyncio.wait_for(entered.get(), timeout=1)
        user, target = identity(limit)
        if scope == "session":
            target = "HASH"  # An alternate spelling of the same Unity target.
        # When: one more execution reaches the same constrained scope.
        result = await asyncio.wait_for(
            service.execute_tool("project", "build", target, {}, user_id=user), timeout=0.05
        )
        # Then: reject immediately without dispatching or queuing another waiter.
        assert not result.success
        assert result.hint == "retry"
        assert "capacity" in result.message.lower()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert service._active_polls == 0
    assert service._polls_by_session == service._polls_by_user == {}
    # Cancellation returns every reservation; an ordinary invocation can succeed.
    monkeypatch.setattr(
        module, "send_with_unity_instance", AsyncMock(return_value={"_mcp_status": "complete"})
    )
    assert (
        await service.execute_tool("project", "build", "Project@hash", {}, user_id="user-a")
    ).success


@pytest.mark.asyncio
async def test_missing_remote_principal_cannot_dispatch(service, monkeypatch):
    # Given: a remote request has no authenticated tenant identity.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    lookup = AsyncMock(return_value=ToolDefinitionModel(name="build", requires_polling=True))
    send = AsyncMock(return_value={"_mcp_status": "complete"})
    monkeypatch.setattr(service, "get_tool_definition", lookup)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    # When: execution is attempted without a user.
    result = await service.execute_tool("project", "build", "Project@hash", {})
    # Then: neither catalog lookup nor plugin dispatch can cross tenants.
    assert not result.success
    lookup.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_implicit_target_cannot_bypass_session_admission(service, monkeypatch):
    # Given: the service is called without a selected instance.
    monkeypatch.setattr(
        service,
        "get_tool_definition",
        AsyncMock(return_value=ToolDefinitionModel(name="build", requires_polling=True)),
    )
    send = AsyncMock(return_value={"_mcp_status": "complete"})
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    # When: an implicit selection is requested.
    result = await service.execute_tool("catalog-id", "build", None, {})
    # Then: the caller must select a canonical target before admission/dispatch.
    assert not result.success
    assert "Explicit Unity instance" in result.message
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["initial", "poll"])
async def test_dispatch_deadline_cancels_work_and_releases_capacity(service, monkeypatch, phase):
    # Given: a cooperative dispatch blocks after advancing beyond the deadline.
    now = [0.0]
    cancelled = []
    requests = []

    async def send(*args, **kwargs):
        requests.append(args[3]["action"])
        if phase == "poll" and len(requests) == 1:
            now[0] = 0.8
            return {"_mcp_status": "pending", "_mcp_poll_interval": 0.1}
        now[0] = 1.1
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def advance(delay):
        now[0] += delay

    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(
        service,
        "get_tool_definition",
        AsyncMock(
            return_value=ToolDefinitionModel(
                name="build", requires_polling=True, max_poll_seconds=1
            )
        ),
    )
    # When: dispatch remains blocked until the event-loop deadline fires.
    result = await service.execute_tool("project", "build", "Project@hash", {"action": "start"})
    # Then: cancel at the original deadline and clear both scopes exactly once.
    assert not result.success
    assert cancelled == [True]
    assert requests == (["start"] if phase == "initial" else ["start", "status"])
    assert service._active_polls == 0
    assert service._polls_by_session == service._polls_by_user == {}


@pytest.mark.asyncio
async def test_initial_dispatch_error_releases_capacity(service, monkeypatch):
    # Given: the transport fails after admission.
    monkeypatch.setattr(
        service,
        "get_tool_definition",
        AsyncMock(return_value=ToolDefinitionModel(name="build", requires_polling=True)),
    )
    monkeypatch.setattr(
        module,
        "send_with_unity_instance",
        AsyncMock(side_effect=RuntimeError("fixture transport failed")),
    )
    # When: the error propagates to the caller.
    with pytest.raises(RuntimeError, match="fixture transport failed"):
        await service.execute_tool("project", "build", "Project@hash", {})
    # Then: no polling capacity or tenant key remains retained.
    assert service._active_polls == 0
    assert service._polls_by_session == service._polls_by_user == {}


@pytest.mark.asyncio
async def test_same_target_budgets_are_isolated_between_users(service, monkeypatch):
    # Given: one user's session is saturated; another user has the same hash.
    entered = asyncio.Queue()
    blocked = asyncio.Event()
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(
        service,
        "get_tool_definition",
        AsyncMock(return_value=ToolDefinitionModel(name="build", requires_polling=True)),
    )

    async def send(*args, **kwargs):
        if kwargs["user_id"] == "user-a":
            entered.put_nowait(True)
            await blocked.wait()
        return {"_mcp_status": "complete", "data": {"owner": kwargs["user_id"]}}

    monkeypatch.setattr(module, "send_with_unity_instance", send)
    tasks = []
    try:
        for index in range(16):
            tasks.append(
                asyncio.create_task(
                    service.execute_tool("project", "build", "Project@hash", {}, user_id="user-a")
                )
            )
            await asyncio.wait_for(entered.get(), timeout=1)
        # When: the other tenant executes its own tool on its own same-hash target.
        result = await service.execute_tool(
            "project", "build", "Project@hash", {}, user_id="user-b"
        )
        # Then: first-user capacity and responses cannot leak to the other user.
        assert result.success
        assert result.data == {"owner": "user-b"}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert service._active_polls == 0
    assert service._polls_by_session == service._polls_by_user == {}
