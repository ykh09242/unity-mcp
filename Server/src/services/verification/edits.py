"""Compose confirmed source writes with bounded observation of existing verification jobs."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from services.registry.operation_context import active_operation
from services.verification.service import BoundContext, VerificationService, VerificationTarget

_deferred_edit = ContextVar("deferred_script_verification", default=None)
_edit_target = ContextVar("script_verification_target", default=None)


@contextmanager
def bind_edit_target(target):
    """Keep the admitted process identity without retaining its authority callbacks."""
    identity = VerificationTarget(target.scope, target.resume_generation) if target else None
    token = _edit_target.set(identity)
    try:
        yield
    finally:
        _edit_target.reset(token)


def after_operation(invoke):
    """Observe after the public mutation releases its native admission lease."""

    @wraps(invoke)
    async def completed(*args, **kwargs):
        pending = []
        token = _deferred_edit.set(pending)
        try:
            response = await invoke(*args, **kwargs)
        finally:
            _deferred_edit.reset(token)
        if len(pending) == 1:
            original, ctx, instance, paths, options = pending[0]
            # A later outer refusal must retain its uncertain outcome.
            if (
                isinstance(response, dict)
                and response.get("success") == original.get("success")
                and response.get("data") is original.get("data")
            ):
                return await complete_edit(ctx, instance, response, paths, **options)
        return response

    return completed


def verification_summary(response):
    """Keep the original run identity and a small actionable sample in an edit receipt."""
    data = response.get("data") or {}
    state = data.get("state")
    stages = data.get("stages", [])
    findings = []
    finding_count = 0
    observed_values = []
    observation_count = 0
    severity_counts = {"error": 0, "warning": 0, "info": 0}
    for stage in stages:
        evidence = stage.get("evidence") or {}
        stage_findings = evidence.get("findings", [])
        counts = evidence.get("active_severity_counts", {})
        for severity in severity_counts:
            severity_counts[severity] += counts.get(severity, 0)
        finding_count += max(len(stage_findings), sum(counts.values()))
        findings.extend(stage_findings[: 20 - len(findings)])
        if stage.get("state") == "failed" and not stage_findings:
            finding_count += 1
            if len(findings) < 20:
                findings.append(
                    {
                        "stage": stage.get("name"),
                        "severity": "error",
                        "reason": evidence.get("reason", "verification_stage_failed"),
                    }
                )
        values = (
            evidence.get("files", []) if stage.get("name", "").startswith("saved_source_") else []
        )
        observation_count += len(values)
        observed_values.extend(
            {"stage": stage.get("name"), **value} for value in values[: 20 - len(observed_values)]
        )
    status = {
        "passed": "issues_found" if findings else "passed",
        "failed": "issues_found",
        "running": "pending",
        "stale": "stale",
    }.get(state, "incomplete" if response.get("success") else "unavailable")
    if response.get("code") == "verification_editor_process_changed":
        status = "stale"
    complete = (
        state in {"passed", "failed"}
        and data.get("native_settled") is True
        and all(
            stage.get("state") not in {"incomplete", "stale", "canceled"}
            and (stage.get("evidence") or {}).get("complete") is not False
            for stage in stages
        )
    )
    result = {
        "status": status,
        "scope": "changed_source",
        "complete": complete,
        "observed_values": observed_values,
        "observed_values_count": observation_count,
        "observed_values_omitted": observation_count - len(observed_values),
        "observed_values_truncated": observation_count > len(observed_values),
        "findings": findings,
        "findings_count": finding_count,
        "findings_count_scope": "observed",
        "findings_omitted": finding_count - len(findings),
        "findings_truncated": finding_count > len(findings),
        "active_severity_counts": severity_counts,
        "diagnostic_counts_scope": "observed",
        "further_fixes_required": True
        if status == "issues_found"
        else False
        if complete and status == "passed"
        else None,
        "stages": [{"name": stage.get("name"), "state": stage.get("state")} for stage in stages],
    }
    if data.get("run_id"):
        result["run_id"] = data["run_id"]
        result["continuation"] = {
            "tool": "manage_verification",
            "action": "status",
            "options": {"run_id": data["run_id"]},
        }
    if response.get("success") is not True:
        result["reason"] = str(
            response.get("code") or response.get("error") or "verification_unavailable"
        )[:256]
    return result


async def complete_edit(
    ctx,
    instance,
    response,
    paths,
    *,
    mode="auto",
    deleted=False,
    expected_sources=None,
    original_target=None,
):
    if mode == "off" or not isinstance(response, dict):
        return response
    data = response.get("data") or {}
    if not isinstance(data, dict) or data.get("no_op") is True:
        return response
    confirmed = (
        bool(expected_sources) or data.get("source_saved") is True or data.get("applied") is True
    )
    if not confirmed and (
        response.get("success") is not True
        or data.get("source_saved") is False
        or data.get("applied") is False
    ):
        return response
    if expected_sources is None:
        identity = {
            key: data[key] for key in ("bytes_sha256", "sha256") if isinstance(data.get(key), str)
        }
        expected_sources = {path: {"absent": True} if deleted else dict(identity) for path in paths}
    verification = None
    try:
        owner = active_operation()
        original_target = original_target or _edit_target.get()
        if owner is not None:
            instance = owner.request.scope.instance
            if original_target is None or original_target.scope != owner.request.scope:
                raise ValueError("original_editor_identity_unavailable")
        pending = _deferred_edit.get()
        if pending is not None:
            if original_target is None:
                raise ValueError("original_editor_identity_unavailable")
            pending.append(
                (
                    response,
                    ctx,
                    instance,
                    paths,
                    {
                        "mode": mode,
                        "deleted": deleted,
                        "expected_sources": expected_sources,
                        "original_target": original_target,
                    },
                )
            )
            return response
        if not isinstance(instance, str) or not instance:
            raise ValueError("original_editor_identity_unavailable")
        service = getattr(ctx.fastmcp, "_verification_service", None)
        if not isinstance(service, VerificationService):
            service = VerificationService()
            ctx.fastmcp._verification_service = service
        initial = await asyncio.wait_for(
            service.start_edit(
                ctx,
                instance,
                paths=[] if deleted else paths,
                expected_sources=expected_sources,
                original_target=original_target,
            ),
            3,
        )
        verification = verification_summary(initial)
        run_id = (initial.get("data") or {}).get("run_id")
        task = service.tasks.get(run_id)
        if task is not None and owner is None:
            await asyncio.wait({task}, timeout=1)
            # Status authorizes this same selected project again. Expiry only ends observation.
            current = await asyncio.wait_for(
                service.execute(
                    BoundContext(ctx.fastmcp, instance, None), "status", {"run_id": run_id}
                ),
                1,
            )
            observed = verification_summary(current)
            if observed.get("run_id") == run_id:
                verification = observed
    except Exception as error:
        if verification is None:
            verification = verification_summary({"success": False, "code": type(error).__name__})
        else:
            verification["observation_reason"] = type(error).__name__
    return {**response, "data": {**data, "verification": verification}}
