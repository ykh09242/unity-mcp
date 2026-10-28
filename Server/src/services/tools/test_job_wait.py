"""The existing positive get_test_job wait, shared without a fresh deadline."""

import asyncio
from contextlib import AsyncExitStack
from contextvars import ContextVar

from core.config import config
from models import MCPResponse
from models.response_lifetime import ResponsePollLifetime
from models.response_limits import response_limit_error
from services.tools.shared_tool_reads import SharedReadCapacityError
from transport.legacy.unity_connection import get_authenticated_stdio_generation

# Set only by the trusted Tasks binding/fallback, never by client metadata.
test_job_wait_deadline: ContextVar[float | None] = ContextVar(
    "test_job_wait_deadline", default=None
)


class _TemporaryObservationFailure(Exception):
    """Retain only a known scalar reason, not an untrusted failed result payload."""


class _ObservationGenerationChanged(Exception):
    """Do not accept an incoming result until its connection identity is checked."""


_TRANSIENT_REASONS = frozenset(
    {
        "operation_target_changed",
        "read_invalidated",
        "no_unity_session",
        "stale_connection",
        "command_not_dispatched",
        "command_deadline_exceeded",
        "job_observation_timeout",
        "reloading",
        "editor_not_ready",
    }
)


async def _poll_generation(ctx, unity_instance, user_id):
    if config.transport_mode.lower() != "http":
        return await get_authenticated_stdio_generation(unity_instance)
    from transport.plugin_hub import PluginHub
    from services.registry.operation_target import capture_target

    if PluginHub.is_configured():
        target = await capture_target(unity_instance, user_id=user_id)
        return target.scope.generation if target is not None and target.is_current() else None
    return await ctx.get_state("unity_session_id")


async def wait_for_test_job(
    ctx,
    unity_instance,
    user_id,
    job_id,
    include_failed_tests,
    include_details,
    wait_timeout,
    _fetch_status,
    *,
    page_size=None,
    cursor=None,
):
    # Keep existing per-job observation/nudge state in its original owner module.
    from services.tools.run_tests import (
        _job_status_reads,
        _update_job_nudge,
        native_test_cleanup_pending,
        test_job_response,
    )

    deadline = test_job_wait_deadline.get()
    if deadline is None:
        deadline = asyncio.get_event_loop().time() + wait_timeout
    poll_interval = 2.0  # Poll Unity every 2 seconds
    response = None
    last_error = None

    async def generation():
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError()
        async with asyncio.timeout(remaining):
            return await _poll_generation(ctx, unity_instance, user_id)

    def expired():
        observation = {
            "status": "unavailable",
            "last_known_status": response is not None,
            "next_action": "poll_same_job",
            "retry_after_ms": 500,
        }
        if response is not None:
            if last_error is None:
                return test_job_response(response)
            value = test_job_response(response)
            value.data.observation = observation
            value.hint = "retry"
            value.message = (
                "The wait expired during connection recovery. This is the last observed job "
                "status, not a current snapshot. Poll the same job ID; do not restart the tests."
            )
            return value
        value = dict(last_error or {})
        value.update(
            success=False,
            hint="retry",
            error=value.get("error")
            or "wait_timeout expired before a test job status was received",
            message="The job's current status is unknown; the observation wait expired. "
            "Poll the same job ID after the Editor reconnects; do not restart the tests.",
            data={
                **(value.get("data") or {}),
                "job_id": job_id,
                "job_status": "unknown",
                **observation,
            },
        )
        return MCPResponse(**value)

    try:
        http_session = await generation()
    except TimeoutError:
        return expired()
    # The selected instance and authenticated owner must match. An absent
    # selection may route through caller context, so those reads stay private.
    poll_key = (
        (
            config.transport_mode,
            user_id,
            unity_instance,
            http_session,
            job_id,
            bool(include_failed_tests),
            bool(include_details),
            page_size,
            cursor,
        )
        if unity_instance
        and (not config.http_remote_hosted or isinstance(user_id, str) and bool(user_id))
        and (isinstance(http_session, str) and bool(http_session))
        else None
    )
    async with ResponsePollLifetime() as responses, AsyncExitStack() as leases:
        shared_read = await leases.enter_async_context(_job_status_reads.session(poll_key))

        async def fetch_observation():
            candidate = value = detail = None
            try:
                candidate, order = await shared_read.fetch(_fetch_status)
                value = candidate.model_dump() if isinstance(candidate, MCPResponse) else candidate
                if isinstance(value, dict) and not value.get("success", True):
                    detail = value.get("data") or {}
                    reason = (
                        detail.get("reason", value.get("error"))
                        if isinstance(detail, dict)
                        else None
                    )
                    if isinstance(reason, str) and reason in _TRANSIENT_REASONS:
                        # A failed incoming reservation must not replace the last valid one.
                        raise _TemporaryObservationFailure(reason)
                if http_session != await generation():
                    raise _ObservationGenerationChanged()
                return candidate, order
            finally:
                # Exceptions can retain this frame through recovery/cleanup awaits.
                # Drop payload locals before ResponsePollLifetime releases their charge.
                candidate = value = detail = None

        while True:
            # Only response owns the previous graph across poll iterations. A
            # generation change can release it before awaiting a replacement.
            candidate = data = None
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return expired()
            try:
                current_generation = await generation()
                if current_generation != http_session:
                    await _job_status_reads.invalidate(poll_key)
                    await leases.aclose()
                    response = None
                    responses.discard()
                    http_session = current_generation
                    poll_key = (
                        (
                            config.transport_mode,
                            user_id,
                            unity_instance,
                            http_session,
                            job_id,
                            bool(include_failed_tests),
                            bool(include_details),
                            page_size,
                            cursor,
                        )
                        if unity_instance
                        and http_session
                        and (
                            not config.http_remote_hosted
                            or isinstance(user_id, str)
                            and bool(user_id)
                        )
                        else None
                    )
                    shared_read = await leases.enter_async_context(
                        _job_status_reads.session(poll_key)
                    )
                candidate, observation_order = await responses.fetch(
                    fetch_observation(),
                    max(0.0, deadline - asyncio.get_event_loop().time()),
                )
            except _ObservationGenerationChanged:
                await _job_status_reads.invalidate(poll_key)
                response = None
                responses.discard()
                continue
            except _TemporaryObservationFailure as error:
                last_error = {"success": False, "data": {"reason": str(error)}}
                await _job_status_reads.invalidate(poll_key)
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return expired()
                await asyncio.sleep(min(poll_interval, remaining))
                continue
            except SharedReadCapacityError:
                return MCPResponse(**response_limit_error("result_capacity"))
            except asyncio.TimeoutError:
                last_error = {"success": False, "data": {"reason": "job_observation_timeout"}}
                return expired()

            if isinstance(candidate, MCPResponse):
                candidate = candidate.model_dump()
            if not isinstance(candidate, dict):
                response = None
                return MCPResponse(success=False, error=str(candidate))

            if not candidate.get("success", True):
                response = None
                return MCPResponse(**candidate)
            response = candidate
            last_error = None

            # Check if tests are done
            data = response.get("data", {})
            status = data.get("status", "")
            try:
                await asyncio.wait_for(
                    _update_job_nudge(
                        unity_instance,
                        user_id,
                        job_id,
                        data,
                        wait=True,
                        observation_order=observation_order,
                    ),
                    timeout=max(0.0, deadline - asyncio.get_event_loop().time()),
                )
            except asyncio.TimeoutError:
                return test_job_response(response)
            if status in ("succeeded", "failed", "cancelled") and not native_test_cleanup_pending(
                data
            ):
                return test_job_response(response)

            # Check timeout
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                # Timeout reached, return current status
                return test_job_response(response)

            # Wait before next poll (but don't exceed remaining time)
            await asyncio.sleep(min(poll_interval, remaining))
