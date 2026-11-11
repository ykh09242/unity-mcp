"""Demand-driven source admission with one frozen Editor and observation deadline."""

import asyncio
import time
import uuid
from typing import Literal

from models.source_freshness import SourceFreshness, parse_freshness
from services.operation_permissions import remote_operation_allowed
from transport import unity_transport
from transport.legacy.unity_connection import async_send_command_with_retry


async def ensure_source_freshness(
    ctx,
    *,
    instance: str | None,
    deadline: float,
    refresh_policy: Literal["never", "if_needed"],
    before_dispatch=None,
) -> SourceFreshness:
    if refresh_policy not in ("never", "if_needed"):
        raise ValueError("refresh_policy must be never or if_needed")
    if not instance:
        return SourceFreshness(reason="exact_editor_required")
    compile_id = uuid.uuid4().hex
    identity = None
    imported = False
    compiled = False

    async def permitted(command, params):
        try:
            return await asyncio.wait_for(
                remote_operation_allowed(command, params, ctx=ctx),
                max(0, deadline - time.monotonic()),
            )
        except TimeoutError:
            return None

    async def send(command, params):
        if before_dispatch is not None:
            await before_dispatch(command, params)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        response = await asyncio.wait_for(
            unity_transport.send_with_unity_instance(
                async_send_command_with_retry,
                instance,
                command,
                params,
                retry_on_reload=False,
                _deadline=deadline,
            ),
            remaining,
        )
        return response.model_dump() if hasattr(response, "model_dump") else response

    while time.monotonic() < deadline:
        permission = await permitted("get_compile_job", {})
        if permission is None:
            return SourceFreshness(reason="observation_deadline")
        if not permission:
            return SourceFreshness(reason="read_permission_required")
        try:
            reply = await send(
                "get_compile_job", {"compile_id": compile_id, "observe_source": True}
            )
        except TimeoutError:
            return SourceFreshness(reason="observation_deadline")
        except Exception:
            # A disconnected observer does not authorize another compilation.
            return SourceFreshness(reason="observation_unavailable")
        if not isinstance(reply, dict) or reply.get("success") is not True:
            return SourceFreshness(reason="observation_unavailable")
        evidence = parse_freshness(reply.get("source_freshness"))
        pair = (evidence.project_id, evidence.session_id)
        if identity is not None and pair != identity:
            return SourceFreshness(reason="editor_identity_changed")
        if all(pair):
            identity = pair
        if evidence.status in ("observed_current", "compile_failed", "unavailable"):
            return evidence
        if evidence.status == "pending":
            await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
            continue
        # A deleted/renamed source can still appear in Unity's old imported
        # assembly inventory. One authorized import may repair that context.
        if refresh_policy == "never" or compiled or (imported and not evidence.coverage.complete):
            return evidence
        params = {
            "mode": "force",
            "scope": "scripts" if imported else "all",
            "compile": "request" if imported else "none",
            "wait_for_ready": False,
            "timeout_seconds": max(0.001, deadline - time.monotonic()),
        }
        if imported:
            params.update(force_recompile=True, compile_id=compile_id)
        permission = await permitted("refresh_unity", params)
        if permission is None:
            return SourceFreshness(reason="observation_deadline")
        if not permission:
            return SourceFreshness(reason="refresh_permission_required")
        try:
            reply = await send("refresh_unity", params)
        except Exception:
            return SourceFreshness(
                reason="refresh_outcome_unknown", compile_id=compile_id if imported else None
            )
        if not isinstance(reply, dict) or reply.get("success") is not True:
            return SourceFreshness(
                reason="refresh_not_confirmed", compile_id=compile_id if imported else None
            )
        if imported:
            compiled = True
        else:
            imported = True
    return SourceFreshness(reason="observation_deadline")
