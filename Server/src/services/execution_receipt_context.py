"""Opaque invocation ownership at native and actual blocking-worker boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import Context, ContextVar
from typing import Protocol, TypeVar

from services.execution_receipt_identity import JsonValue

T = TypeVar("T")


class Worker(Protocol):
    def close(self) -> None: ...


class ExecutionOwner(Protocol):
    expires_at: float

    def acquire_worker(self) -> Worker: ...

    def native(
        self,
        name: str,
        params: dict[str, JsonValue],
        *,
        custom: bool,
        kind: str,
        attempt_id: str,
        session_id: str | None,
        generation: str | int | None,
        project_hash: str | None,
    ) -> None: ...

    def observe(
        self,
        *,
        kind: str,
        attempt_id: str,
        session_id: str | None,
        generation: str | int | None,
        dispatched: bool,
        observed: bool,
        cause: str,
    ) -> None: ...

    async def native_async(self, name: str, params: dict[str, JsonValue], **values) -> None: ...

    async def observe_async(self, **values) -> None: ...

    def dispatch_started(self, **values) -> None: ...

    def mark_unknown(self) -> None: ...


execution_owner: ContextVar[ExecutionOwner | None] = ContextVar(
    "unity_execution_receipt_owner", default=None
)


def submit_owned_executor(loop, executor, function: Callable[..., T], *args) -> asyncio.Future[T]:
    """Acquire before submission; callers shield the future through actual completion.

    The existing callers already shield executor futures. Restore only the opaque
    receipt handle in a fresh worker Context. Opted-out execution retains its
    existing executor behavior.
    """
    owner = execution_owner.get()
    if owner is None:
        return loop.run_in_executor(executor, function, *args)
    pin = owner.acquire_worker()
    # Existing executor callers had no request Context. Restore only the opaque
    # ownership handle; never copy HTTP request/header or SDK session material.
    context = Context()
    context.run(execution_owner.set, owner)
    try:
        future = loop.run_in_executor(executor, context.run, function, *args)
    except BaseException:
        pin.close()
        raise
    future.add_done_callback(lambda _: pin.close())
    return future


async def owned_to_thread(function: Callable[..., T], /, *args, **kwargs) -> T:
    """Preserve to_thread context and retain ownership after waiter cancellation."""
    if execution_owner.get() is None:
        return await asyncio.to_thread(function, *args, **kwargs)
    future = submit_owned_executor(
        asyncio.get_running_loop(), None, lambda: function(*args, **kwargs)
    )
    return await asyncio.shield(future)


def own_future(future: asyncio.Future, pin: Worker) -> None:
    """Transfer a pre-scheduling pin to an independently shielded flight."""
    future.add_done_callback(lambda _: pin.close())


def observe_native_result(result: dict) -> None:
    """Keep CORE-01 uncertainty even when a public wrapper discards its error body."""
    owner = execution_owner.get()
    if owner is not None and isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and data.get("reason") == "outcome_unknown":
            owner.mark_unknown()
