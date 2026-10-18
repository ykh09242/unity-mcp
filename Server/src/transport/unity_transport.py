"""Transport helpers for routing commands to Unity."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, TypeVar

from transport.plugin_hub import (
    CommandNotDispatchedError,
    InstanceSelectionRequiredError,
    PluginHub,
    command_outcome_unknown,
)
from services.execution_receipt_context import observe_native_result
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

        read_options = {}
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

    if unity_instance:
        kwargs.setdefault("instance_id", unity_instance)
    result = await send_fn(*args, **kwargs)
    observe_native_result({"data": result.data} if isinstance(result, MCPResponse) else result)
    return result
