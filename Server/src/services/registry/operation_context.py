"""Opaque operation ownership delegated only to actual submitted work."""

import asyncio  # noqa: ANYIO_OK -- existing transport task ownership.
from collections.abc import Coroutine
from contextvars import Context, ContextVar, copy_context
from typing import TypeVar
from weakref import WeakKeyDictionary

from services.registry.operation_scheduler import (
    OperationLease,
    OperationScheduler,
    OperationScopeChanged,
    _WorkerPin,
)

T = TypeVar("T")
current_operation: ContextVar[OperationLease | None] = ContextVar("unity_operation", default=None)
_delegated: ContextVar[_WorkerPin | None] = ContextVar("unity_operation_worker", default=None)
_schedulers: WeakKeyDictionary[asyncio.AbstractEventLoop, OperationScheduler] = WeakKeyDictionary()


def scheduler() -> OperationScheduler:
    loop = asyncio.get_running_loop()
    value = _schedulers.get(loop)
    if value is None:
        value = OperationScheduler()
        _schedulers[loop] = value
    return value


def active_operation() -> OperationLease | None:
    owner = current_operation.get()
    if owner is not None and owner.closed:
        delegated = _delegated.get()
        if delegated is None or delegated._owner is not owner:
            raise OperationScopeChanged()
    return owner


def capture_operation_worker(context: Context) -> _WorkerPin | None:
    """Carry only opaque admitted ownership into an actual executor/task."""
    owner = active_operation()
    if owner is None:
        return None
    pin = owner._pin()
    context.run(current_operation.set, owner)
    context.run(_delegated.set, pin)
    return pin


def operation_task(coroutine: Coroutine[None, None, T]) -> asyncio.Task[T]:
    context = copy_context()
    pin = capture_operation_worker(context)
    try:
        task = asyncio.create_task(coroutine, context=context)
    except BaseException:
        if pin is not None:
            pin.close()
        coroutine.close()
        raise
    if pin is not None:
        task.add_done_callback(lambda _: pin.close())
    return task
