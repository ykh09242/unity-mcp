"""Optional first observation of an already-started native job."""

import asyncio

from models.models import MCPResponse
from models.response_lifetime import ResponsePollLifetime
from models.unity_response import normalize_unity_response
from services.operation_permissions import authorize_remote_operation
from services.tools.wait_for_operation import (
    capture_target,
    fetch_observation,
    operation_wait_target,
    wait_for_operation,
)


async def prepare_wait(ctx, wait_timeout):
    """Capture the observation connection before issuing the original operation."""
    if type(wait_timeout) is not int or not 0 <= wait_timeout <= 60:
        raise ValueError("wait_timeout must be an integer from 0 to 60")
    if not wait_timeout:
        return None
    try:
        return await asyncio.wait_for(capture_target(ctx), 3)
    except Exception:
        return None


async def complete_job(ctx, target, kind, response, wait_timeout):
    """Preserve the start/cancel receipt even if status observation cannot complete."""
    if not wait_timeout:
        return response
    value = response.model_dump() if isinstance(response, MCPResponse) else response
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        return response
    data = value["data"]
    job_id = data.get("audit_id" if kind == "auditor" else "job_id")
    if not isinstance(job_id, str) or not job_id:
        return response
    completion = {"status": "unavailable", "complete": False, "job_id": job_id}
    if target is None:
        completion["reason"] = "original_observation_connection_unavailable"
    else:
        token = operation_wait_target.set(target)
        try:
            deadline = asyncio.get_running_loop().time() + wait_timeout
            await authorize_remote_operation(
                "wait_for_operation", {"kind": kind, "job_id": job_id}, ctx=ctx
            )
            observation = await wait_for_operation(ctx, kind, job_id, wait_timeout)
            observed = observation.get("data")
            if observation.get("success") is True and isinstance(observed, dict):
                completion.update(
                    status=observed["job_status"],
                    complete=observed["terminal"],
                    phase=observed["phase"],
                    wait_status=observed["wait_status"],
                    summary=observed.get("summary", {}),
                )
                if kind == "package" and observed["terminal"]:
                    completion["source_state"] = {"status": "unavailable"}
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining > 0:
                        await authorize_remote_operation("get_editor_state", {}, ctx=ctx)
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise TimeoutError("Initial observation budget exhausted")
                        async with ResponsePollLifetime() as lifetime:
                            state = await lifetime.fetch(
                                fetch_observation(target, "get_editor_state", {}), remaining
                            )
                        state = normalize_unity_response(state)
                        source = (
                            state.get("data")
                            if isinstance(state, dict) and state.get("success")
                            else None
                        )
                        if isinstance(source, dict):
                            projection = {"status": "observed", "scope": "editor_flags"}
                            fields = {
                                "compilation": ("is_compiling", "is_domain_reload_pending"),
                                "assets": ("is_updating", "external_changes_dirty"),
                            }
                            for section, keys in fields.items():
                                values = source.get(section)
                                if isinstance(values, dict):
                                    projection[section] = {
                                        key: values[key]
                                        for key in keys
                                        if type(values.get(key)) is bool
                                    }
                            completion["source_state"] = projection
            else:
                completion["reason"] = str(observation.get("error", "observation_unavailable"))[
                    :256
                ]
        except Exception as error:
            completion["reason"] = type(error).__name__
        finally:
            operation_wait_target.reset(token)
    result = {**value, "data": {**data, "completion": completion}}
    return type(response).model_validate(result) if isinstance(response, MCPResponse) else result
