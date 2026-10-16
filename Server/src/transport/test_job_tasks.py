"""Opt-in Tasks for one captured bounded wait, independent of native execution.

Capture/execution/owner callbacks are supplied by the ordinary wait
adapter. No callback may retain an HTTP Context or start a native test run.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import Context
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING, Literal

import anyio
from fastmcp import Context as FastMCPContext
from fastmcp.server.extensions import MethodBinding, ServerExtension
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.inbound import decode_header_value
from mcp_types.jsonrpc import HEADER_MISMATCH, MISSING_REQUIRED_CLIENT_CAPABILITY
from mcp_types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    CallToolRequestParams,
    InputResponses,
    Request,
    RequestParams,
    Result,
)
from pydantic import Field
from starlette.types import ASGIApp, Receive, Scope, Send

from services.test_wait_journal import (
    JournalCapacity,
    JournalUnavailable,
    TaskNotFound,
    WaitJournal,
    WaitOperation,
    WaitRecord,
)
from models.response_limits import ResponseOwner, response_owner, response_size

if TYPE_CHECKING:
    from transport.plugin_hub import HttpBudgetSnapshot

TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
_MODERN = frozenset({"2026-07-28"})
_DELIVERY_OWNER = "unity_mcp_wait_task_delivery"


class WaitDeliveryAdmission:
    """One bounded journal decode/SDK serialization/delivery at a time.

    Reads may contain a full ordinary 32 MiB result. Reserving the whole existing
    response graph budget for each reader avoids many small probes amplifying it.
    The slot belongs to actual ASGI exit, including slow send and teardown.
    """

    def __init__(self):
        self._lock = Lock()
        self.active = False

    def acquire(self) -> None:
        with self._lock:
            if self.active:
                raise MCPError(INTERNAL_ERROR, "Bounded wait result delivery capacity reached")
            self.active = True

    def release(self) -> None:
        with self._lock:
            self.active = False


_delivery_admission = WaitDeliveryAdmission()


@dataclass(slots=True)
class _DeliveryOwner:
    admission: WaitDeliveryAdmission | None = None


class WaitTaskDeliveryMiddleware:
    """Hold Task-method journal delivery admission until actual ASGI exit."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        owner = _DeliveryOwner()
        owned = {**scope, "state": {**scope.get("state", {}), _DELIVERY_OWNER: owner}}
        try:
            await self.app(owned, receive, send)
        finally:
            if owner.admission is not None:
                owner.admission.release()


class CreateWaitTaskResult(Result):
    """The released flat Tasks extension result, not the SDK's legacy alias."""

    result_type: Literal["task"] = Field(default="task", alias="resultType")
    task_id: str = Field(alias="taskId", max_length=32)
    status: Literal["working", "completed", "failed", "cancelled"]
    created_at: str = Field(alias="createdAt", max_length=40)
    last_updated_at: str = Field(alias="lastUpdatedAt", max_length=40)
    ttl_ms: int = Field(alias="ttlMs", ge=1, le=3_600_000)
    poll_interval_ms: int = Field(default=1000, alias="pollIntervalMs")


class GetWaitTaskResult(CreateWaitTaskResult):
    """Released detailed task response; clients retain its nested ordinary result."""

    result_type: Literal["complete"] = Field(default="complete", alias="resultType")
    result: dict | None = None
    error: dict | None = None


class TaskParams(RequestParams):
    task_id: str = Field(alias="taskId", min_length=1, max_length=1024)


class UpdateTaskParams(TaskParams):
    input_responses: InputResponses = Field(alias="inputResponses")


class GetWaitTaskRequest(Request[TaskParams, Literal["tasks/get"]]):
    name_param = "taskId"
    method: Literal["tasks/get"] = "tasks/get"


class CancelWaitTaskRequest(Request[TaskParams, Literal["tasks/cancel"]]):
    name_param = "taskId"
    method: Literal["tasks/cancel"] = "tasks/cancel"


class UpdateWaitTaskRequest(Request[UpdateTaskParams, Literal["tasks/update"]]):
    name_param = "taskId"
    method: Literal["tasks/update"] = "tasks/update"


@dataclass(frozen=True, slots=True)
class CapturedWait:
    """Original monotonic guards and durable immutable scope, captured once."""

    operation: WaitOperation
    wait_deadline: float
    outer_deadline: float
    outer_timeout_result: dict
    budget_snapshot: HttpBudgetSnapshot | None = None

    def __post_init__(self):
        if not math.isfinite(self.wait_deadline) or not math.isfinite(self.outer_deadline):
            raise ValueError("Original wait deadlines must be finite")
        if response_size(self.outer_timeout_result, max_bytes=32_768) is None:
            raise ValueError("Ordinary timeout evidence exceeds supported bounds")
        # Capture values rather than the request's mutable execution-evidence
        # graph. The ordinary runner binding remains a separate release gate.
        object.__setattr__(
            self, "outer_timeout_result", json.loads(json.dumps(self.outer_timeout_result))
        )


class WaitAdmission:
    """Actual worker lifetimes, including retired noncooperative workers."""

    def __init__(self, total=4, principal=2, project=2):
        self.total = total
        self.principal = principal
        self.project = project
        self.active: dict[object, tuple[str, str]] = {}
        self.principals: Counter[str] = Counter()
        self.projects: Counter[tuple[str, str]] = Counter()
        self._lock = Lock()

    def acquire(self, operation: WaitOperation) -> object:
        with self._lock:
            key = (operation.owner, operation.project)
            if (
                len(self.active) >= self.total
                or self.principals[operation.owner] >= self.principal
                or self.projects[key] >= self.project
            ):
                raise JournalCapacity("Bounded wait worker capacity reached")
            token = object()
            self.active[token] = key
            self.principals[operation.owner] += 1
            self.projects[key] += 1
            return token

    def release(self, token: object) -> None:
        with self._lock:
            owner, project = self.active.pop(token)
            self.principals[owner] -= 1
            self.projects[(owner, project)] -= 1
            if not self.principals[owner]:
                del self.principals[owner]
            if not self.projects[(owner, project)]:
                del self.projects[(owner, project)]


_worker_admission = WaitAdmission()


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def task_handle(record: WaitRecord) -> CreateWaitTaskResult:
    return CreateWaitTaskResult(
        taskId=record.task_id,
        status=record.status,
        createdAt=_timestamp(record.created_at),
        lastUpdatedAt=_timestamp(record.updated_at),
        ttlMs=round((record.expires_at - record.created_at) * 1000),
    )


class TestJobTasks(ServerExtension):
    """Public extension with an explicitly supplied authenticated wait binding."""

    __test__ = False
    identifier = TASKS_EXTENSION

    def __init__(
        self,
        journal: WaitJournal,
        *,
        capture: Callable[[CallToolRequestParams, FastMCPContext], Awaitable[CapturedWait | None]],
        execute: Callable[[CapturedWait], Awaitable[dict]],
        owner: Callable[[ServerRequestContext], Awaitable[str]],
        admission: WaitAdmission | None = None,
        fallback: Callable | None = None,
        executor_owns_deadline: bool = False,
    ):
        self.journal = journal
        self.capture = capture
        self.execute = execute
        self.owner = owner
        self.fallback = fallback
        self.executor_owns_deadline = executor_owns_deadline
        self.admission = admission if admission is not None else _worker_admission
        self.delivery_admission = _delivery_admission
        self._workers: dict[str, asyncio.Task] = {}
        self._scopes: dict[str, anyio.CancelScope] = {}
        self._preparing: set[object] = set()
        self._io_owners = 0
        self._method_owners = 0
        self._cancelled: set[str] = set()
        self._closed = True

    def methods(self):
        return (
            MethodBinding("tasks/get", TaskParams, self.get_task, protocol_versions=_MODERN),
            MethodBinding(
                "tasks/update", UpdateTaskParams, self.update_task, protocol_versions=_MODERN
            ),
            MethodBinding("tasks/cancel", TaskParams, self.cancel_task, protocol_versions=_MODERN),
        )

    @asynccontextmanager
    async def lifespan(self):
        self._closed = False
        try:
            yield
        finally:
            await self.close()

    async def close(self, *, grace_seconds: float = 5) -> None:
        """Graceful drain; noncooperative work keeps its slot and OS writer lock."""
        if not 0 <= grace_seconds <= 5:
            raise ValueError("Unsupported bounded wait shutdown grace")
        self._closed = True
        for scope in tuple(self._scopes.values()):
            scope.cancel()
        if self._workers:
            await asyncio.wait(tuple(self._workers.values()), timeout=grace_seconds)
        self._close_if_idle()
        # The last actual worker exit closes a retired journal below.

    def _close_if_idle(self) -> None:
        # Task methods also own journal threads. Counting only native-wait
        # workers/creation would synchronously wait on their RLock from the
        # event loop during shutdown or final-worker cleanup.
        if (
            self._closed
            and not self._workers
            and not self._preparing
            and not self._io_owners
            and not self._method_owners
        ):
            self.journal.close()

    @asynccontextmanager
    async def _method_lifetime(self):
        # Preserve the writer between lookup and a subsequent cancel commit too,
        # rather than closing it at the intervening zero-I/O checkpoint.
        self._method_owners += 1
        try:
            yield
        finally:
            self._method_owners -= 1
            self._close_if_idle()

    async def _io(self, function, *args, **kwargs):
        # AnyIO shielding also covers normal SDK request cancellation. Raw
        # asyncio deadlines may cancel the awaiting Task directly, so defer that
        # cancellation until the actual thread exits and its result is adopted.
        self._io_owners += 1
        io = asyncio.create_task(
            anyio.to_thread.run_sync(lambda: function(*args, **kwargs)), context=Context()
        )
        cancelled = False
        with anyio.CancelScope(shield=True):
            try:
                while not io.done():
                    try:
                        await asyncio.shield(io)
                    except asyncio.CancelledError:
                        cancelled = True
                return io.result()
            finally:
                self._io_owners -= 1
                self._close_if_idle()
                if cancelled:
                    task = asyncio.current_task()
                    asyncio.get_running_loop().call_soon(task.cancel)

    async def intercept_tool_call(self, params, context, call_next):
        request_context = context.request_context
        # FastMCP exposes a compatibility wrapper; extension method handlers use
        # this same SDK request context directly. Never retain either in workers.
        ctx = request_context._srctx if request_context is not None else None
        args = params.arguments or {}
        eligible = (
            not self._closed
            and ctx is not None
            and ctx.request is not None
            and ctx.protocol_version in _MODERN
            and self.client_settings(ctx) is not None
            and params.name == "get_test_job"
            and type(args.get("wait_timeout")) is int
            and 0 < args["wait_timeout"] <= 60
            and not any(args.get(key) for key in ("include_details", "include_failed_tests"))
            and args.get("page_size") is None
            and args.get("cursor") is None
        )
        if not eligible:
            return await call_next()
        principal = await self.owner(ctx)
        if not isinstance(principal, str) or not principal:
            raise MCPError(INVALID_PARAMS, "Authenticated wait owner is required")
        captured = await self.capture(params, context)
        if captured is None:
            return await call_next()
        if captured.operation.owner != principal:
            raise MCPError(INTERNAL_ERROR, "Bounded wait capture owner mismatch")
        admission = None
        try:
            admission = self.admission.acquire(captured.operation)
            self._preparing.add(admission)
            try:
                record = await self._io(self.journal.create, captured.operation)
            finally:
                self._preparing.discard(admission)
        except JournalCapacity:
            if admission is not None:
                self.admission.release(admission)
            if self._closed:
                self._close_if_idle()
                raise MCPError(INTERNAL_ERROR, "Bounded wait Tasks are closed") from None
            if self.fallback is not None:
                return await self.fallback(captured, call_next)
            return await call_next()
        except BaseException:
            if admission is not None:
                self.admission.release(admission)
            self._close_if_idle()
            raise
        if self._closed:
            try:
                await self._io(
                    self.journal.finish,
                    record.task_id,
                    "failed",
                    error={
                        "code": INTERNAL_ERROR,
                        "message": "Bounded wait interrupted during server shutdown",
                    },
                )
            finally:
                self.admission.release(admission)
                self._close_if_idle()
            raise MCPError(INTERNAL_ERROR, "Bounded wait Tasks are closed")
        # An empty Context prevents request/budget/credentials from leaking into
        # detached work. SDK lifespan scopes cannot span a custom AnyIO group.
        self._scopes[record.task_id] = anyio.CancelScope()
        worker = asyncio.create_task(
            self._run(record.task_id, captured, admission), context=Context()
        )
        self._workers[record.task_id] = worker
        return task_handle(record)

    async def _run(self, task_id: str, captured: CapturedWait, admission: object):
        result_owner = ResponseOwner()
        owner_token = response_owner.set(result_owner)
        try:
            # The original wait runner owns the inner deadline and ordinary last
            # observation semantics. This hard outer guard never refreshes time.
            result = None
            with self._scopes[task_id]:
                if self.executor_owns_deadline:
                    # The production binding owns the SAME guard plus live
                    # command evidence and ordinary SDK timeout conversion.
                    result = await self.execute(captured)
                elif asyncio.get_running_loop().time() >= captured.outer_deadline:
                    result = captured.outer_timeout_result
                else:
                    guard = asyncio.timeout_at(captured.outer_deadline)
                    try:
                        async with guard:
                            result = await self.execute(captured)
                    except TimeoutError:
                        if not guard.expired():
                            raise
                        result = captured.outer_timeout_result
            if result is not None:
                await self._io(self.journal.finish, task_id, "completed", result=result)
            elif task_id in self._cancelled:
                await self._io(self.journal.finish, task_id, "cancelled")
            else:
                await self._io(
                    self.journal.finish,
                    task_id,
                    "failed",
                    error={
                        "code": INTERNAL_ERROR,
                        "message": "Bounded wait interrupted by server shutdown; native job state is unchanged",
                    },
                )
        except asyncio.CancelledError:
            if task_id in self._cancelled:
                await self._io(self.journal.finish, task_id, "cancelled")
            else:
                await self._io(
                    self.journal.finish,
                    task_id,
                    "failed",
                    error={
                        "code": INTERNAL_ERROR,
                        "message": "Bounded wait interrupted by server shutdown; native job state is unchanged",
                    },
                )
        except MCPError as error:
            await self._io(
                self.journal.finish,
                task_id,
                "failed",
                error=error.error.model_dump(by_alias=True, exclude_none=True),
            )
        except Exception:
            await self._io(
                self.journal.finish,
                task_id,
                "failed",
                error={"code": INTERNAL_ERROR, "message": "Bounded wait execution failed"},
            )
        finally:
            result_owner.release()
            response_owner.reset(owner_token)
            self._workers.pop(task_id, None)
            self._scopes.pop(task_id, None)
            self._cancelled.discard(task_id)
            self.admission.release(admission)
            self._close_if_idle()

    async def _authorize(self, ctx, params) -> str:
        if self._closed:
            raise MCPError(INTERNAL_ERROR, "Bounded wait Tasks are closed")
        if self.client_settings(ctx) is None:
            raise MCPError(
                MISSING_REQUIRED_CLIENT_CAPABILITY, "Tasks extension capability is required"
            )
        principal = await self.owner(ctx)
        if not isinstance(principal, str) or not principal:
            raise MCPError(INVALID_PARAMS, "Authenticated wait owner is required")
        if ctx.request is not None:
            value = ctx.request.headers.get("mcp-name")
            try:
                if value is None or decode_header_value(value) != params.task_id:
                    raise ValueError("Task routing name mismatch")
            except ValueError as error:
                raise MCPError(HEADER_MISMATCH, "Task routing name mismatch") from error
            delivery = ctx.request.scope.get("state", {}).get(_DELIVERY_OWNER)
            if not isinstance(delivery, _DeliveryOwner):
                raise MCPError(INTERNAL_ERROR, "Bounded wait delivery lifetime is unavailable")
            if delivery.admission is None:
                self.delivery_admission.acquire()
                delivery.admission = self.delivery_admission
        else:
            raise MCPError(INVALID_PARAMS, "Authenticated HTTP is required for wait Tasks")
        return principal

    async def _lookup(self, task_id: str, principal: str) -> WaitRecord:
        try:
            return await self._io(self.journal.get, task_id, principal)
        except TaskNotFound as error:
            raise MCPError(INVALID_PARAMS, "Task not found") from error
        except JournalUnavailable as error:
            raise MCPError(INTERNAL_ERROR, "Bounded wait journal unavailable") from error

    async def get_task(self, ctx, params):
        async with self._method_lifetime():
            principal = await self._authorize(ctx, params)
            record = await self._lookup(params.task_id, principal)
            result = task_handle(record).model_dump(by_alias=True, exclude_none=True)
            result["resultType"] = "complete"
            if record.result is not None:
                result["result"] = record.result
            if record.error is not None:
                result["error"] = record.error
            return result

    async def update_task(self, ctx, params):
        async with self._method_lifetime():
            principal = await self._authorize(ctx, params)
            await self._lookup(params.task_id, principal)
            return {"resultType": "complete"}

    async def cancel_task(self, ctx, params):
        async with self._method_lifetime():
            principal = await self._authorize(ctx, params)
            await self._lookup(params.task_id, principal)
            await self._io(self.journal.request_cancel, params.task_id, principal)
            worker = self._workers.get(params.task_id)
            if worker is not None and params.task_id not in self._cancelled:
                self._cancelled.add(params.task_id)
                self._scopes[params.task_id].cancel()
            return {"resultType": "complete"}
