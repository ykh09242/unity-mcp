"""Optional bounded receipts around complete ordinary public invocations.

Receipt history records observation, never an exactly-once transaction with Unity.
No SDK private dispatch or serialization kernel is used here.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable
from contextvars import Context
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

import anyio
from fastmcp import Context as FastMCPContext
from fastmcp.tools.base import ToolResult
from mcp_types import CallToolResult
from pydantic import BaseModel

from core.config import config
from models.tool_outcome import is_execution_failure
from models.response_limits import (
    MAX_RESPONSE_BYTES,
    MAX_RESPONSE_RETAINED_BYTES,
    ResponseOwner,
    bounded_json_text,
    response_owner,
    response_size,
)
from services.credential_lease import CredentialLease, require_credential_lease
from services.execution_receipt_context import execution_owner
from services.execution_receipt_identity import (
    MAX_ARGUMENT_BYTES,
    MAX_ARGUMENT_NODES,
    JsonValue,
    ReceiptInvalid,
    ReceiptNotFound,
    ReceiptOptions,
    ReceiptScope,
    ReceiptUnavailable,
    RequestIdentity,
    Surface,
    capture_identity,
)
from services.execution_receipts import ExecutionReceiptStore, ReceiptInvocation, ReceiptView
from services.operation_permissions import Permission, required_permissions
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE
from transport.local_project_context import full_hash

META_KEY = "io.github.ykh09242.unity-mcp/execution"
RESULT_BYTES = 1024 * 1024
RESULT_WORKING_BYTES = 32 * 1024 * 1024
METADATA_WORKING_BYTES = 256 * 1024
EXECUTION_SECONDS = 24 * 60 * 60 + 60  # Includes result/transport grace for a day-long operation.
T = TypeVar("T")


def execution_options(ctx) -> ReceiptOptions | None:
    """Read original public request metadata, not FastMCP's reconstructed message."""
    rc = ctx.request_context if ctx is not None else None
    meta = rc.meta if rc is not None else None
    if isinstance(meta, dict) and META_KEY in meta:
        return ReceiptOptions.parse(meta[META_KEY])
    return None


def has_execution_options(request_context) -> bool:
    meta = request_context.meta if request_context is not None else None
    return isinstance(meta, dict) and META_KEY in meta


def component_version(ctx) -> str | None:
    meta = ctx.request_context.meta if ctx.request_context is not None else None
    fastmcp = meta.get("fastmcp") if isinstance(meta, dict) else None
    value = fastmcp.get("version") if isinstance(fastmcp, dict) else None
    if value is not None and (type(value) is not str or not 1 <= len(value) <= 128):
        raise ReceiptInvalid("invalid_component_version")
    return value


def require_supported_sdk() -> None:
    if any(
        version(name) != expected
        for name, expected in (
            ("fastmcp", "4.1.1"),
            ("mcp", "2.3.0"),
            ("mcp_types", "2.3.0"),
        )
    ):
        raise ReceiptUnavailable("execution_receipt_sdk_unsupported")


def reject_continuation(ctx) -> None:
    """Reject unsupported MRTR legs before any ask, provider or handler work."""
    if ctx.request_state is not None or ctx.input_responses is not None:
        raise ReceiptUnavailable("execution_receipt_continuation_unsupported")


class _WorkingMemory:
    """Charge conservative graph/copy/encode space before allocating owned graphs."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.entries: dict[str, int] = {}

    def reserve(self, size: int, owner: ResponseOwner) -> None:
        with self.lock:
            if owner.released or sum(self.entries.values()) + size > MAX_RESPONSE_RETAINED_BYTES:
                raise ReceiptUnavailable("execution_receipt_working_capacity")
            key = uuid4().hex
            self.entries[key] = size
            owner.entries.append((self.entries, key))

            def copy_reservation(copy_owner: ResponseOwner) -> bool:
                try:
                    self.reserve(size, copy_owner)
                    return True
                except ReceiptUnavailable:
                    return False

            owner.copy_reservations.append(copy_reservation)


_working = _WorkingMemory()


@dataclass(frozen=True, slots=True)
class Authority:
    scope: ReceiptScope
    grants: frozenset[str]
    lease: CredentialLease | None

    async def check(self) -> None:
        if self.lease is not None:
            await self.lease.check()

    def check_now(self) -> None:
        if self.lease is not None:
            self.lease.check_now()


def _delivery_owner() -> ResponseOwner:
    owner = response_owner.get()
    if owner is None or owner.released:
        raise ReceiptUnavailable("execution_receipt_delivery_owner_unavailable")
    return owner


def _receipt_meta(view: ReceiptView, *, replayed: bool = False) -> dict:
    metadata = asdict(view)
    reason = metadata.pop("result_reason")
    metadata["result_unavailable_reason"] = None if view.result_available else reason
    return {"version": 1, **metadata, "replayed": replayed}


def _with_receipt(result: ToolResult, view: ReceiptView, *, replayed: bool = False) -> ToolResult:
    """Construct a fresh public typed result; cached SDK wire objects stay untouched."""
    native = result.to_mcp_result()
    if isinstance(native, CallToolResult):
        content, structured, meta, error = (
            native.content,
            native.structured_content,
            native.meta,
            native.is_error,
        )
    else:
        content, structured, meta, error = (
            result.content,
            result.structured_content,
            result.meta,
            result.is_error,
        )
    error = error or is_execution_failure(structured, meta)
    charge = response_size(
        {"content": content, "structuredContent": structured, "_meta": meta, "isError": error},
        max_bytes=MAX_RESPONSE_BYTES - 32768,
    )
    if charge is None:
        raise ReceiptUnavailable("execution_receipt_response_limit")
    _working.reserve(charge + METADATA_WORKING_BYTES, _delivery_owner())
    fresh = CallToolResult(
        content=content,
        structuredContent=structured,
        isError=error,
        _meta={**(meta or {}), META_KEY: _receipt_meta(view, replayed=replayed)},
    )
    return ToolResult.from_mcp_result(fresh)


def _public_projection(result: ToolResult) -> CallToolResult:
    native = result.to_mcp_result()
    wire = (
        native
        if isinstance(native, CallToolResult)
        else CallToolResult(
            content=result.content,
            structuredContent=result.structured_content,
            isError=result.is_error,
            _meta=result.meta,
        )
    )
    # These two namespaces belong to server/protocol machinery. Application
    # content and other application metadata are retained exactly, even if sensitive.
    return CallToolResult(
        content=wire.content,
        structuredContent=wire.structured_content,
        isError=wire.is_error or is_execution_failure(wire.structured_content, wire.meta),
        _meta={
            key: value
            for key, value in (wire.meta or {}).items()
            if key not in {META_KEY, "fastmcp"} and not key.startswith("io.modelcontextprotocol/")
        },
    )


def _outcome_unknown(value) -> bool:
    """Preserve CORE-01's explicit uncertainty instead of upgrading an error to success."""
    if not isinstance(value, dict):
        return False
    if set(value) == {"http_status", "body"} and isinstance(value["body"], dict):
        value = value["body"]
    data = value.get("data")
    return value.get("error") == "outcome_unknown" or (
        isinstance(data, dict)
        and (data.get("reason") == "outcome_unknown" or data.get("outcome") == "unknown")
    )


class _WorkerPin:
    def __init__(self, execution: PreparedExecution, store_pin) -> None:
        self.execution, self.store_pin = execution, store_pin
        self.closed = False

    def close(self) -> None:
        with self.execution.lock:
            if self.closed:
                return
            self.closed = True
            self.store_pin.close()
            self.execution.references -= 1
            self.execution._release_if_settled()


class PreparedExecution:
    """Own the immutable request graph through wrapper AND actual worker completion."""

    def __init__(
        self,
        runtime: ExecutionReceipts,
        authority: Authority,
        options: ReceiptOptions,
        identity: RequestIdentity,
        floor: frozenset[str],
        memory: ResponseOwner,
    ) -> None:
        self.runtime, self.authority, self.options = runtime, authority, options
        self.identity, self.floor, self.memory = identity, floor, memory
        self.invocation: ReceiptInvocation | None = None
        self.lock = threading.RLock()
        self.references = 1
        self.closed = False
        self.stopping = False
        self.target: tuple[str, str | int | None] | None = None
        self.expires_at = time.monotonic() + EXECUTION_SECONDS
        self.attempts: dict[tuple[str, str], dict] = {}
        self.native_unknown = False

    def close(self) -> None:
        with self.lock:
            if not self.closed:
                self.closed = True
                self.references -= 1
                self._release_if_settled()

    def _release_if_settled(self) -> None:
        if self.references == 0:
            self.memory.release()
            self.runtime.close_if_idle()

    def acquire_worker(self) -> _WorkerPin:
        with self.lock:
            if self.closed or self.stopping or self.invocation is None:
                raise ReceiptUnavailable("execution_receipt_invocation_closed")
            pin = self.invocation.acquire_worker()
            self.references += 1
            return _WorkerPin(self, pin)

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
    ) -> None:
        """Synchronous durable fence before canonical native I/O, including threads.

        Writes are bounded and no handler is admitted on an uncertain write. The
        existing native path must re-run its final lease fence after this I/O.
        """
        with self.lock:
            if self.closed or self.stopping or self.invocation is None:
                raise ReceiptUnavailable("execution_receipt_invocation_closed")
            self.authority.check_now()
            if (
                project_hash != self.options.project_hash
                or session_id is None
                or generation is None
            ):
                raise ReceiptUnavailable("execution_receipt_project_changed")
            target = session_id, generation
            if self.target is not None and target != self.target:
                raise ReceiptUnavailable("execution_receipt_generation_changed")
            self.target = target
            invocation = self.invocation
        # The short ownership lock never spans journal I/O. Wrapper cancellation
        # may mark stopping while this owned worker completes its durable fence.
        floor = frozenset(
            p.value for p in required_permissions(name, params, custom=custom, native=True)
        )
        invocation.extend_floor(floor, grants=self.authority.grants)
        invocation.observe_attempt(
            kind=kind,
            attempt_id=attempt_id,
            session_id=session_id,
            generation=generation,
            dispatched=False,
            observed=False,
            cause="dispatching",
        )
        self.authority.check_now()
        with self.lock:
            if self.closed or self.stopping:
                raise ReceiptUnavailable("execution_receipt_invocation_closed")

    def dispatch_started(self, **values) -> None:
        """Record the non-yielding native send boundary; finalization persists it.

        The durable intent precedes this observation. A crash before its later
        flush still recovers UNKNOWN; an absent flag never proves no execution.
        """
        with self.lock:
            if self.closed or self.stopping:
                raise ReceiptUnavailable("execution_receipt_invocation_closed")
            key = values["kind"], values["attempt_id"]
            self.attempts[key] = {
                **values,
                "dispatched": True,
                "observed": False,
                "cause": "dispatching",
            }
            while len(self.attempts) > 8:
                self.attempts.pop(next(iter(self.attempts)))

    def mark_unknown(self) -> None:
        with self.lock:
            if not self.closed and not self.stopping:
                self.native_unknown = True

    async def native_async(self, name: str, params: dict[str, JsonValue], **values) -> None:
        pin = self.acquire_worker()
        try:
            await self.runtime.io(lambda: self.native(name, params, **values))
            await anyio.lowlevel.checkpoint()
            await self.authority.check()
        finally:
            pin.close()

    def observe(self, **values) -> None:
        with self.lock:
            invocation = self.invocation if not self.closed and not self.stopping else None
            if invocation is not None:
                key = values["kind"], values["attempt_id"]
                self.attempts[key] = values
                while len(self.attempts) > 8:
                    self.attempts.pop(next(iter(self.attempts)))
        if invocation is not None:
            try:
                invocation.observe_attempt(**values, new_attempt=False)
            except ReceiptInvalid:
                # A late owning-socket result cannot rewrite a stopped wrapper.
                pass

    async def observe_async(self, **values) -> None:
        try:
            pin = self.acquire_worker()
        except (ReceiptUnavailable, ReceiptInvalid):
            return
        try:
            await self.runtime.io(lambda: self.observe(**values))
        finally:
            pin.close()

    async def execute(
        self,
        invoke: Callable[[], Awaitable[ToolResult]],
        *,
        before_handler: Callable[[], Awaitable[None]] | None = None,
    ) -> ToolResult:
        await self.authority.check()
        store = self.runtime.require_store()
        admitted = await self.runtime.io(
            lambda: store.admit(
                self.authority.scope,
                self.options,
                self.identity,
                floor=self.floor,
                grants=self.authority.grants,
            )
        )
        if isinstance(admitted, ReceiptView):
            # A concurrent identical call may have committed while selection awaited.
            return await self.runtime.replay(self.authority, admitted)
        self.invocation = admitted
        token = execution_owner.set(self)
        entered = False
        try:
            # Journal I/O must first adopt a possibly published intent. A local
            # authorization check may not yield, so explicitly deliver deferred
            # cancellation before ANY handler instruction can execute.
            await anyio.lowlevel.checkpoint()
            await self.authority.check()
            if before_handler is not None:
                await before_handler()
                await self.authority.check()
            admitted.enter_handler()
            entered = True
            with anyio.fail_after(EXECUTION_SECONDS):
                result = await invoke()
            await self.authority.check()
            if type(result) is not ToolResult:
                raise ReceiptUnavailable("execution_receipt_result_kind_unsupported")
            # Match the ordinary response guard before calling public serializers.
            wire = _public_projection(result)
            if response_size(wire, max_bytes=MAX_RESPONSE_BYTES - 32768) is None:
                raise ReceiptUnavailable("execution_receipt_response_limit")
            raw = None
            reason = None
            if self.options.retain_result:
                if (
                    response_size(wire, max_bytes=RESULT_BYTES, max_retained=RESULT_WORKING_BYTES)
                    is None
                ):
                    reason = "result_too_large"
                else:
                    memory = ResponseOwner()
                    _working.reserve(RESULT_WORKING_BYTES, memory)
                    try:
                        projection = BaseModel.model_dump(
                            wire,
                            by_alias=True,
                            mode="json",
                            exclude_none=True,
                            include={"content", "structured_content", "is_error", "meta"},
                        )
                        raw = json.dumps(
                            projection, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                        ).encode("utf-8")
                        if len(raw) > RESULT_BYTES:
                            raw, reason = None, "result_too_large"
                        view = await self._finish(
                            "unknown" if _outcome_unknown(wire.structured_content) else "completed",
                            raw,
                            reason,
                        )
                    finally:
                        memory.release()
                    await self.authority.check()
                    return _with_receipt(result, view)
            view = await self._finish(
                "unknown" if _outcome_unknown(wire.structured_content) else "completed", raw, reason
            )
            await self.authority.check()
            return _with_receipt(result, view)
        except BaseException:
            # Preserve SDK masking/exception conversion outside this middleware.
            # No invented formatted result is retained when the SDK has not observed it.
            if self.invocation is not None:
                try:
                    await self._finish("unknown" if entered else "not_dispatched", None, None)
                except (ReceiptInvalid, ReceiptUnavailable):
                    # A successful final commit can precede a later delivery/auth failure.
                    # Its immutable observed history is not rewritten or dispatched again.
                    pass
            raise
        finally:
            execution_owner.reset(token)

    async def _finish(self, status: str, raw: bytes | None, reason: str | None) -> ReceiptView:
        invocation = self.invocation
        with self.lock:
            if status == "completed" and self.native_unknown:
                status = "unknown"
        if status != "completed":
            raw, reason = None, None
        pin = None
        if status != "not_dispatched":
            try:
                pin = self.acquire_worker()
            except (ReceiptUnavailable, ReceiptInvalid):
                # A full native-pin budget cannot block or bypass finalization.
                # Runtime I/O owns cleanup even without an additional native pin.
                pass
        with self.lock:
            self.stopping = True
            attempts = tuple(self.attempts.values())

        def finalize() -> ReceiptView:
            try:
                for attempt in attempts:
                    invocation.observe_attempt(**attempt, new_attempt=False)
            except BaseException:
                try:
                    invocation.finish("unknown")
                except (ReceiptInvalid, ReceiptUnavailable):
                    pass
                raise
            return invocation.finish(status, result=raw, result_reason=reason)

        try:
            return await self.runtime.io(finalize)
        finally:
            if pin is not None:
                pin.close()


class ExecutionReceipts:
    """One process store; explicit requests never silently fall back to unprotected work."""

    def __init__(self, root: Path | None, *, private_root: bool = False) -> None:
        self.root, self.private_root = root, private_root
        self.store: ExecutionReceiptStore | None = None
        self.closed = False
        self.io_owners = 0

    async def start(self) -> None:
        if self.store is not None:
            raise ReceiptUnavailable("execution_receipt_store_already_open")
        self.closed = False
        if self.root is not None:
            require_supported_sdk()
            self.store = await self.io(
                lambda: ExecutionReceiptStore(self.root, private_root=self.private_root)
            )

    def require_store(self) -> ExecutionReceiptStore:
        if self.closed or self.store is None:
            raise ReceiptUnavailable("execution_receipts_unavailable")
        return self.store

    async def io(self, function: Callable[[], T]) -> T:
        """Adopt every I/O result before deferring cancellation; never orphan admission.

        This is the existing TestJobTasks shield/adoption pattern. Empty Context
        keeps request/credentials out of journal threads. The worker returns no
        result graph to an idle AnyIO thread: the holder belongs to this request.
        """
        holder: list[T] = []
        operation = [function]
        self.io_owners += 1

        def run() -> None:
            holder.append(operation.pop()())

        cancelled = False
        try:
            task = asyncio.create_task(anyio.to_thread.run_sync(run), context=Context())
            with anyio.CancelScope(shield=True):
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                task.result()
                return holder.pop()
        finally:
            holder.clear()
            operation.clear()
            self.io_owners -= 1
            self.close_if_idle()
            if cancelled:
                current = asyncio.current_task()
                if current is not None:
                    asyncio.get_running_loop().call_soon(current.cancel)

    def close_if_idle(self) -> None:
        if self.closed and self.io_owners == 0 and self.store is not None:
            if self.store.active_count == 0:
                self.store.close()
                self.store = None

    def close(self) -> None:
        self.closed = True
        self.close_if_idle()

    async def authority(self, project_hash: str, *, ctx=None, request=None) -> Authority:
        store = self.require_store()
        if config.http_remote_hosted:
            lease = await require_credential_lease()
            return Authority(
                ReceiptScope(lease.user_id, project_hash, "remote"),
                frozenset(p.value for p in lease.permissions.permissions),
                lease,
            )
        rc = ctx.request_context if ctx is not None else None
        actual_request = request if request is not None else rc.request if rc is not None else None
        if (
            actual_request is not None
            and actual_request.scope.get("state", {}).get(AUTHENTICATED_LOCAL_STATE) is not True
        ):
            raise ReceiptNotFound("receipt_not_found")
        return Authority(
            ReceiptScope(store.local_owner, project_hash, "local"),
            frozenset(p.value for p in Permission),
            None,
        )

    async def prepare(
        self,
        *,
        options: ReceiptOptions,
        surface: Surface,
        name: str,
        arguments,
        semantic_context: dict,
        policy_params: dict,
        custom: bool = False,
        ctx=None,
        request=None,
        version_selector: str | None = None,
    ) -> PreparedExecution | ToolResult:
        require_supported_sdk()
        store = self.require_store()
        authority = await self.authority(options.project_hash, ctx=ctx, request=request)
        _delivery_owner()
        await authority.check()
        floor = frozenset(p.value for p in required_permissions(name, policy_params, custom=custom))
        if not floor <= authority.grants:
            raise ReceiptNotFound("receipt_not_found")
        memory = ResponseOwner()
        try:
            charge = response_size(
                {"arguments": arguments, "context": semantic_context},
                max_bytes=MAX_ARGUMENT_BYTES,
                max_nodes=MAX_ARGUMENT_NODES,
            )
            if charge is None:
                raise ReceiptInvalid("receipt_request_graph_limit")
            _working.reserve(charge + METADATA_WORKING_BYTES, memory)
            identity = capture_identity(
                surface=surface,
                name=name,
                arguments=arguments,
                options=options,
                semantic_context=semantic_context,
                component_version=version_selector,
            )
            await authority.check()
            existing = await self.io(
                lambda: store.lookup(authority.scope, options, identity, grants=authority.grants)
            )
            await authority.check()
            if existing is not None:
                result = await self.replay(authority, existing)
                memory.release()
                return result
            return PreparedExecution(self, authority, options, identity, floor, memory)
        except BaseException:
            memory.release()
            raise

    async def replay(self, authority: Authority, view: ReceiptView) -> ToolResult:
        await authority.check()
        if view.result_available:
            owner = _delivery_owner()
            _working.reserve(RESULT_WORKING_BYTES, owner)
            raw = await self.io(
                lambda: self.require_store().read_result(
                    authority.scope,
                    store_id=view.store_id,
                    receipt_id=view.receipt_id,
                    grants=authority.grants,
                )
            )
            await authority.check()
            if raw is not None:
                text = bounded_json_text(
                    raw, max_bytes=RESULT_BYTES, max_depth=64, max_nodes=100000
                )
                if text is None:
                    raise ReceiptUnavailable("execution_receipt_result_invalid")
                typed = CallToolResult.model_validate(json.loads(text))
                if (
                    response_size(typed, max_bytes=RESULT_BYTES, max_retained=RESULT_WORKING_BYTES)
                    is None
                ):
                    raise ReceiptUnavailable("execution_receipt_result_invalid")
                # Result expiry may cross while the graph is decoded/authorized.
                view = await self.io(
                    lambda: self.require_store().get(
                        authority.scope,
                        store_id=view.store_id,
                        receipt_id=view.receipt_id,
                        grants=authority.grants,
                    )
                )
                await authority.check()
                if view.result_available:
                    return _with_receipt(ToolResult.from_mcp_result(typed), view, replayed=True)
            else:
                view = await self.io(
                    lambda: self.require_store().get(
                        authority.scope,
                        store_id=view.store_id,
                        receipt_id=view.receipt_id,
                        grants=authority.grants,
                    )
                )
        reason = {"pending": "receipt_pending", "unknown": "receipt_outcome_unknown"}.get(
            view.status, "receipt_result_unavailable"
        )
        error = {
            "success": False,
            "error": reason,
            "data": {
                "status": view.status,
                "reason": reason,
                "result_unavailable_reason": view.result_reason,
            },
        }
        await authority.check()
        return _with_receipt(
            ToolResult(content=error, structured_content=error, is_error=True), view, replayed=True
        )

    async def control(
        self,
        *,
        action: str,
        project_hash: str | None,
        store_id: str | None,
        receipt_id: str | None,
        idempotency_key: str | None,
        include_result: bool,
        ctx=None,
        request=None,
    ) -> ToolResult:
        if (
            type(action) is not str
            or action not in {"describe", "get"}
            or type(include_result) is not bool
        ):
            raise ReceiptInvalid("invalid_receipt_lookup")
        if action == "describe":
            if (
                any(
                    value is not None
                    for value in (project_hash, store_id, receipt_id, idempotency_key)
                )
                or include_result
            ):
                raise ReceiptInvalid("invalid_receipt_lookup")
            # Authentication is still required when the optional store is disabled.
            if config.http_remote_hosted:
                await require_credential_lease()
            elif request is not None or (
                ctx is not None
                and ctx.request_context is not None
                and ctx.request_context.request is not None
            ):
                actual = request if request is not None else ctx.request_context.request
                if actual.scope.get("state", {}).get(AUTHENTICATED_LOCAL_STATE) is not True:
                    raise ReceiptNotFound("receipt_not_found")
            data = {
                "version": 1,
                "available": self.store is not None and not self.closed and self.store.available,
                "store_id": self.store.store_id if self.store is not None else None,
                "record_ttl_seconds": 7200,
                "result_ttl_seconds": 600,
                "max_result_bytes": RESULT_BYTES,
                "max_records": 1024,
                "surfaces": ["mcp-tool", "/api/command", "/api/context-command"],
                "limits": {
                    "metadata_bytes": 8192,
                    "metadata_quota": 16 * 1024 * 1024,
                    "records_global_owner_project": [1024, 256, 128],
                    "active_global_owner_project": [256, 32, 16],
                    "result_global_owner_project_bytes": [
                        64 * RESULT_BYTES,
                        16 * RESULT_BYTES,
                        8 * RESULT_BYTES,
                    ],
                    "active_result_slots": 2,
                    "workers_per_invocation": 64,
                    "execution_seconds": EXECUTION_SECONDS,
                    "argument_bytes": MAX_ARGUMENT_BYTES,
                    "argument_nodes": MAX_ARGUMENT_NODES,
                    "argument_depth": 64,
                    "working_bytes": MAX_RESPONSE_RETAINED_BYTES,
                },
            }
            return ToolResult(structured_content=data)
        if action != "get" or project_hash is None or store_id is None:
            raise ReceiptInvalid("invalid_receipt_lookup")
        # Metadata-only history is also a bounded decode/copy/delivery operation.
        _working.reserve(METADATA_WORKING_BYTES, _delivery_owner())
        authority = await self.authority(project_hash, ctx=ctx, request=request)
        await authority.check()
        view = await self.io(
            lambda: self.require_store().get(
                authority.scope,
                store_id=store_id,
                receipt_id=receipt_id,
                idempotency_key=idempotency_key,
                grants=authority.grants,
            )
        )
        await authority.check()
        data = _receipt_meta(view)
        if include_result:
            data["result"] = None
            if view.result_available:
                retained = await self.replay(authority, view)
                fresh = (retained.meta or {})[META_KEY]
                data = {**fresh, "result": None}
                if fresh["result_available"]:
                    _working.reserve(RESULT_WORKING_BYTES, _delivery_owner())
                    data["result"] = BaseModel.model_dump(
                        _public_projection(retained),
                        by_alias=True,
                        mode="json",
                        exclude_none=True,
                        include={"content", "structured_content", "is_error", "meta"},
                    )
        await authority.check()
        return ToolResult(content=[], structured_content=data)


def install_execution_receipts(server, root: Path | None, *, private_root: bool = False):
    runtime = ExecutionReceipts(root, private_root=private_root)
    server._execution_receipts = runtime

    async def get_execution_receipt(
        ctx: FastMCPContext,
        action: str = "describe",
        store_id: str | None = None,
        project_hash: str | None = None,
        receipt_id: str | None = None,
        idempotency_key: str | None = None,
        include_result: bool = False,
    ) -> ToolResult:
        """Describe optional execution receipts or retrieve one authorized historical receipt.

        Retained application results may contain sensitive content. UNKNOWN is not
        proof that execution failed and must never trigger an automatic resend.
        """
        if execution_options(ctx) is not None:
            raise ReceiptInvalid("receipt_control_cannot_create_receipt")
        return await runtime.control(
            action=action,
            store_id=store_id,
            project_hash=project_hash,
            receipt_id=receipt_id,
            idempotency_key=idempotency_key,
            include_result=include_result,
            ctx=ctx,
        )

    server.tool(name="get_execution_receipt")(get_execution_receipt)
    return runtime


async def execute_receipted_rest(runtime, request, raw_body: dict, route: Surface, forward):
    """Wrap the existing authenticated local route, preserving its public body/status.

    Receipt result retrieval exposes the explicit {http_status, body} result envelope;
    ordinary REST delivery unwraps that envelope and appends fresh receipt metadata.
    The stored expected root is the validated request value, never a live rebind.
    """
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from transport.local_project_context import ContextCommand, ReceiptCommand

    model = ContextCommand if route == "/api/context-command" else ReceiptCommand
    body = model.model_validate(raw_body)
    if body.type == "get_execution_receipt":
        raise ReceiptInvalid("receipt_control_cannot_create_receipt")
    options = ReceiptOptions.parse(raw_body["execution"])
    if full_hash(body.unity_instance) != options.project_hash:
        raise ReceiptInvalid("execution_receipt_project_mismatch")
    envelope = body.model_dump(mode="json", exclude={"execution"}, exclude_unset=True)
    prepared = await runtime.prepare(
        options=options,
        surface=route,
        name=body.type,
        arguments=body.params,
        semantic_context=envelope,
        policy_params=body.params,
        custom=body.type == "execute_custom_tool",
        request=request,
    )
    if isinstance(prepared, ToolResult):
        result = prepared
    else:
        # Forward only the validated immutable contract through the existing route.
        # The recursive request has no execution option, so no receipt can be bypassed
        # or admitted twice. Existing budgets/project fences/custom handling stay intact.
        async def invoke() -> ToolResult:
            payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}

            forwarded = Request(request.scope, receive=receive)
            response = await forward(forwarded)
            value = json.loads(response.body)
            return ToolResult(
                content=[],
                structured_content={
                    "http_status": response.status_code,
                    "body": value,
                },
                is_error=response.status_code >= 400 or value.get("success") is False,
            )

        try:
            result = await prepared.execute(invoke)
        finally:
            prepared.close()
    structured = result.structured_content or {}
    if set(structured) == {"http_status", "body"}:
        status, value = structured["http_status"], structured["body"]
    else:
        status, value = 200, structured
    receipt = (result.meta or {}).get(META_KEY)
    if isinstance(value, dict) and receipt is not None:
        value = {**value, "execution_receipt": receipt}
    return JSONResponse(value, status_code=status)


async def rest_receipt_control(runtime, request, body: dict):
    """Use the same stored-record authorization without selecting a live Unity target."""
    from starlette.responses import JSONResponse

    if body.keys() - {"type", "params", "unity_instance", "expected_project"}:
        raise ReceiptInvalid("invalid_receipt_lookup")
    params = body.get("params", {})
    allowed = {
        "action",
        "store_id",
        "project_hash",
        "receipt_id",
        "idempotency_key",
        "include_result",
    }
    if type(params) is not dict or params.keys() - allowed:
        raise ReceiptInvalid("invalid_receipt_lookup")
    result = await runtime.control(
        action=params.get("action", "describe"),
        project_hash=params.get("project_hash"),
        store_id=params.get("store_id"),
        receipt_id=params.get("receipt_id"),
        idempotency_key=params.get("idempotency_key"),
        include_result=params.get("include_result", False),
        request=request,
    )
    native = result.to_mcp_result()
    wire = (
        native
        if isinstance(native, CallToolResult)
        else CallToolResult(
            content=result.content,
            structuredContent=result.structured_content,
            isError=result.is_error,
            _meta=result.meta,
        )
    )
    if response_size(wire, max_bytes=MAX_RESPONSE_BYTES - 32768) is None:
        raise ReceiptUnavailable("execution_receipt_response_limit")
    return JSONResponse(BaseModel.model_dump(wire, by_alias=True, mode="json", exclude_none=True))
