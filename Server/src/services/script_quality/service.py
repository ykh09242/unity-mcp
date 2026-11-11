"""Exact-target, once-only native diagnostic dispatch with private job identities."""

import asyncio
from dataclasses import dataclass, field
import secrets
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from core.config import config
from models import MCPResponse
from models.script_quality import parse_quality_options
from services.script_quality.baselines import baseline_eligible, baseline_receipt
from services.script_quality.pages import collect_result, result_page, InconsistentResult
from services.script_quality.results import QualityResults, RetentionRefused
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
    retained: bool = False
    baseline_token: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class ScriptQualityService:
    """A server-lifetime store; native jobs are never replayed after reload or eviction."""

    def __init__(self):
        self.jobs: dict[str, QualityJob] = {}
        self.results = QualityResults()
        self._cursor_secret = secrets.token_bytes(32)

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
            if job.retained and action in {"status", "cancel"}:
                return self._page(job_id, job, parsed)
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
            try:
                self.results.reserve(job_id, owner)
            except RetentionRefused as error:
                return self.error(
                    str(error),
                    "The bounded evidence store has no capacity; settle outstanding jobs first.",
                )
            self.jobs[job_id] = QualityJob(owner, instance, action, parsed, monotonic())
            if parsed.get("baseline_id"):
                baseline = self.results.read(parsed["baseline_id"], owner, baseline=True)
                if baseline is not None:
                    self.jobs[job_id].baseline_token = baseline.get("source_token")
        params = {"action": action, **parsed}
        params.pop("baseline_id", None)
        if job_id is not None:
            params["job_id"] = job_id
        if action == "status":
            params.pop("cursor", None)
            params["page_size"] = 500
        deadline = monotonic() + 30
        try:
            response = await self._send(target, instance, params, deadline)
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
                if (
                    data.get("job_id", job_id) != job_id
                    or data.get("state") == "completed"
                    and data.get("job_id") != job_id
                ):
                    return self.error(
                        "analysis_result_inconsistent",
                        "The native receipt names a different or missing job identity.",
                    )
                data["job_id"] = job_id
                if data.get("terminal") is True and data.get("state") in {
                    "completed",
                    "failed",
                    "canceled",
                }:
                    self.jobs[job_id].terminal = True
                if (
                    action == "status"
                    and data.get("state") == "completed"
                    and data.get("terminal") is True
                ):
                    job = self.jobs[job_id]
                    async with job.lock:
                        if job.retained:
                            return self._page(job_id, job, parsed)
                        try:

                            async def fetch(cursor):
                                return await self._send(
                                    target,
                                    instance,
                                    {
                                        "action": "status",
                                        "job_id": job_id,
                                        "page_size": 500,
                                        "cursor": cursor,
                                    },
                                    deadline,
                                )

                            result = await collect_result(response, job_id, fetch)
                            self.results.complete(
                                job_id,
                                owner,
                                result,
                                baseline=job.action == "capture_baseline"
                                and baseline_eligible(result),
                            )
                            job.retained = True
                            job.options = {"baseline_id": job.options.get("baseline_id")}
                        except (
                            InconsistentResult,
                            RetentionRefused,
                            TimeoutError,
                            ConnectionError,
                        ):
                            self.results.settle(job_id, owner)
                            job.retained = True
                            return self.error(
                                "analysis_result_inconsistent",
                                "The completed native result could not be collected as one bounded source observation.",
                            )
                    return self._page(job_id, job, parsed)
                if self.jobs[job_id].terminal:
                    self.results.settle(job_id, owner)
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
                        "analysis_host_update_required",
                        "shipped_host_source_unavailable",
                        "host_files_missing",
                    }
                ):
                    # Only a known pre-admission refusal proves no process exists.
                    self.jobs[job_id].terminal = True
                    self.results.settle(job_id, owner)
        return response

    async def _send(self, target, instance, params, deadline):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError()
        check = None
        if target.authorize_public is not None:
            check = await target.authorize_public("manage_script_quality", params)
            check()
        if not target.is_current():
            raise InconsistentResult("analysis_target_changed")
        response = await asyncio.wait_for(
            send_with_unity_instance(
                async_send_command_with_retry,
                instance,
                "manage_script_quality",
                params,
                retry_on_reload=False,
                _deadline=deadline,
            ),
            max(0, deadline - monotonic()),
        )
        if check is not None:
            check()
        return (
            response.model_dump(exclude_none=True)
            if isinstance(response, MCPResponse)
            else response
        )

    def _page(self, identity, job, options):
        result = self.results.read(identity, job.owner)
        if result is None:
            return self.error(
                "analysis_expired_or_evicted",
                "The retained result is unavailable; this identifier will not re-fetch or rerun native work.",
            )
        baseline = None
        if job.baseline_token:
            baseline = self.results.read(job.options.get("baseline_id"), job.owner, baseline=True)
            if baseline is not None and baseline.get("source_token") != job.baseline_token:
                baseline = None
        try:
            page = result_page(
                result,
                baseline,
                identity=identity,
                owner=job.owner,
                secret=self._cursor_secret,
                size=options.get("page_size", 100),
                cursor=options.get("cursor"),
            )
        except InconsistentResult as error:
            return self.error(
                str(error),
                "The page cursor or retained result no longer matches this source and comparison view.",
            )
        data = {
            "job_id": identity,
            "analysis_id": identity,
            "state": "completed",
            "terminal": True,
            "result": page,
        }
        if job.action == "capture_baseline":
            receipt = baseline_receipt(identity, result)
            data.update(
                baseline_id=receipt["baseline_id"],
                baseline_timing=receipt["baseline_timing"],
                baseline_reason=receipt["reason"],
            )
        return {"success": True, "data": data}

    @staticmethod
    def error(code: str, message: str):
        return {"success": False, "code": code, "error": message}
