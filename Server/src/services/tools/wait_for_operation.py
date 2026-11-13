"""Observe existing native job owners; never start, resume or cancel their work."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from core.config import config
from models.models import MCPResponse
from models.response_lifetime import ResponsePollLifetime
from models.unity_response import normalize_unity_response
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.test_job_wait import test_job_wait_deadline
from transport.legacy.unity_connection import (
    async_send_command_with_retry,
    get_authenticated_stdio_generation,
)
from transport.plugin_hub import PluginHub, http_request_budget

JobKind = Literal["build", "package", "scenario", "recording", "auditor", "test"]
POLL_INTERVAL = 2.0
_COMMANDS = {
    "build": "manage_build",
    "package": "manage_packages",
    "scenario": "manage_play_scenario",
    "recording": "manage_recording",
    "auditor": "manage_project_auditor",
    "test": "get_test_job",
}
_TERMINAL = frozenset(
    {
        "succeeded",
        "failed",
        "cancelled",
        "skipped",
        "interrupted",
        "timed_out",
        "completed",
        "stopped",
        "unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class ObservationTarget:
    instance: str
    session_id: str | None
    generation: str
    user_id: str | None


# Journal fallback stays bound to the same immutable observation identity.
operation_wait_target: ContextVar[ObservationTarget | None] = ContextVar(
    "operation_wait_target", default=None
)


def unavailable(reason: str) -> dict:
    response = {
        "success": False,
        "error": reason,
        "data": {"reason": reason, "native_job_unchanged": True},
    }
    if reason in {
        "observation_generation_changed",
        "observation_connection_unavailable_or_changed",
    }:
        response["hint"] = (
            "The observation connection changed. Re-select the same Unity instance and explicitly "
            "observe the same job_id again. This does not establish native job failure; do not "
            "restart or replay the operation."
        )
    return response


async def capture_target(ctx: Context) -> ObservationTarget | None:
    instance = await get_unity_instance_from_context(ctx)
    if not isinstance(instance, str) or not instance:
        return None
    user_id = await ctx.get_state("user_id") if config.http_remote_hosted else None
    expected = await ctx.get_state("operation_wait_generation")
    session = (
        await ctx.get_state("unity_session_id") if config.transport_mode.lower() == "http" else None
    )
    target = await capture_observation_target(instance, session, user_id)
    if target is None or expected is not None and expected != target.generation:
        return None
    captured = operation_wait_target.get()
    return target if captured is None or captured == target else None


async def capture_observation_target(
    instance: str, session: str | None, user_id: str | None
) -> ObservationTarget | None:
    """Pin the authenticated generation for an already resolved instance and route."""
    if config.transport_mode.lower() == "http":
        identity = await PluginHub._read_identity(session) if isinstance(session, str) else None
        registry = PluginHub._registry
        registered = await registry.get_session(session) if identity and registry else None
        if (
            identity is None
            or identity.user_id != user_id
            or registered is None
            or registered.project_hash != instance.rpartition("@")[2]
        ):
            return None
        generation = identity.generation
    else:
        session = None
        generation = await get_authenticated_stdio_generation(instance)
    if not isinstance(generation, str) or not generation:
        return None
    return ObservationTarget(instance, session, generation, user_id)


async def fetch_status(target: ObservationTarget, kind: JobKind, job_id: str):
    command = _COMMANDS[kind]
    params = {"action": "status", "audit_id" if kind == "auditor" else "job_id": job_id}
    if kind == "test":
        params = {"job_id": job_id, "includeFailedTests": True, "page_size": 10}
    return await fetch_observation(target, command, params)


async def fetch_observation(target: ObservationTarget, command: str, params: dict):
    """Send one declared read against the captured authenticated generation."""
    if target.session_id is not None:
        current = await PluginHub._read_identity(target.session_id)
        if (
            current is None
            or current.generation != target.generation
            or current.user_id != target.user_id
        ):
            return unavailable("observation_generation_changed")
        request = http_request_budget.get()
        options = (
            {
                "_budget": request.command_budget(
                    command, PluginHub._command_timeouts(command, params)[1]
                )
            }
            if request
            else {}
        )
        return await PluginHub.send_command(
            target.session_id, command, params, _expected_generation=target.generation, **options
        )
    return await async_send_command_with_retry(
        command,
        params,
        instance_id=target.instance,
        retry_on_reload=False,
        _expected_generation=target.generation,
    )


def project_observation(kind: JobKind, job_id: str, response) -> dict:
    if isinstance(response, MCPResponse):
        response = response.model_dump(exclude_none=True)
    # Direct HTTP reads retain the transport envelope; its success only
    # acknowledges the query, never completion of the enclosed native job.
    response = normalize_unity_response(response)
    if not isinstance(response, dict):
        return unavailable("invalid_job_observation")
    data = response.get("data")
    if response.get("success") is not True:
        if (
            response.get("success") is False
            and kind == "package"
            and isinstance(data, dict)
            and data.get("job_id") == job_id
            and data.get("operation") in ("list_packages", "search_packages")
            and data.get("status") == "failed"
        ):
            # UPM query failures keep their direct ErrorResponse contract. A failed
            # identified job is still a successful observation of its native outcome.
            data = {**data, **{key: response[key] for key in ("error", "code") if key in response}}
        else:
            if isinstance(data, dict) and data.get("reason") == "observation_generation_changed":
                response = {
                    **response,
                    "hint": unavailable("observation_generation_changed")["hint"],
                }
            return response
    identity_key = "audit_id" if kind == "auditor" else "job_id"
    if not isinstance(data, dict) or data.get(identity_key) != job_id:
        return unavailable("job_observation_identity_mismatch")
    state = data.get("result" if kind == "build" else "state" if kind == "auditor" else "status")
    if not isinstance(state, str):
        return unavailable("invalid_job_observation")
    state = state.lower()
    test_cleanup_pending = False
    if kind == "test":
        from services.tools.run_tests import native_test_cleanup_pending

        test_cleanup_pending = native_test_cleanup_pending(data)
    scenario_cleanup_failed = (
        kind == "scenario"
        and data.get("phase") == "finished"
        and state in _TERMINAL
        and data.get("runner_resources_released") is False
    )
    batch_child_running = (
        kind == "build" and state == "cancelled" and bool(data.get("current_build"))
    )
    if (
        test_cleanup_pending
        or batch_child_running
        or data.get("phase") == "finalizing"
        or data.get("runner_resources_released") is False
        and state in _TERMINAL
        and not scenario_cleanup_failed
        or kind == "auditor"
        and data.get("native_owner_retained") is True
        and state in _TERMINAL
    ):
        phase, terminal = "finalizing", False
    elif (
        kind == "package"
        and data.get("operation") == "resolve"
        and isinstance(data.get("resolve"), dict)
        and data["resolve"].get("completion") == "unknown"
    ):
        phase, terminal = "unknown", False
    elif state in _TERMINAL:
        phase, terminal = "terminal", True
    elif state in {"pending", "queued", "starting", "waiting"}:
        phase, terminal = "waiting", False
    elif state in {"building", "running", "recording"}:
        phase, terminal = "running", False
    elif state == "unknown":
        phase, terminal = "unknown", False
    else:
        return unavailable("unsupported_job_observation")
    summary = terminal_summary(data, kind) if terminal else None
    if scenario_cleanup_failed:
        # The native owner has finished finalization. Polling cannot release its
        # retained resources; preserve its outcome and existing recovery semantics.
        summary["recovery"] = {
            "source": "play_scenario",
            "phase": "cleanup_failed",
            "reason": "scenario_resources_unreleased",
            "mode": "restart_required",
            "job_id": job_id,
            "message": (
                "The scenario finished without confirming release of its runner resources. "
                "Inspect the job's cleanup and report errors, then restart the Editor before "
                "starting native work."
            ),
        }
    return {
        "success": True,
        "data": {
            "kind": kind,
            "job_id": job_id,
            "phase": phase,
            "terminal": terminal,
            "job_status": state,
            "wait_status": "observed",
            "job": data,
            **({"summary": summary} if terminal else {}),
        },
    }


def terminal_summary(data: dict, kind: JobKind | None = None) -> dict:
    """Retain a compact sample from the observed terminal job without another read."""
    if kind == "scenario":
        from services.workflow_summaries import scenario_summary

        return scenario_summary(data)
    summary = {}
    for key in (
        "error",
        "reason",
        "output_path",
        "package_name",
        "package_version",
        "operation",
        "result_name",
        "result_version",
        "frames_recorded",
        "skipped_sample_slots",
        "elapsed_seconds",
        "diagnostic_count",
        "diagnostic_count_exact",
        "modules_failed",
        "modules_interrupted",
        "results_available",
        "observation_truncated",
        "clean",
    ):
        value = data.get(key)
        if type(value) in (str, bool, int, float):
            summary[key] = value[:1024] if isinstance(value, str) else value
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("summary"), dict):
        summary["counts"] = {
            key: result["summary"][key]
            for key in ("total", "passed", "failed", "skipped", "inconclusive", "durationSeconds")
            if type(result["summary"].get(key)) in (bool, int, float)
        }
    progress = data.get("progress")
    if isinstance(progress, dict) and isinstance(progress.get("failures_so_far"), list):
        failures = progress["failures_so_far"]
        summary["failures"] = [
            {
                key: row[key][:1024]
                for key in ("full_name", "message")
                if isinstance(row.get(key), str)
            }
            for row in failures[:10]
            if isinstance(row, dict)
        ]
        summary["failures_truncated"] = (
            len(failures) > 10 or progress.get("failures_capped") is True
        )
    return summary


@mcp_for_unity_tool(
    group="core",
    unity_target=None,
    description="Observe an existing test, build, package query/mutation/resolve, single scenario, recording, or Project Auditor job. "
    "Never starts, retries or cancels native work. wait_timeout bounds only this observation (0 is one poll). "
    "Reports waiting/running/finalizing/terminal/unknown with the original job evidence; a failed job is a valid observation. "
    "An authenticated connection is captured once; reload/disconnect requires a fresh explicit observation. "
    "Opt-in modern HTTP Tasks can durably retain this bounded wait; other clients receive ordinary polling results.",
    annotations=ToolAnnotations(
        title="Wait for Operation",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def wait_for_operation(
    ctx: Context,
    kind: JobKind,
    job_id: Annotated[str, Field(min_length=1, max_length=1024, pattern=r"^\S+$")],
    wait_timeout: Annotated[int, Field(strict=True, ge=0, le=60)] = 30,
) -> dict:
    if kind in {"scenario", "recording", "auditor", "test"} and (
        len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id)
    ):
        return unavailable("invalid_scenario_job_id" if kind == "scenario" else "invalid_job_id")
    loop = asyncio.get_running_loop()
    deadline = test_job_wait_deadline.get()
    if deadline is None:
        deadline = loop.time() + wait_timeout
    try:
        target = await asyncio.wait_for(
            capture_target(ctx), max(0.0, deadline - loop.time()) if wait_timeout else None
        )
    except TimeoutError:
        return unavailable("observation_wait_expired_before_status")
    if target is None:
        return unavailable("observation_connection_unavailable_or_changed")
    response = None
    async with ResponsePollLifetime() as lifetime:
        while True:
            remaining = deadline - loop.time()
            if wait_timeout > 0 and remaining <= 0:
                break
            try:
                native = await lifetime.fetch(
                    fetch_status(target, kind, job_id), remaining if wait_timeout else None
                )
            except TimeoutError:
                break
            response = project_observation(kind, job_id, native)
            if (
                not response.get("success")
                or not wait_timeout
                or response["data"]["phase"] in {"terminal", "unknown"}
            ):
                return response
            remaining = deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(min(POLL_INTERVAL, remaining))
        if response is None:
            return unavailable("observation_wait_expired_before_status")
        response["data"]["wait_status"] = "expired"
        return response
