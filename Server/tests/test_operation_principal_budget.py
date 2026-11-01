"""Queued requests and actual workers share a principal's retention budget."""

import asyncio
from dataclasses import replace

import pytest

from services.registry.operation_scheduler import (
    OperationAdmission,
    OperationCapacityError,
    OperationScheduler,
    OperationScope,
)
from tests.test_operation_scheduler import settled


def request(principal="remote:alice", instance="one", *, charge=4):
    return OperationAdmission(
        OperationScope(principal, instance, "g1"),
        "exclusive",
        lambda: True,
        retained_bytes=charge,
    )


@pytest.mark.asyncio
async def test_principal_budget_counts_queue_across_instances_and_releases_cancelled_waiter():
    scheduler = OperationScheduler(max_retained_bytes=16, max_retained_bytes_per_principal=8)

    async def waiting():
        async with scheduler.session(request()):
            pytest.fail("Cancelled waiter entered")

    async with scheduler.session(request()):
        queued = asyncio.create_task(waiting())
        try:
            await settled()
            assert scheduler.snapshot()["waiting"] == 1
            with pytest.raises(OperationCapacityError):
                async with scheduler.session(request(instance="two", charge=1)):
                    pytest.fail("Principal exceeded its retained-byte quota")
            async with scheduler.session(request("remote:bob", "two", charge=7)):
                assert scheduler.snapshot()["retained_bytes"] == 15
        finally:
            queued.cancel()
            await asyncio.gather(queued, return_exceptions=True)
        async with scheduler.session(request(instance="two")):
            assert scheduler.snapshot()["retained_bytes"] == 8
    assert scheduler.snapshot()["retained_bytes"] == 0
    assert scheduler._principal_bytes == {}


@pytest.mark.asyncio
async def test_principal_resize_and_closed_owner_keep_charge_until_worker_pin_exits():
    scheduler = OperationScheduler(max_retained_bytes=16, max_retained_bytes_per_principal=8)
    async with scheduler.session(request()) as owner:
        owner.request = replace(
            owner.request, retained_bytes=8, scope=replace(owner.request.scope, generation="g2")
        )
        with pytest.raises(OperationCapacityError):
            owner.request = replace(owner.request, retained_bytes=9)
        assert scheduler.snapshot()["retained_bytes"] == 8
        owner.request = replace(owner.request, retained_bytes=6)
        pin = owner.acquire_worker()
    with pytest.raises(OperationCapacityError):
        async with scheduler.session(request(instance="two", charge=3)):
            pytest.fail("Live worker lost its principal charge")
    async with scheduler.session(request("remote:bob", charge=8)):
        assert scheduler.snapshot()["retained_bytes"] == 14
    pin.close()
    pin.close()
    owner.close()
    assert scheduler.snapshot()["retained_bytes"] == 0
    assert scheduler._principal_bytes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", ["remote:alice", "remote:local"])
async def test_default_remote_quota_preserves_local_global_allowance(principal):
    scheduler = OperationScheduler()
    charge = 512 * 1024 * 1024 + 1
    with pytest.raises(OperationCapacityError):
        async with scheduler.session(request(principal, charge=charge)):
            pytest.fail("Remote principal exceeded the 512 MiB default")
    async with scheduler.session(request("local", charge=charge)):
        assert scheduler.snapshot()["retained_bytes"] == charge
    assert scheduler._principal_bytes == {}
