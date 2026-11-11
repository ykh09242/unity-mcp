"""Exact-target, once-only native diagnostic dispatch with private job identities."""

from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from core.config import config
from models import MCPResponse
from models.script_quality import parse_quality_options
from services.registry.operation_target import capture_target
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


@dataclass
class QualityJob:
    owner: tuple[str, str]
    instance: str | None
    action: str
    options: dict[str, Any]
    created: float
    terminal: bool = False


class ScriptQualityService:
    """A server-lifetime store; native jobs are never replayed after reload or eviction."""

    def __init__(self):
        self.jobs: dict[str, QualityJob] = {}

    async def execute(self, ctx, action: str, options: dict | None):
        try:
            parsed = parse_quality_options(action, options).model_dump(exclude_none=True)
        except (ValueError, ValidationError) as error:
            return self.error("invalid_script_quality_request", str(error)[:2048])
        instance = await get_unity_instance_from_context(ctx)
        user_id = None
        if config.http_remote_hosted:
            from services.credential_lease import require_credential_lease

            user_id = (await require_credential_lease()).user_id
        target = await capture_target(instance, user_id=user_id)
        if target is None:
            return self.error("analysis_target_unavailable", "Select a connected Editor project.")
        if target.authorize_public is not None:
            check = await target.authorize_public(
                "manage_script_quality", {"action": action, "options": parsed}
            )
            check()
        if not target.is_current():
            return self.error(
                "analysis_target_changed", "The selected Editor connection changed before dispatch."
            )
        owner = target.scope.key
        job_id = parsed.get("job_id") or parsed.get("analysis_id")
        if action in {"status", "cancel", "impact"}:
            job = self.jobs.get(job_id)
            if job is None or job.owner != owner:
                return self.error(
                    "analysis_not_found",
                    "This analysis is unavailable in the authenticated project scope.",
                )
        if action == "impact":
            return self.error(
                "impact_unavailable",
                "Reference impact requires retained completed analysis evidence.",
            )
        if action in {"analyze", "capture_baseline"}:
            if len(self.jobs) >= 512:
                oldest = next((key for key, item in self.jobs.items() if item.terminal), None)
                if oldest is None:
                    return self.error(
                        "analysis_capacity_unavailable",
                        "Outstanding native jobs retain their ownership records.",
                    )
                del self.jobs[oldest]
            job_id = uuid4().hex
            self.jobs[job_id] = QualityJob(owner, instance, action, parsed, monotonic())
        params = {"action": action, **parsed}
        params.pop("baseline_id", None)
        if job_id is not None:
            params["job_id"] = job_id
        try:
            response = await send_with_unity_instance(
                async_send_command_with_retry,
                instance,
                "manage_script_quality",
                params,
                retry_on_reload=False,
            )
        except (TimeoutError, ConnectionError):
            return {
                **self.error(
                    "analysis_transport_unknown",
                    "The native outcome is unknown; query status with this job_id before starting again.",
                ),
                "job_id": job_id,
            }
        if isinstance(response, MCPResponse):
            response = response.model_dump(exclude_none=True)
        if job_id is not None and isinstance(response, dict):
            data = response.get("data")
            if isinstance(data, dict) and response.get("success"):
                data["job_id"] = job_id
                if data.get("terminal") is True:
                    self.jobs[job_id].terminal = True
            else:
                response = {**response, "job_id": job_id}
                if (
                    action in {"analyze", "capture_baseline"}
                    and response.get("error") == "script_quality_unavailable"
                    and isinstance(data, dict)
                    and data.get("reason")
                    in {
                        "analysis_capacity_unavailable",
                        "analysis_consent_required",
                        "analyzer_unavailable",
                    }
                ):
                    # Only a known pre-admission refusal proves no process exists.
                    self.jobs[job_id].terminal = True
        return response

    @staticmethod
    def error(code: str, message: str):
        return {"success": False, "code": code, "error": message}
