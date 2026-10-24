"""Bound response CPU work without releasing owners before workers finish."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from itertools import chain
from threading import Lock
from typing import TypeVar
from weakref import WeakKeyDictionary, ref

import anyio
from pydantic import BaseModel

from services.execution_receipt_context import submit_owned_executor

T = TypeVar("T")
_states: WeakKeyDictionary = WeakKeyDictionary()


class _Workers:
    def __init__(self):
        self.slots = asyncio.Semaphore(2)


def needs_response_worker(value: object) -> bool:
    """Bound the scheduling probe; this is not validation or a workload limit."""
    pending = [iter((value,))]
    nodes = 0
    while pending and nodes < 256:
        try:
            item = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        nodes += 1
        item_type = type(item)
        if item_type is str:
            if len(item) >= 64 * 1024:
                return True
        elif item_type is dict:
            pending.append(iter(chain.from_iterable(item.items())))
        elif item_type is list or item_type is tuple:
            pending.append(iter(item))
        elif item is None or item_type is int or item_type is float or item_type is bool:
            continue
        elif isinstance(item, BaseModel):
            # Stored values include SDK content, raw results, extras and excluded
            # fields. Inspect them conservatively without dumping a model or
            # calling extension serialization hooks. Iterators keep this probe
            # bounded even for large containers and cyclic model storage.
            storage = BaseModel.__dict__["__dict__"].__get__(item)
            extra = BaseModel.__dict__["__pydantic_extra__"].__get__(item)
            private = BaseModel.__dict__["__pydantic_private__"].__get__(item)
            pending.append(iter((storage, extra, private)))
        else:
            # Unknown/subclass containers may hide arbitrary stored work. Do not
            # invoke their length/iteration hooks in the scheduling probe.
            return True
    return bool(pending)


class _Operation:
    def __init__(self, call):
        self._lock = Lock()
        self._call = call
        self._started = False

    def run(self):
        with self._lock:
            call, self._call = self._call, None
            if call is None:
                return None
            self._started = True
        return call()

    def abort_before_start(self):
        with self._lock:
            if self._started:
                return False
            self._call = None
            return True


async def response_work(operation: Callable[[], T], *, large: bool) -> T:
    """Keep queued work cancellable, then settle actual workers before release."""
    if not large:
        return operation()
    loop = asyncio.get_running_loop()
    weak = _states.get(loop)
    state = weak() if weak is not None else None
    if state is None:
        state = _Workers()
        # A semaphore may bind to its loop. Only active/waiting callers retain
        # this state, so closing a loop cannot leave a process-wide strong cycle.
        _states[loop] = ref(state)
    async with state.slots:
        work = _Operation(operation)
        del operation
        future = submit_owned_executor(loop, None, work.run)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if work.abort_before_start():
                # The executor can later run the empty holder. No response graph
                # survives in its queue and the real future keeps its receipt pin.
                raise
            # A running worker still touches the graph. Keep its owners alive
            # under both level cancellation and repeated Task.cancel() calls.
            with anyio.CancelScope(shield=True):
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
                if not future.cancelled():
                    future.exception()
            raise
