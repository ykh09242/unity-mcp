"""Reviewed recovery controls have bounded, separately owned overflow capacity."""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace

import pytest

from services.control_capacity import reserved_control
from services.registry.operation_scheduler import (
    OperationAdmission,
    OperationCapacityError,
    OperationScheduler,
    OperationScope,
)


def request(principal="remote:alice", instance="one", *, control=True, charge=4):
    return OperationAdmission(
        OperationScope(principal, instance, "g1"),
        "control" if control else "exclusive",
        lambda: True,
        retained_bytes=charge,
        control_reserve=control,
    )


@pytest.mark.parametrize(
    "name,params,trusted,expected",
    [
        ("get_compile_job", {"compile_id": "one"}, True, True),
        ("manage_build", {"action": "cancel"}, True, True),
        ("manage_packages", {"action": "STATUS"}, True, True),
        ("get_test_job", {}, False, False),
        ("unknown_custom_tool", {"action": "status", "mode": "control"}, True, False),
        ("execute_custom_tool", {"action": "status"}, True, False),
        ("manage_scene", {"action": "status", "control_reserve": True}, True, False),
        ("manage_build", {"action": "build"}, True, False),
        ("get_compile_job", {"action": "status"}, True, False),
        ("export_test_job", {}, True, False),
        ("view_test_results", {}, True, False),
        ("manage_project_auditor", {"action": "issues"}, True, False),
    ],
)
def test_only_reviewed_trusted_routes_receive_reserve(name, params, trusted, expected):
    assert reserved_control(name, params, trusted_builtin=trusted) is expected
    assert reserved_control(name, params, trusted_builtin=trusted, native=True) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits",
    [
        {"max_participants": 1},
        {"max_per_instance": 1},
        {"max_retained_bytes": 4},
        {"max_retained_bytes_per_principal": 4},
    ],
)
async def test_ordinary_saturation_admits_one_control_without_lending_its_reserve(limits):
    scheduler = OperationScheduler(**limits)
    async with scheduler.session(request(control=False)):
        # A mode alone is not evidence that a public/native route was reviewed.
        with pytest.raises(OperationCapacityError):
            async with scheduler.session(replace(request(), control_reserve=False)):
                pytest.fail("Unreviewed control borrowed recovery capacity")
        async with scheduler.session(request()) as owner:
            assert owner._reserve is not None
            assert scheduler.snapshot()["active"] == 2
            assert scheduler.snapshot()["retained_bytes"] == 8
            with pytest.raises(OperationCapacityError):
                async with scheduler.session(request()):
                    pytest.fail("Control flood exceeded per-instance reserve")
    assert scheduler.snapshot() == {"instances": 0, "active": 0, "waiting": 0, "retained_bytes": 0}
    assert scheduler._controls == {} and scheduler._principal_bytes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,principal,instance",
    [
        ("max_control_participants", "remote:bob", "two"),
        ("max_control_per_principal", "remote:alice", "two"),
        ("max_control_per_instance", "remote:alice", "one"),
        ("max_control_bytes", "remote:bob", "two"),
        ("max_control_bytes_per_principal", "remote:alice", "two"),
        ("max_control_bytes_per_instance", "remote:alice", "one"),
    ],
)
async def test_each_reserve_count_and_byte_ceiling_is_enforced(limit, principal, instance):
    scheduler = OperationScheduler(
        max_participants=0,
        **{limit: 4 if "bytes" in limit else 1},
    )
    async with scheduler.session(request()):
        with pytest.raises(OperationCapacityError):
            async with scheduler.session(request(principal, instance, charge=1)):
                pytest.fail("Control exceeded its count/byte ceiling")
    assert scheduler.snapshot()["retained_bytes"] == 0
    assert scheduler._controls == {}


@pytest.mark.asyncio
async def test_reserve_keeps_tenant_limits_and_frees_ordinary_capacity_independently():
    scheduler = OperationScheduler(max_participants=1)
    async with AsyncExitStack() as stack:
        ordinary = await stack.enter_async_context(scheduler.session(request(control=False)))
        reserved = await stack.enter_async_context(scheduler.session(request()))
        await stack.enter_async_context(scheduler.session(request(instance="two")))
        with pytest.raises(OperationCapacityError):
            async with scheduler.session(request(instance="three")):
                pytest.fail("Alice borrowed another principal's control quota")
        await stack.enter_async_context(scheduler.session(request("remote:bob", "one")))
        ordinary.close()
        async with scheduler.session(request(control=False)) as following:
            assert following._reserve is None and reserved._reserve is not None
    assert scheduler.snapshot()["retained_bytes"] == 0


@pytest.mark.asyncio
async def test_cancelled_control_keeps_reservation_until_actual_worker_settles():
    scheduler = OperationScheduler(max_participants=0, max_control_bytes_per_instance=8)
    started, settle = asyncio.Event(), asyncio.Event()
    workers = []

    async def control():
        async with scheduler.session(request()) as owner:
            owner.request = replace(owner.request, retained_bytes=8)
            with pytest.raises(OperationCapacityError):
                owner.request = replace(owner.request, retained_bytes=9)
            pin = owner.acquire_worker()

            async def worker():
                try:
                    await settle.wait()
                finally:
                    pin.close()

            workers.append(asyncio.create_task(worker()))
            started.set()
            await asyncio.Event().wait()

    caller = asyncio.create_task(control())
    await started.wait()
    caller.cancel()
    await asyncio.gather(caller, return_exceptions=True)
    assert scheduler.snapshot()["retained_bytes"] == 8
    with pytest.raises(OperationCapacityError):
        async with scheduler.session(request()):
            pytest.fail("Cancelled invocation released a still-running worker's reserve")
    settle.set()
    await asyncio.gather(*workers)
    assert scheduler.snapshot() == {"instances": 0, "active": 0, "waiting": 0, "retained_bytes": 0}
    assert scheduler._controls == {} and scheduler._principal_bytes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [True, False])
async def test_public_control_precheck_preserves_trusted_scheduler_admission(monkeypatch, trusted):
    from services.registry import operation_invocation as mod
    from services.registry.operation_context import active_operation
    from services.registry.operation_target import OperationTarget
    from tests.test_operation_invocation import Context

    scheduler = OperationScheduler(max_participants=0)
    entered = []

    async def target(*_args, **_kwargs):
        return OperationTarget(
            OperationScope("local", "deadbeef", "g1"),
            lambda: True,
            True,
            capacity_error=lambda: {"success": False, "error": "ordinary_capacity"},
        )

    async def authorize(*_args, **_kwargs):
        return None

    async def status(ctx):
        owner = active_operation()
        entered.append(owner._reserve is not None and owner.request.control_reserve)
        return {"success": True}

    monkeypatch.setattr(mod, "capture_target", target)
    monkeypatch.setattr(mod, "authorize_remote_operation", authorize)
    monkeypatch.setattr(mod, "trusted_tool", lambda *_: trusted)
    monkeypatch.setattr(mod, "scheduler", lambda: scheduler)
    result = await mod.wrap_tool(status, "get_compile_job", "get_compile_job")(Context())
    assert result["success"] is trusted
    assert entered == ([True] if trusted else [])
    assert scheduler.snapshot()["retained_bytes"] == 0 and scheduler._controls == {}
