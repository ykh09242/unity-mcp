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


async def wait_for_test_job(
    ctx,
    unity_instance,
    user_id,
    job_id,
    include_failed_tests,
    include_details,
    wait_timeout,
    _fetch_status,
):
    # Keep existing per-job observation/nudge state in its original owner module.
    from services.tools.run_tests import GetTestJobResponse, _job_status_reads, _update_job_nudge

    http_session = (
        await ctx.get_state("unity_session_id")
        if config.transport_mode.lower() == "http"
        else await get_authenticated_stdio_generation(unity_instance)
    )
    deadline = test_job_wait_deadline.get()
    if deadline is None:
        deadline = asyncio.get_event_loop().time() + wait_timeout
    poll_interval = 2.0  # Poll Unity every 2 seconds
    response = None
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
        )
        if unity_instance
        and (not config.http_remote_hosted or isinstance(user_id, str) and bool(user_id))
        and (isinstance(http_session, str) and bool(http_session))
        else None
    )
    async with ResponsePollLifetime() as responses, AsyncExitStack() as leases:
        shared_read = await leases.enter_async_context(_job_status_reads.session(poll_key))
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                if response is None:
                    return MCPResponse(
                        success=False,
                        error="wait_timeout expired before a test job status was received",
                    )
                return GetTestJobResponse.model_validate(response)
            try:
                if config.transport_mode.lower() != "http":
                    current_generation = await get_authenticated_stdio_generation(unity_instance)
                    if current_generation != http_session:
                        await leases.aclose()
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
                            )
                            if unity_instance and http_session
                            else None
                        )
                        shared_read = await leases.enter_async_context(
                            _job_status_reads.session(poll_key)
                        )
                response, observation_order = await responses.fetch(
                    shared_read.fetch(_fetch_status), remaining
                )
            except SharedReadCapacityError:
                return MCPResponse(**response_limit_error("result_capacity"))
            except asyncio.TimeoutError:
                if response is None:
                    return MCPResponse(
                        success=False,
                        error="wait_timeout expired before a test job status was received",
                    )
                return GetTestJobResponse.model_validate(response)

            if isinstance(response, MCPResponse):
                return response
            if not isinstance(response, dict):
                return MCPResponse(success=False, error=str(response))

            if not response.get("success", True):
                return MCPResponse(**response)

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
                return GetTestJobResponse(**response)
            if status in ("succeeded", "failed", "cancelled"):
                return GetTestJobResponse(**response)

            # Check timeout
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                # Timeout reached, return current status
                return GetTestJobResponse(**response)

            # Wait before next poll (but don't exceed remaining time)
            await asyncio.sleep(min(poll_interval, remaining))
