"""Cancellation preserves actual workers without delegating request context."""

import asyncio  # noqa: ANYIO_OK -- exercise production loop/executor ownership.
from contextvars import ContextVar
import threading

import pytest

from services.execution_receipt_context import owned_to_thread
from services.registry.operation_context import (
    active_operation,
    current_operation,
    operation_task,
)
from services.registry.operation_scheduler import (
    OperationAdmission,
    OperationScheduler,
    OperationScope,
    OperationScopeChanged,
)
from tests.timing import await_signal, watchdog_timeout


def request():
    return OperationAdmission(OperationScope("local", "one", "g1"), "exclusive", lambda: True)


@pytest.mark.asyncio
async def test_cancelled_executor_await_keeps_fence_until_actual_worker_returns():
    scheduler = OperationScheduler()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    request_material = ContextVar("owned_test_request_material", default=None)
    seen = []

    def work():
        seen.append((active_operation() is not None, request_material.get()))
        loop.call_soon_threadsafe(started.set)
        assert release.wait(watchdog_timeout())

    async with scheduler.session(request()) as owner:
        token = current_operation.set(owner)
        secret_token = request_material.set("request-only")
        try:
            worker = asyncio.create_task(owned_to_thread(work))
            await await_signal(started, "executor started")
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        finally:
            request_material.reset(secret_token)
            current_operation.reset(token)
    assert scheduler.snapshot()["active"] == 1
    assert seen == [(True, None)]
    release.set()
    # An admitted successor observes the actual completion callback, not a sleep.
    async with scheduler.session(request()):
        assert scheduler.snapshot()["active"] == 1
    assert scheduler.snapshot()["instances"] == 0


@pytest.mark.asyncio
async def test_only_explicit_actual_task_pin_can_outlive_closed_invocation_context():
    scheduler = OperationScheduler()
    release = asyncio.Event()

    async def child():
        await release.wait()
        return active_operation()

    async with scheduler.session(request()) as owner:
        token = current_operation.set(owner)
        try:
            actual_worker = operation_task(child())
            inherited_only = asyncio.create_task(child())
        finally:
            current_operation.reset(token)
    assert scheduler.snapshot()["active"] == 1
    release.set()
    assert await actual_worker is owner
    with pytest.raises(OperationScopeChanged):
        await inherited_only
    async with scheduler.session(request()):
        pass
    assert scheduler.snapshot()["instances"] == 0
