from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
import transport.unity_transport as unity_transport
from transport.plugin_hub import extend_http_request_deadline
from transport.command_timeout import MAX_COMMAND_TIMEOUT, configured_wait_timeout
from transport.command_budget import command_outcome_unknown
import transport.legacy.unity_connection as _legacy_conn
from services.state.external_changes_scanner import external_changes_scanner
import services.resources.editor_state as editor_state
from services.tools.get_compile_job import parse_compile_job, read_compile_job, unknown_compile_job

logger = logging.getLogger(__name__)

REFRESH_READY_TIMEOUT_SECONDS = 300.0


def _in_pytest() -> bool:
    """Return True when running inside pytest to avoid polling unmocked resources."""
    return "PYTEST_CURRENT_TEST" in os.environ


async def wait_for_editor_ready(
    ctx: Context,
    timeout_s: float | None = None,
    *,
    on_observation: Callable[[dict[str, Any] | None], Awaitable[None]] | None = None,
) -> tuple[bool, float]:
    """Poll editor_state until Unity is ready for tool calls.

    Returns (ready, elapsed_seconds).  Treats exceptions from
    get_editor_state as "not ready yet" so the loop survives transient
    connection errors during domain reload.
    """
    if timeout_s is None:
        timeout_s = configured_wait_timeout(
            "UNITY_MCP_EDITOR_READY_TIMEOUT", REFRESH_READY_TIMEOUT_SECONDS
        )
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        return (False, 0.0)
    extend_http_request_deadline(timeout_s)
    if _in_pytest():
        return (True, 0.0)

    start = time.monotonic()
    deadline = start + timeout_s
    while time.monotonic() < deadline:
        data = None
        try:
            state_resp = await asyncio.wait_for(
                editor_state.get_editor_state_authoritative(ctx),
                timeout=max(0.0, deadline - time.monotonic()),
            )
            if time.monotonic() >= deadline:
                break
            state = state_resp.model_dump() if hasattr(state_resp, "model_dump") else state_resp
            data = (
                state.get("data")
                if isinstance(state, dict) and state.get("success") is True
                else None
            )
        except Exception:
            pass  # not ready yet — keep polling
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        advice = (data or {}).get("advice") if isinstance(data, dict) else None
        observed_ready = isinstance(advice, dict) and advice.get("ready_for_tools") is True
        if on_observation is not None:
            try:
                await asyncio.wait_for(on_observation(data), timeout=remaining)
            except TimeoutError:
                if not observed_ready:
                    raise
                # Optional evidence collection cannot erase readiness already
                # observed before the deadline. Its work is cancelled and drained.
        if observed_ready:
            return (True, time.monotonic() - start)
        if time.monotonic() >= deadline:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.25, remaining))

    return (False, time.monotonic() - start)


def is_reloading_rejection(resp: Any) -> bool:
    """Recognize a reported refusal for conservative acknowledgement decisions.

    Handler dictionaries are not proof of zero dispatch; retry requires the
    separate typed local-preflight check.
    """
    if isinstance(resp, MCPResponse):
        return (
            not resp.success
            and isinstance(resp.data, dict)
            and resp.data.get("reason") == "reloading"
            and resp.hint == "retry"
        )
    if not isinstance(resp, dict) or resp.get("success"):
        return False
    data = resp.get("data") or {}
    return data.get("reason") == "reloading" and resp.get("hint") == "retry"


def _is_preflight_reload(resp: Any) -> bool:
    # Handler data can conservatively prevent acknowledgement, but never prove
    # zero dispatch. Only the transport's in-process preflight model can retry.
    return isinstance(resp, MCPResponse) and is_reloading_rejection(resp)


def is_connection_lost_after_send(resp: Any) -> bool:
    """True when a mutation's response indicates TCP was lost after command was sent.

    Script mutations trigger domain reload which kills the TCP connection.
    The mutation was likely executed but the response was lost.
    """
    if isinstance(resp, dict):
        if resp.get("success"):
            return False
        err = (resp.get("error") or resp.get("message") or "").lower()
    else:
        if getattr(resp, "success", None):
            return False
        err = (getattr(resp, "error", "") or "").lower()
    return "connection closed" in err or "disconnected" in err or "aborted" in err


async def send_mutation(
    ctx: Context,
    unity_instance: str | None,
    command: str,
    params: dict[str, Any],
    *,
    verify_after_disconnect: Callable[[], Awaitable[dict | None]] | None = None,
) -> dict | Any:
    from services.reference_graph.mutation_tracking import tracked_mutation

    return await tracked_mutation(
        ctx,
        unity_instance,
        command,
        params,
        lambda: _send_mutation(
            ctx, unity_instance, command, params, verify_after_disconnect=verify_after_disconnect
        ),
    )


async def _send_mutation(
    ctx: Context,
    unity_instance: str | None,
    command: str,
    params: dict[str, Any],
    *,
    verify_after_disconnect: Callable[[], Awaitable[dict | None]] | None = None,
) -> dict | Any:
    """Send a non-idempotent mutation with reload recovery.

    Handles the full retry/recovery pattern for script mutations:
    1. Send with retry_on_reload=False (don't re-send if Unity is reloading)
    2. If a typed local transport preflight refused before dispatch → wait + retry once
    3. If connection lost after send → wait + verify via callback
    4. Wait for editor readiness after successful or disconnected mutations

    Args:
        verify_after_disconnect: read-only async callable returning a replacement
            response dict if the mutation was verified after connection loss,
            or None to keep the original error response.
    """
    resp = await unity_transport.send_with_unity_instance(
        _legacy_conn.async_send_command_with_retry,
        unity_instance,
        command,
        params,
        retry_on_reload=False,
    )
    if _is_preflight_reload(resp):
        ready, _ = await wait_for_editor_ready(ctx)
        if not ready:
            return resp
        resp = await unity_transport.send_with_unity_instance(
            _legacy_conn.async_send_command_with_retry,
            unity_instance,
            command,
            params,
            retry_on_reload=False,
        )
    if (
        isinstance(resp, dict)
        and resp.get("success") is False
        and resp.get("hint") == "select_instance"
        and isinstance(resp.get("data"), dict)
        and resp["data"].get("reason") == "instance_selection_required"
    ):
        # Selection failed before dispatch; polling cannot select an instance.
        return resp
    raw_error = str(
        (resp.get("error") or resp.get("message") or "")
        if isinstance(resp, dict)
        else getattr(resp, "error", "")
    ).lower()
    if is_connection_lost_after_send(resp) or "timeout" in raw_error or "timed out" in raw_error:
        from services.reference_graph.mutation_tracking import mark_uncertain_dispatch

        mark_uncertain_dispatch()
    if is_connection_lost_after_send(resp) and verify_after_disconnect:
        ready, _ = await wait_for_editor_ready(ctx)
        verified = await verify_after_disconnect()
        if verified is not None:
            resp = verified
            if ready and verified.get("success") is True:
                # Verification only reads state; it cannot schedule another reload.
                return resp
    failed = (
        resp.get("success") is False
        if isinstance(resp, dict)
        else (isinstance(resp, MCPResponse) and resp.success is False)
    )
    error = (
        (resp.get("error") or resp.get("message") or "")
        if isinstance(resp, dict)
        else (getattr(resp, "error", "") or getattr(resp, "message", "") or "")
    )
    timed_out = "timeout" in error.lower() or "timed out" in error.lower()
    if (
        failed
        and not timed_out
        and not is_connection_lost_after_send(resp)
        and not is_reloading_rejection(resp)
    ):
        # Return definitive tool errors without delaying them behind an
        # unrelated readiness poll. Timeouts can occur after a mutation was
        # sent, so keep their readiness wait alongside reload/disconnect recovery.
        return resp
    await wait_for_editor_ready(ctx)
    return resp


async def verify_edit_by_sha(
    unity_instance: str | None,
    name: str,
    path: str,
    pre_sha: str | None,
    *,
    expected_sha: str | None = None,
) -> bool:
    """Confirm the expected result, never infer ownership from an arbitrary change."""
    if not pre_sha or not expected_sha:
        return False
    try:
        verify = await unity_transport.send_with_unity_instance(
            _legacy_conn.async_send_command_with_retry,
            unity_instance,
            "manage_script",
            {"action": "get_sha", "name": name, "path": path},
        )
        if isinstance(verify, dict) and verify.get("success"):
            new_sha = (verify.get("data") or {}).get("sha256")
            return bool(new_sha and new_sha == expected_sha and new_sha != pre_sha)
    except Exception as exc:
        logger.debug(
            "Failed to verify edit after disconnect for %s at %s: %r",
            name,
            path,
            exc,
        )
    return False


@mcp_for_unity_tool(
    description=(
        "Request a Unity asset database refresh and optionally a script compilation. "
        "Can wait for native Editor readiness, independently of background file scan coverage. "
        "Readiness timeout preserves the latest compilation snapshot and reports readiness "
        "blockers separately; compilation_observation_age_seconds describes snapshot age. "
        "Response summaries follow the latest observed Editor phase and compilation evidence. "
        "compilation_goal_satisfied means a matching pipeline cycle was observed, not compiler "
        "success; source_revision_confirmed remains false without source-to-assembly proof. "
        "A timeout never replays the refresh or cancels native compilation."
    ),
    annotations=ToolAnnotations(
        title="Refresh Unity",
        destructiveHint=True,
    ),
)
async def refresh_unity(
    ctx: Context,
    mode: Annotated[Literal["if_dirty", "force"], "Refresh mode"] = "if_dirty",
    scope: Annotated[Literal["assets", "scripts", "all"], "Refresh scope"] = "all",
    compile: Annotated[Literal["none", "request"], "Whether to request compilation"] = "none",
    wait_for_ready: Annotated[
        bool,
        "If true, wait until mcpforunity://editor/state reports data.advice.ready_for_tools true",
    ] = True,
    timeout_seconds: Annotated[
        float,
        Field(strict=True, ge=1, le=MAX_COMMAND_TIMEOUT, allow_inf_nan=False),
        "Total refresh/readiness observation budget in seconds; expiry does not undo native work",
    ] = REFRESH_READY_TIMEOUT_SECONDS,
) -> MCPResponse | dict[str, Any]:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 1 <= timeout_seconds <= MAX_COMMAND_TIMEOUT
        or not math.isfinite(timeout_seconds)
    ):
        return MCPResponse(
            success=False, error="timeout_seconds must be a finite number from 1 to 86400."
        )
    deadline = time.monotonic() + timeout_seconds
    extend_http_request_deadline(timeout_seconds + 5.0)
    unity_instance = await get_unity_instance_from_context(ctx)
    dirty_instance = unity_instance or await editor_state.infer_single_instance_id(ctx)
    dirty_snapshot = (
        external_changes_scanner.capture_dirty_state(dirty_instance) if dirty_instance else None
    )

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return MCPResponse(
            success=False,
            error="Refresh observation budget expired before dispatch.",
            hint="retry",
            data={"reason": "command_deadline_exceeded", "phase": "selection"},
        )
    params: dict[str, Any] = {
        "mode": mode,
        "scope": scope,
        "compile": compile,
        # Explicit compilation needs the native start barrier. For an asset refresh,
        # wait on the server instead: the native wait cannot survive domain reload
        # and also waits for an otherwise-ready Play Mode session to end.
        "wait_for_ready": bool(wait_for_ready and compile == "request"),
        "timeout_seconds": remaining,
    }

    compile_id = uuid.uuid4().hex if compile == "request" else None
    if compile_id is not None:
        params["compile_id"] = compile_id
    compilation = unknown_compile_job(compile_id, "tracking_unsupported") if compile_id else None
    compilation_observed_at = None
    compilation_observation_error = None
    readiness = None
    resulting_state = None

    def with_compilation(value):
        if compile_id is None and readiness is None:
            return value
        payload = value.model_dump() if isinstance(value, MCPResponse) else dict(value)
        data = dict(payload["data"]) if isinstance(payload.get("data"), dict) else {}
        if readiness is not None:
            data["readiness"] = readiness
            if resulting_state is not None:
                data["resulting_state"] = resulting_state
            if readiness["ready"]:
                data["hint"] = (
                    "Editor is ready for tools. Inspect compilation.outcome for compiler status; "
                    "readiness does not confirm a source revision."
                    if compile_id is not None
                    else "Editor is ready for tools."
                )
        if compile_id is not None:
            data["compilation"] = compilation
            data["source_revision_confirmed"] = False
            # The goal is an observed request-associated pipeline cycle, not a
            # successful compiler outcome or proof of the loaded source revision.
            if (
                compilation["state"] in ("running", "finished")
                and compilation["attribution"] == "observed_after_request"
                and compilation["session_id"] is not None
                and compilation["generation"] is not None
                and compilation["candidate_generations"] == [compilation["generation"]]
                and compilation["requested_at_unix_ms"] is not None
                and compilation["started_at_unix_ms"] is not None
                and compilation["requested_at_unix_ms"] <= compilation["started_at_unix_ms"]
            ):
                data["compilation_goal_satisfied"] = True
            if compilation["native_coordination"] is not None:
                data["native_coordination"] = compilation["native_coordination"]
            data["compilation_observation_age_seconds"] = (
                max(0.0, time.monotonic() - compilation_observed_at)
                if compilation_observed_at is not None
                else None
            )
            if compilation_observation_error is not None:
                data["compilation_observation_error"] = compilation_observation_error
        if compile_id is not None and dirty_snapshot is not None:
            current = external_changes_scanner.capture_dirty_state(dirty_instance)
            if current is not None and (
                current.state is not dirty_snapshot.state
                or current.last_seen_mtime_ns != dirty_snapshot.last_seen_mtime_ns
            ):
                data["concurrent_external_changes"] = True
        payload["data"] = data
        return MCPResponse(**payload)

    recovered_from_disconnect = False
    # Don't retry on reload - refresh_unity triggers compilation/reload,
    # so retrying would cause multiple reloads (issue #577)
    try:
        response = await asyncio.wait_for(
            unity_transport.send_with_unity_instance(
                _legacy_conn.async_send_command_with_retry,
                unity_instance,
                "refresh_unity",
                params,
                retry_on_reload=False,
            ),
            timeout=max(0.0, deadline - time.monotonic()),
        )
    except TimeoutError:
        # Expiry bounds observation, not native side effects. Never replay the write.
        return with_compilation(
            MCPResponse(
                **command_outcome_unknown(
                    "refresh_unity",
                    "observation deadline expired",
                    cause="timeout",
                    phase="dispatch",
                )
            )
        )

    # A domain reload may lose the response after dispatch. Recover readiness
    # without replaying the refresh; execution remains unconfirmed in that case.
    # Convert MCPResponse to dict if needed
    response_dict = (
        response
        if isinstance(response, dict)
        else (response.model_dump() if hasattr(response, "model_dump") else response.__dict__)
    )
    native_data = response_dict.get("data")
    if compile_id and isinstance(native_data, dict):
        compilation = parse_compile_job(native_data.get("compilation"), compile_id)
        if compilation["reason"] != "tracking_unsupported":
            compilation_observed_at = time.monotonic()
    if not response_dict.get("success", True):
        err = (response_dict.get("error") or response_dict.get("message") or "").lower()
        outcome = response_dict.get("data")
        deadline_after_dispatch = (
            isinstance(outcome, dict)
            and outcome.get("reason") == "outcome_unknown"
            and outcome.get("cause") == "timeout"
        )
        is_connection_lost = is_connection_lost_after_send(response_dict) or (
            (deadline_after_dispatch or "timeout" in err) and compile == "request"
        )
        # A rejected command never ran. Later readiness cannot prove refresh success.
        # Only an ambiguous lost response may recover, without replaying the write.
        if is_reloading_rejection(response_dict) or not wait_for_ready or not is_connection_lost:
            return with_compilation(MCPResponse(**response_dict))
        recovered_from_disconnect = True

    compile_polled = False
    last_compile_poll = None

    async def observe_refresh(state):
        nonlocal compilation, compilation_observed_at, compilation_observation_error
        nonlocal readiness, resulting_state, compile_polled, last_compile_poll
        advice = state.get("advice") if isinstance(state, dict) else None
        observed_ready = isinstance(advice, dict) and advice.get("ready_for_tools") is True
        if isinstance(advice, dict):
            readiness = {
                "ready": observed_ready,
                "blocking_reasons": advice.get("blocking_reasons"),
                "warnings": advice.get("warnings", []),
            }
            activity = state.get("activity")
            phase = activity.get("phase") if isinstance(activity, dict) else None
            if isinstance(phase, str) and phase:
                resulting_state = phase
        if (
            compile_id is None
            or (not recovered_from_disconnect and compilation["reason"] == "tracking_unsupported")
            # A compiler finish can precede native release. At the ready edge,
            # observe that status once more within the same remaining budget.
            or (not observed_ready and compile_polled and compilation["state"] == "finished")
            or (
                not observed_ready
                and last_compile_poll is not None
                and time.monotonic() - last_compile_poll < 1.0
            )
        ):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        last_compile_poll = time.monotonic()
        # Also retain this marker if the outer readiness budget cancels the read.
        compilation_observation_error = "observation_unavailable"
        try:
            observed = await asyncio.wait_for(
                read_compile_job(unity_instance, compile_id), timeout=min(1.0, remaining)
            )
            if (
                observed["outcome"] == "unknown"
                and observed["state"] == "finished"
                and observed["session_id"] is None
            ):
                compilation_observation_error = observed["reason"]
                return
            compilation = observed
            compilation_observed_at = time.monotonic()
            compilation_observation_error = None
            compile_polled = True
        except Exception:
            # A failed read cannot erase previously observed compiler evidence.
            compilation_observation_error = "observation_unavailable"

    # Optional server-side wait loop (defensive): if Unity tool doesn't wait or returns quickly,
    # poll the canonical editor_state resource until ready or timeout.
    ready_confirmed = False
    if wait_for_ready:
        readiness = {"ready": False, "blocking_reasons": None, "warnings": []}
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                ready_confirmed, _ = await wait_for_editor_ready(
                    ctx, timeout_s=remaining, on_observation=observe_refresh
                )
            except TimeoutError:
                pass  # The observer shares the readiness deadline; never extend it.
        readiness["ready"] = ready_confirmed

        # If we timed out without confirming readiness, log and return failure
        if not ready_confirmed:
            logger.warning(
                "refresh_unity: Timed out after %ss waiting for editor to become ready",
                timeout_seconds,
            )
            return with_compilation(
                MCPResponse(
                    success=False,
                    message=f"Refresh triggered but timed out after {timeout_seconds:g}s waiting for editor readiness.",
                    data={
                        **(native_data if isinstance(native_data, dict) else {}),
                        "timeout": True,
                        "wait_seconds": timeout_seconds,
                    },
                )
            )

    if (
        compile_id
        and wait_for_ready
        and not compile_polled
        and (recovered_from_disconnect or compilation["reason"] != "tracking_unsupported")
    ):
        await observe_refresh(None)

    # A dispatch-only response does not confirm import. Acknowledge only the edits
    # observed before dispatch, leaving concurrent or subsequently sampled edits dirty.
    response_data = response_dict.get("data")
    refresh_performed = (
        response_dict.get("success") is True
        and scope in ("assets", "all")
        and (
            not isinstance(response_data, dict)
            or response_data.get("refresh_triggered") is not False
        )
    )
    if ready_confirmed and refresh_performed and dirty_snapshot is not None:
        external_changes_scanner.clear_dirty(dirty_instance, expected=dirty_snapshot)

    if recovered_from_disconnect:
        return with_compilation(
            MCPResponse(
                success=True,
                message="Editor is ready after the refresh response was lost; refresh execution is unconfirmed.",
                data={"recovered_from_disconnect": True},
            )
        )

    return with_compilation(
        MCPResponse(**response_dict) if isinstance(response, dict) else response
    )
