"""Transport helpers for routing commands to Unity."""

from __future__ import annotations

import logging
import asyncio
from typing import Awaitable, Callable, TypeVar

from transport.plugin_hub import (
    CommandNotDispatchedError,
    InstanceSelectionRequiredError,
    PluginHub,
    command_outcome_unknown,
)
from services.execution_receipt_context import observe_native_result
from services.command_progress import ProgressSink
from core.config import config
from services.credential_lease import (
    CredentialLeaseError,
    current_credential_lease,
    require_credential_lease,
)
from models.models import MCPResponse
from models.unity_response import normalize_unity_response

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def send_job_observation(send_fn, unity_instance, command, params, **kwargs):
    """Retry one refused job read on the same owner, never a started/unknown command.

    Each transport still checks credentials, catalog and native admission. The
    public operation's original project/principal and elapsed budget stay pinned.
    """
    from services.registry.operation_context import active_operation
    from services.registry.operation_invocation import is_observational_continuation
    from services.registry.operation_target import capture_target
    from transport.command_budget import http_request_budget
    from transport.legacy.unity_connection import _command_deadline

    owner = active_operation()
    if (
        owner is None
        or not unity_instance
        or command not in {"get_test_job", "get_compile_job"}
        or not is_observational_continuation(command, params)
    ):
        return await send_with_unity_instance(send_fn, unity_instance, command, params, **kwargs)
    scope = owner.request.scope.key
    deadline = _command_deadline(command, params)
    if _is_http_transport():
        ceiling = (
            asyncio.get_running_loop().time() + PluginHub._command_timeouts(command, params)[1]
        )
        deadline = min(deadline, ceiling) if deadline is not None else ceiling
    request = http_request_budget.get()
    if request is not None:
        deadline = (
            min(deadline, request.budget.expires_at)
            if deadline is not None
            else request.budget.expires_at
        )
    result = None
    sending = False
    try:
        async with asyncio.timeout_at(deadline):
            for attempt in range(2):
                sending = True
                result = await send_with_unity_instance(
                    send_fn, unity_instance, command, params, **kwargs, _deadline=deadline
                )
                sending = False
                value = result.model_dump() if isinstance(result, MCPResponse) else result
                if not isinstance(value, dict) or value.get("success", True):
                    return result
                data = value.get("data") or {}
                reason = data.get("reason", value.get("error")) if isinstance(data, dict) else None
                if not isinstance(reason, str) or reason not in {
                    "operation_target_changed",
                    "read_invalidated",
                    "no_unity_session",
                    "stale_connection",
                    "command_not_dispatched",
                }:
                    return result
                if attempt == 1:
                    break
                target = await capture_target(unity_instance, user_id=kwargs.get("user_id"))
                if target is None or target.scope.key != scope or not target.is_current():
                    break
                await asyncio.sleep(0.1)
    except TimeoutError:
        if sending:
            # A previous refusal says nothing about this attempt's dispatch.
            result = command_outcome_unknown(
                command, "job observation deadline expired", cause="timeout"
            )
        elif result is None:
            result = {
                "success": False,
                "error": "Job status observation timed out",
                "data": {"reason": "job_observation_timeout"},
            }
    value = result.model_dump() if isinstance(result, MCPResponse) else dict(result)
    value["message"] = (
        "The job status could not be observed during connection recovery. "
        "This does not mean the job failed. Retry the same job ID after the Editor reconnects; "
        "do not start the tests or compilation again."
    )
    value["hint"] = "retry"
    value["data"] = {
        **(value.get("data") or {}),
        **{key: params[key] for key in ("job_id", "compile_id") if key in params},
        "job_status": "unknown",
        "next_action": "poll_same_job",
        "retry_after_ms": 500,
    }
    return MCPResponse(**value) if isinstance(result, MCPResponse) else value


def _is_http_transport() -> bool:
    return config.transport_mode.lower() == "http"


def _observed_unity_response(raw):
    """Observe the final canonical response, including outer error construction."""
    observe_native_result(raw)
    return normalize_unity_response(raw)


async def _resolve_user_id_from_request() -> str | None:
    """Read identity validated by the outer HTTP authentication boundary."""
    if not config.http_remote_hosted:
        return None
    lease = current_credential_lease()
    if lease is None:
        return None
    await lease.check()
    return lease.user_id


async def send_with_unity_instance(
    send_fn: Callable[..., Awaitable[T]],
    unity_instance: str | None,
    *args,
    user_id: str | None = None,
    _progress: ProgressSink | None = None,
    **kwargs,
) -> T:
    if _is_http_transport():
        if not args:
            raise ValueError("HTTP transport requires command arguments")
        command_type = args[0]
        params = args[1] if len(args) > 1 else kwargs.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise TypeError("Command parameters must be a dict for HTTP transport")

        # An explicit principal alone is not a remote authorization capability.
        if config.http_remote_hosted:
            try:
                lease = await require_credential_lease(user_id)
            except CredentialLeaseError:
                return _observed_unity_response(
                    MCPResponse(
                        success=False,
                        error="auth_required",
                        message="API key authentication required",
                    ).model_dump()
                )
            user_id = lease.user_id

        retry_on_reload = kwargs.pop("retry_on_reload", True)
        if not isinstance(retry_on_reload, bool):
            retry_on_reload = True

        read_options = {"_progress": _progress} if _progress is not None else {}
        if "_catalog_guard" in kwargs:
            read_options["_catalog_guard"] = kwargs.pop("_catalog_guard")
            retry_on_reload = False
        if "editor_state_read_mode" in kwargs:
            read_options["editor_state_read_mode"] = kwargs.pop("editor_state_read_mode")

        transport_returned = False
        try:
            raw = await PluginHub.send_command_for_instance(
                unity_instance,
                command_type,
                params,
                user_id=user_id,
                retry_on_reload=retry_on_reload,
                **read_options,
            )
            transport_returned = True
            if config.http_remote_hosted:
                await lease.check()
            return _observed_unity_response(raw)
        except CredentialLeaseError:
            if transport_returned:
                return _observed_unity_response(
                    command_outcome_unknown(
                        command_type,
                        "Remote authorization ended before delivery",
                        unity_instance=unity_instance,
                        cause="authentication_ended",
                    )
                )
            return _observed_unity_response(
                MCPResponse(
                    success=False, error="auth_required", message="Remote authorization ended"
                ).model_dump()
            )
        except InstanceSelectionRequiredError as exc:
            # Not retryable: a blind retry fails identically. The client must pick
            # an instance, so hint at selection and hand over the ids structurally.
            return _observed_unity_response(
                MCPResponse(
                    success=False,
                    error=str(exc),
                    hint="select_instance",
                    data={
                        "reason": "instance_selection_required",
                        "available_instances": exc.available_instances,
                    },
                ).model_dump()
            )
        except CommandNotDispatchedError as exc:
            return _observed_unity_response(
                MCPResponse(
                    success=False,
                    error=str(exc),
                    hint="retry",
                    data={
                        "reason": "command_not_dispatched",
                        "command": command_type,
                        "unity_instance": unity_instance,
                    },
                ).model_dump()
            )
        except Exception as exc:
            # NOTE: asyncio.TimeoutError has an empty str() by default, which is confusing for clients.
            err = str(exc) or f"{type(exc).__name__}"
            # Unknown failures do not prove the command stayed on this server.
            return _observed_unity_response(
                command_outcome_unknown(command_type, err, unity_instance=unity_instance)
            )

    if _progress is not None:
        kwargs["_progress"] = _progress
    if unity_instance:
        kwargs.setdefault("instance_id", unity_instance)
    result = await send_fn(*args, **kwargs)
    observe_native_result({"data": result.data} if isinstance(result, MCPResponse) else result)
    return result
