"""Whole public invocation admission before refresh-capable preflight."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import wraps
import inspect
import logging
from pathlib import Path
from typing import ParamSpec, TypeVar

from pydantic_core import to_jsonable_python

from core.config import config
from models.response_limits import response_size
from services.execution_receipt_context import owned_to_thread
from services.operation_permissions import JsonValue, authorize_remote_operation
from services.operation_policy import OperationMode, resolve_operation_policy
from services.registry.operation_context import active_operation, current_operation, scheduler
from services.registry.operation_scheduler import (
    OperationAdmission,
    OperationCapacityError,
    OperationLease,
    OperationScopeChanged,
)
from services.registry.operation_target import OperationTarget, capture_target

P = ParamSpec("P")
T = TypeVar("T")
_LOGGER = logging.getLogger("mcp-for-unity-server")


class OperationPolicyViolation(RuntimeError):
    def __init__(self) -> None:
        super().__init__("operation_policy_escalation")


@asynccontextmanager
async def operation_session(request: OperationAdmission) -> AsyncIterator[OperationLease]:
    owner = active_operation()
    if owner is not None:
        if owner.request.scope.key != request.scope.key:
            raise OperationScopeChanged()
        if not request.is_current():
            raise OperationScopeChanged()
        if (
            owner.request.scope.generation != request.scope.generation
            or not owner.request.is_current()
        ) and not request.observational_continuation:
            raise OperationScopeChanged()
        if (
            owner.request.mode != "exclusive"
            and request.mode != "control"
            and request.mode != owner.request.mode
        ):
            raise OperationPolicyViolation()
        yield owner
        return
    async with scheduler().session(request) as owner:
        token = current_operation.set(owner)
        try:
            yield owner
        finally:
            current_operation.reset(token)


def admission_for(
    target: OperationTarget,
    mode: OperationMode,
    *,
    expires_at: float | None,
    retained_bytes: int = 0,
    observational_continuation: bool = False,
) -> OperationAdmission:
    # Old native packages retain conservative public sequencing. A control
    # request can still queue at their existing transport predecessor boundary.
    effective = (
        "exclusive" if mode == OperationMode.SHARED_READ and not target.negotiated else mode.value
    )
    _LOGGER.debug(
        "Unity operation policy mode=%s native_arbitration=%s sequencing=%s",
        effective,
        target.negotiated,
        "fair_compatible" if target.negotiated else "conservative",
    )
    return OperationAdmission(
        target.scope,
        effective,
        target.is_current,
        expires_at,
        retained_bytes,
        observational_continuation,
    )


def is_observational_continuation(name: str, params: dict[str, JsonValue]) -> bool:
    """Only these native status reads may cross an owned wrapper's reload fence."""
    if name in {"ping", "get_editor_state"}:
        return not params
    key = {"get_compile_job": "compile_id", "get_test_job": "job_id"}.get(name)
    value = params.get(key) if key is not None else None
    return (
        key is not None
        and isinstance(value, str)
        and 0 < len(value) <= 128
        and set(params) == {key}
    )


def trusted_tool(function: Callable, name: str) -> bool:
    """A registered Python callback cannot gain read eligibility by its name."""
    code = getattr(function, "__code__", None)
    if code is None:
        return False
    source = Path(code.co_filename).resolve()
    services = Path(__file__).resolve().parent.parent
    if function.__module__.startswith("services.tools."):
        return source.is_relative_to(services / "tools")
    if function.__module__ == "services.registry.capability_adapter":
        from services.registry.capability_catalog import CAPABILITIES

        return source == services / "registry" / "capability_adapter.py" and any(
            capability.name == name for capability in CAPABILITIES
        )
    return False


def _deadline(name: str, params: dict[str, JsonValue]) -> float | None:
    from transport.command_budget import http_request_budget
    from transport.legacy.unity_connection import _command_deadline

    request = http_request_budget.get()
    if request is not None:
        from transport.plugin_hub import PluginHub

        request.extend(PluginHub._command_timeouts(name, params)[1])
        return request.budget.expires_at
    return _command_deadline(name, params)


def _refusal(reason: str) -> dict[str, JsonValue]:
    return {
        "success": False,
        "error": reason,
        "hint": "select_instance" if reason == "operation_target_changed" else "retry",
        "data": {"reason": reason, "dispatch": "not_dispatched"},
    }


def wrap_tool(function: Callable[P, T], name: str, unity_target: str | None) -> Callable:
    """Wrap once at registration; direct native senders retain their own admission."""
    signature = inspect.signature(function)
    trusted = trusted_tool(function, name)

    @wraps(function)
    async def invoke(*args: P.args, **kwargs: P.kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        ctx = bound.arguments.get("ctx")
        # Validated API inputs are JSON-compatible models/scalars. Context is
        # never put in policy input, queue accounting, diagnostics or workers.
        params = to_jsonable_python(
            {key: value for key, value in bound.arguments.items() if key != "ctx"}
        )
        await authorize_remote_operation(name, params, ctx=ctx)

        async def validate_enabled() -> None:
            server = getattr(ctx, "fastmcp", None)
            lookup = getattr(server, "get_tool", None)
            if lookup is not None and await lookup(name) is None:
                raise OperationScopeChanged()

        async def call():
            if inspect.iscoroutinefunction(function):
                return await function(*args, **kwargs)
            return await owned_to_thread(function, *args, **kwargs)

        await validate_enabled()
        if unity_target is None or ctx is None:
            return await call()
        from services.tools import get_unity_instance_from_context

        instance = await get_unity_instance_from_context(ctx)
        user = await ctx.get_state("user_id") if config.http_remote_hosted else None
        target = await capture_target(instance, user_id=user)
        if target is None:
            # Unselected/unavailable instances keep their existing selection
            # refusal path. Native admission still applies if a target is resolved.
            return await call()
        charge = response_size(
            params,
            max_bytes=32 * 1024 * 1024,
            max_depth=64,
            max_nodes=1_000_000,
            max_retained=256 * 1024 * 1024,
        )
        if charge is None:
            return _refusal("operation_payload_limit")
        policy = resolve_operation_policy(name, params, trusted_builtin=trusted)
        request = admission_for(
            target, policy.mode, expires_at=_deadline(name, params), retained_bytes=charge
        )
        try:
            async with operation_session(request):
                await authorize_remote_operation(name, params, ctx=ctx)
                await validate_enabled()
                if not target.is_current():
                    raise OperationScopeChanged()
                return await call()
        except OperationScopeChanged:
            return _refusal("operation_target_changed")
        except OperationCapacityError:
            return _refusal("operation_capacity")

    return invoke
