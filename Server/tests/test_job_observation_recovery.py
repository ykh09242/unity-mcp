"""Job reads recover a pre-dispatch reconnect without replaying execution."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.config import config
from services.operation_policy import OperationMode
from services.registry.operation_invocation import admission_for, operation_session
from services.registry.operation_scheduler import OperationScope
from services.registry.operation_target import OperationTarget
from transport import unity_transport as transport


@pytest.mark.asyncio
async def test_observation_deadline_does_not_reuse_earlier_refusal_as_dispatch_proof(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    original = OperationTarget(OperationScope("local", "owned", "old"), lambda: True, True)
    monkeypatch.setattr(
        "services.registry.operation_target.capture_target", AsyncMock(return_value=original)
    )
    entered, cancelled = asyncio.Event(), asyncio.Event()
    count = 0

    async def sender(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            return {
                "success": False,
                "data": {"reason": "operation_target_changed", "dispatch": "not_dispatched"},
            }
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    deadline = asyncio.get_running_loop().time() + 0.2
    async with operation_session(
        admission_for(original, OperationMode.CONTROL, expires_at=deadline)
    ):
        result = await transport.send_job_observation(
            sender, "Main@owned", "get_test_job", {"job_id": "existing-job"}
        )
    assert entered.is_set() and cancelled.is_set()
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["data"].get("dispatch") != "not_dispatched"
    assert result["data"]["next_action"] == "poll_same_job"


@pytest.mark.asyncio
async def test_observation_cancellation_is_propagated_and_drained(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    original = OperationTarget(OperationScope("local", "owned", "old"), lambda: True, True)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def sender(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def call():
        async with operation_session(
            admission_for(original, OperationMode.CONTROL, expires_at=None)
        ):
            return await transport.send_job_observation(
                sender, "Main@owned", "get_test_job", {"job_id": "existing-job"}
            )

    task = asyncio.create_task(call())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,key", [("get_test_job", "job_id"), ("get_compile_job", "compile_id")]
)
async def test_same_job_reconnect_read_preserves_identity_and_deadline(monkeypatch, command, key):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    old = OperationTarget(OperationScope("local", "owned", "old"), lambda: True, True)
    fresh = OperationTarget(OperationScope("local", "owned", "fresh"), lambda: True, True)
    capture = AsyncMock(return_value=fresh)
    monkeypatch.setattr("services.registry.operation_target.capture_target", capture)
    sender = AsyncMock(
        side_effect=[
            {
                "success": False,
                "error": "operation_target_changed",
                "data": {"reason": "operation_target_changed", "dispatch": "not_dispatched"},
            },
            {"success": True, "data": {key: "existing-job", "passed": 272}},
        ]
    )
    deadline = asyncio.get_running_loop().time() + 5
    async with operation_session(admission_for(old, OperationMode.CONTROL, expires_at=deadline)):
        result = await transport.send_job_observation(
            sender, "Main@owned", command, {key: "existing-job"}
        )
    assert result["success"] and result["data"]["passed"] == 272
    assert sender.await_count == 2
    assert all(call.args == (command, {key: "existing-job"}) for call in sender.await_args_list)
    assert all(call.kwargs["instance_id"] == "Main@owned" for call in sender.await_args_list)
    assert (
        sender.await_args_list[0].kwargs["_deadline"]
        == sender.await_args_list[1].kwargs["_deadline"]
    )
    assert sender.await_args_list[0].kwargs["_deadline"] <= deadline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["permission_denied", "auth_required", "outcome_unknown", "job_not_found", "result_capacity"],
)
async def test_permanent_or_uncertain_observation_failure_is_not_replayed(monkeypatch, reason):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    sender = AsyncMock(return_value={"success": False, "data": {"reason": reason}})
    owner = OperationTarget(OperationScope("local", "owned", "old"), lambda: True, True)
    async with operation_session(admission_for(owner, OperationMode.CONTROL, expires_at=None)):
        result = await transport.send_job_observation(
            sender, "Main@owned", "get_test_job", {"job_id": "existing-job"}
        )
    assert result["data"]["reason"] == reason
    assert sender.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("principal,instance", [("remote:other", "owned"), ("local", "other")])
async def test_recovery_never_uses_another_project_or_principal(monkeypatch, principal, instance):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    original = OperationTarget(OperationScope("local", "owned", "old"), lambda: True, True)
    changed = OperationTarget(OperationScope(principal, instance, "new"), lambda: True, True)
    monkeypatch.setattr(
        "services.registry.operation_target.capture_target", AsyncMock(return_value=changed)
    )
    sender = AsyncMock(return_value={"success": False, "error": "operation_target_changed"})
    async with operation_session(admission_for(original, OperationMode.CONTROL, expires_at=None)):
        result = await transport.send_job_observation(
            sender, "Main@owned", "get_test_job", {"job_id": "existing-job"}
        )
    assert result["success"] is False and sender.await_count == 1
