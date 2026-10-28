"""Whole public invocation admission before refresh-capable preflight."""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from functools import wraps
import asyncio  # noqa: ANYIO_OK -- share the existing public request budget.
import inspect
import importlib
import logging
import time
from pathlib import Path
from typing import ParamSpec, TypeVar, get_args

from pydantic import MISSING
from pydantic_core import to_jsonable_python
from pydantic.fields import FieldInfo

from core.config import config
from models.response_limits import response_size
from models.models import MCPResponse
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
_console_read_retry: ContextVar[tuple[str, dict[str, JsonValue]] | None] = ContextVar(
    "console_read_retry", default=None
)


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


@contextmanager
def console_read_continuation(name: str, params: dict[str, JsonValue]) -> Iterator[None]:
    """Permit only the exact read retried by console readiness recovery."""
    allowed = (name == "read_console" and params.get("action") == "get") or (
        name == "tail_console" and "action" not in params
    )
    token = _console_read_retry.set((name, to_jsonable_python(params)) if allowed else None)
    try:
        yield
    finally:
        _console_read_retry.reset(token)


def is_observational_continuation(
    name: str, params: dict[str, JsonValue], *, public: bool = False
) -> bool:
    """Only reviewed native observations may cross an owned wrapper's reload fence."""
    if not public and _console_read_retry.get() == (name, params):
        return True
    if name in {"ping", "get_editor_state"}:
        return not params
    key = {"get_compile_job": "compile_id", "get_test_job": "job_id"}.get(name)
    value = params.get(key) if key is not None else None
    if not (key is not None and isinstance(value, str) and 0 < len(value) <= 128):
        return False
    if name == "get_compile_job":
        return set(params) == {key}
    booleans = (
        {"include_failed_tests", "include_details"}
        if public
        else {"includeFailedTests", "includeDetails"}
    )
    allowed = {key, *booleans, "page_size", "cursor"}
    if public:
        allowed.add("wait_timeout")
    return (
        set(params) <= allowed
        and all(type(params[field]) is bool for field in booleans & params.keys())
        and (
            params.get("page_size") is None
            or type(params["page_size"]) is int
            and 1 <= params["page_size"] <= 100
        )
        and (
            params.get("cursor") is None
            or isinstance(params["cursor"], str)
            and 0 < len(params["cursor"]) <= 4096
        )
        and (
            params.get("wait_timeout") is None
            or type(params["wait_timeout"]) is int
            and params["wait_timeout"] >= 0
        )
    )


def trusted_tool(function: Callable, name: str) -> bool:
    """A registered Python callback cannot gain read eligibility by its name."""
    code = getattr(function, "__code__", None)
    if code is None:
        return False
    source = Path(code.co_filename).resolve()
    services = Path(__file__).resolve().parent.parent
    if function.__module__.startswith("services.tools."):
        if (
            not source.is_relative_to(services / "tools")
            or function.__name__ != name
            or code.co_name != name
        ):
            return False
        try:
            module = importlib.import_module(function.__module__)
        except ImportError:
            return False
        return getattr(module, name, None) is function
    if function.__module__ == "services.registry.capability_adapter":
        from services.registry.capability_catalog import CAPABILITIES
        from services.registry.capability_adapter import get_capability_implementation

        feature = next((item for item in CAPABILITIES if item.name == name), None)
        implementation = get_capability_implementation(function, name)
        return (
            source == services / "registry" / "capability_adapter.py"
            and feature is not None
            and implementation is not None
            and trusted_tool(implementation, feature.source)
        )
    return False


def operation_payload_charge(params: dict[str, JsonValue]) -> int | None:
    """Account retained memory without imposing HTTP's wire limit on other routes."""
    from transport.legacy.unity_connection import FRAMED_MAX

    retention_limit = 1024 * 1024 * 1024
    return response_size(
        params,
        # Whole SDK arguments need not be one native command. Existing ingress
        # and transport writers remain authoritative for their own wire limits.
        max_bytes=FRAMED_MAX,
        max_depth=64,
        max_nodes=retention_limit // 128,
        max_retained=retention_limit,
    )


def _omitted_default(default, value) -> bool:
    """Recognize exact internal markers without evaluating a default factory."""
    if value is default and (
        value is MISSING or type(value) is object or isinstance(value, FieldInfo)
    ):
        return True
    if type(value) is not object or not isinstance(default, FieldInfo):
        return False
    factory = default.default_factory
    return inspect.isfunction(factory) and any(
        factory.__globals__.get(name) is value for name in factory.__code__.co_names
    )


def _deadline(
    name: str, params: dict[str, JsonValue], *, function=None, trusted_builtin=False
) -> float | None:
    from transport.command_budget import http_request_budget
    from transport.legacy.unity_connection import _command_deadline
    from services.execution_receipt_context import execution_owner
    from services.registry.operation_budget import resolve_public_budget

    policy = resolve_public_budget(function, name, params, trusted_builtin=trusted_builtin)
    request = http_request_budget.get()
    started = request.started_at if request is not None else time.monotonic()
    ceilings = []
    if policy.hard_seconds is not None:
        ceilings.append(started + policy.hard_seconds)
    parent = active_operation()
    if parent is not None and parent.request.expires_at is not None:
        ceilings.append(parent.request.expires_at)
    receipt = execution_owner.get()
    if receipt is not None:
        ceilings.append(receipt.expires_at)
    if request is not None and request.hard_expires_at is not None:
        ceilings.append(request.hard_expires_at)
    if request is not None:
        from transport.plugin_hub import PluginHub

        desired = started + max(
            PluginHub._command_timeouts(name, params)[1], policy.readiness_seconds
        )
        request.extend(max(0.0, min([desired, *ceilings]) - started))
        deadline = request.budget.expires_at
    else:
        deadline = _command_deadline(name, params)
        if deadline is not None:
            deadline = max(deadline, started + policy.readiness_seconds)
    return min([deadline, *ceilings]) if deadline is not None else min(ceilings, default=None)


def _refusal(reason: str, *, invocation_started: bool = False) -> dict[str, JsonValue]:
    result = {
        "success": False,
        "error": reason,
        "hint": "select_instance" if reason == "operation_target_changed" else "retry",
        "data": {"reason": reason, "dispatch": "not_dispatched"},
    }
    if reason == "operation_target_changed":
        result["message"] = (
            "The Editor connection or operation generation changed, which can happen when the "
            "same project reconnects or reloads. Read mcpforunity://instances and confirm the "
            "intended project before selecting its current instance. "
        )
        result["data"]["automatic_retry"] = False
        if invocation_started:
            # A wrapper may already have refreshed or edited before a later nested
            # command sees the fence. Its exception alone cannot prove non-dispatch.
            result["data"]["dispatch"] = "unknown"
            result["hint"] = "inspect_state_before_retry"
            result["message"] += (
                "The tool had started; inspect its target state before retrying because earlier "
                "steps may have executed. No automatic replay was performed."
            )
        else:
            result["message"] += (
                "This tool was rejected before it started. After the Editor is ready, submit "
                "the request again against the confirmed instance."
            )
    return result


def wrap_tool(function: Callable[P, T], name: str, unity_target: str | None) -> Callable:
    """Wrap once at registration; direct native senders retain their own admission."""
    signature = inspect.signature(function)
    trusted = trusted_tool(function, name)
    try:
        result_type = inspect.get_annotations(function, eval_str=True).get("return")
    except (NameError, TypeError):
        result_type = None
    model_response = result_type is MCPResponse or MCPResponse in get_args(result_type)

    def refusal_result(value: dict[str, JsonValue]):
        return MCPResponse.model_validate(value) if model_response else value

    @wraps(function)
    async def invoke(*args: P.args, **kwargs: P.kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        ctx = bound.arguments.get("ctx")
        # Validated API inputs are JSON-compatible models/scalars. Context is
        # never put in policy input, queue accounting, diagnostics or workers.
        params = to_jsonable_python(
            {
                key: value
                for key, value in bound.arguments.items()
                if key != "ctx" and not _omitted_default(signature.parameters[key].default, value)
            }
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

        expires_at = _deadline(name, params, function=function, trusted_builtin=trusted)
        instance = await get_unity_instance_from_context(ctx)
        user = None
        if config.http_remote_hosted:
            from services.credential_lease import require_credential_lease

            user = (await require_credential_lease()).user_id
        async with asyncio.timeout_at(expires_at):
            target = await capture_target(instance, user_id=user)
        if target is None:
            # Unselected/unavailable instances keep their existing selection
            # refusal path. Native admission still applies if a target is resolved.
            return await call()
        charge = operation_payload_charge(params)
        if charge is None:
            return refusal_result(_refusal("operation_payload_limit"))
        policy = resolve_operation_policy(name, params, trusted_builtin=trusted)
        observation = (
            trusted
            and name in {"get_test_job", "get_compile_job"}
            and is_observational_continuation(name, params, public=True)
        )
        original_scope = target.scope.key
        invocation_started = False
        # Rebind at most once, before the callback starts. An active mutating
        # wrapper keeps its original fence; only reviewed job observations cross it.
        for attempt in range(2):
            try:
                if not target.is_current():
                    raise OperationScopeChanged()
                await authorize_remote_operation(name, params, ctx=ctx)
                if target.authorize_public is not None:
                    await target.authorize_public(name, params)
                if target.capacity_error is not None:
                    refusal = target.capacity_error()
                    if refusal is not None:
                        return refusal_result(refusal)
                if target.public_available is not None and not await target.public_available(
                    (name, unity_target), getattr(ctx, "fastmcp", None)
                ):
                    if not target.is_current():
                        raise OperationScopeChanged()
                    return refusal_result(_refusal("operation_tool_unavailable"))
                request = admission_for(
                    target,
                    policy.mode,
                    expires_at=expires_at,
                    retained_bytes=charge,
                    observational_continuation=observation,
                )
                async with operation_session(request):
                    await authorize_remote_operation(name, params, ctx=ctx)
                    if target.authorize_public is not None:
                        await target.authorize_public(name, params)
                    if target.capacity_error is not None:
                        refusal = target.capacity_error()
                        if refusal is not None:
                            return refusal_result(refusal)
                    await validate_enabled()
                    if target.public_available is not None and not await target.public_available(
                        (name, unity_target), getattr(ctx, "fastmcp", None)
                    ):
                        if not target.is_current():
                            raise OperationScopeChanged()
                        return refusal_result(_refusal("operation_tool_unavailable"))
                    if not target.is_current():
                        raise OperationScopeChanged()
                    invocation_started = True
                    return await call()
            except OperationScopeChanged:
                if observation and not invocation_started and attempt == 0:
                    async with asyncio.timeout_at(expires_at):
                        fresh = await capture_target(instance, user_id=user)
                    if fresh is not None and fresh.scope.key == original_scope:
                        target = fresh
                        continue
                return refusal_result(
                    _refusal("operation_target_changed", invocation_started=invocation_started)
                )
            except OperationCapacityError:
                return refusal_result(_refusal("operation_capacity"))

    return invoke
