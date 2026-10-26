"""Held signals verify operation fairness, ownership and bounded cancellation."""

import asyncio  # noqa: ANYIO_OK -- the production scheduler uses the existing loop.

import pytest

from services.registry.operation_scheduler import (
    OperationAdmission,
    OperationCapacityError,
    OperationScheduler,
    OperationScope,
    OperationScopeChanged,
)
from tests.timing import await_signal, watchdog_timeout


def admission(instance="one", mode="exclusive", *, generation="g1", current=None, charge=1):
    scope = OperationScope("local", instance, generation)
    return OperationAdmission(scope, mode, current or (lambda: True), retained_bytes=charge)


async def settled():
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_writer_fences_later_reads_but_control_and_other_instance_progress():
    scheduler = OperationScheduler()
    order = []
    writer_started, reader_started = asyncio.Event(), asyncio.Event()
    release_writer = asyncio.Event()

    async def writer():
        async with scheduler.session(admission(mode="exclusive")):
            order.append("writer")
            writer_started.set()
            await release_writer.wait()

    async def later_read():
        async with scheduler.session(admission(mode="shared_read")):
            order.append("later_read")
            reader_started.set()

    async with scheduler.session(admission(mode="shared_read")):
        waiting_writer = asyncio.create_task(writer())
        await settled()
        waiting_reader = asyncio.create_task(later_read())
        await settled()
        async with scheduler.session(admission(mode="control")):
            order.append("control")
        async with scheduler.session(admission(instance="two")):
            order.append("independent")
        assert not writer_started.is_set() and not reader_started.is_set()
    await await_signal(writer_started, "writer admission")
    assert not reader_started.is_set()
    release_writer.set()
    await asyncio.gather(waiting_writer, waiting_reader)
    assert order == ["control", "independent", "writer", "later_read"]
    assert scheduler.snapshot() == {"instances": 0, "active": 0, "waiting": 0, "retained_bytes": 0}


@pytest.mark.asyncio
async def test_two_readers_overlap_and_principals_do_not_share_ownership():
    scheduler = OperationScheduler()
    async with scheduler.session(admission(mode="shared_read")):
        async with scheduler.session(admission(mode="shared_read")):
            assert scheduler.snapshot()["active"] == 2
    assert scheduler.snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_cancelled_queued_writer_never_enters_and_unblocks_reader():
    scheduler = OperationScheduler()
    entered = []

    async def contender():
        async with scheduler.session(admission()):
            entered.append(True)

    async with scheduler.session(admission(mode="shared_read")):
        queued = asyncio.create_task(contender())
        await settled()
        assert scheduler.snapshot()["waiting"] == 1
        queued.cancel()
        await asyncio.gather(queued, return_exceptions=True)
        async with scheduler.session(admission(mode="shared_read")):
            assert entered == []
    assert scheduler.snapshot()["retained_bytes"] == 0


@pytest.mark.asyncio
async def test_running_cancel_keeps_worker_pin_until_actual_settlement():
    scheduler = OperationScheduler()
    started = asyncio.Event()
    pins = []
    successor_started = asyncio.Event()

    async def running():
        async with scheduler.session(admission()) as owner:
            pin = owner.acquire_worker()
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                # A real executor completion callback owns this pin.
                pins.append(pin)

    async def successor():
        async with scheduler.session(admission()):
            successor_started.set()

    task = asyncio.create_task(running())
    await await_signal(started, "worker admission")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    following = asyncio.create_task(successor())
    await settled()
    assert not successor_started.is_set()
    pins.pop().close()
    await asyncio.wait_for(following, watchdog_timeout())
    assert scheduler.snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_generation_changed_while_queued_is_refused_without_retargeting():
    scheduler = OperationScheduler()
    current = ["g1"]
    entered = []

    async def contender():
        request = admission(current=lambda: current[0] == "g1")
        with pytest.raises(OperationScopeChanged):
            async with scheduler.session(request):
                entered.append(True)

    async with scheduler.session(admission()):
        queued = asyncio.create_task(contender())
        await settled()
        current[0] = "g2"
    await queued
    assert entered == [] and scheduler.snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_original_deadline_expires_in_queue_and_capacity_is_reclaimed():
    scheduler = OperationScheduler(max_retained_bytes=8)
    async with scheduler.session(admission(charge=4)):
        request = admission(charge=4)
        request = OperationAdmission(
            request.scope,
            request.mode,
            request.is_current,
            expires_at=asyncio.get_running_loop().time() + 0.02,
            retained_bytes=4,
        )
        with pytest.raises(TimeoutError):
            async with scheduler.session(request):
                pytest.fail("Expired request was dispatched")
        assert scheduler.snapshot()["waiting"] == 0
        with pytest.raises(OperationCapacityError):
            async with scheduler.session(admission(charge=5)):
                pytest.fail("Over-capacity request was admitted")
    assert scheduler.snapshot() == {"instances": 0, "active": 0, "waiting": 0, "retained_bytes": 0}
