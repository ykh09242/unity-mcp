"""A sanitized doctor view over the existing generation-verified capability capture."""

import re

from fastmcp import Context
from pydantic import ValidationError

from core.server_build import RUNNING_SERVER
from models.doctor import (
    DoctorCheck,
    DoctorLimits,
    DoctorPackage,
    DoctorReceipt,
    DoctorServer,
    DoctorTarget,
)
from services.capabilities import _capture_project_capabilities

_VERSION = re.compile(r"[0-9][0-9A-Za-z+_.-]{0,79}\Z")
_COMMIT = re.compile(r"[0-9a-fA-F]{40}\Z")


def _version(value):
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else None


def _commit(value):
    return (
        value.lower()
        if isinstance(value, str) and _COMMIT.fullmatch(value) and set(value) != {"0"}
        else None
    )


def _package(raw) -> DoctorPackage:
    if not isinstance(raw, dict) or raw.get("name") != "com.ykh09242.unity-mcp":
        return DoctorPackage()
    if _version(raw.get("version")) is None:
        return DoctorPackage()
    kind = raw.get("effectiveSourceKind")
    if kind not in ("package_pin", "override_pin", "override_unpinned", "unavailable"):
        kind = "unavailable"
    return DoctorPackage(
        name="com.ykh09242.unity-mcp",
        version=_version(raw.get("version")),
        package_server_commit=_commit(raw.get("packageServerCommit")),
        effective_source_kind=kind,
        effective_server_commit=_commit(raw.get("effectiveServerCommit"))
        if kind in ("package_pin", "override_pin")
        else None,
        skill_revision=_commit(raw.get("skillRevision")),
    )


async def query_doctor(ctx: Context) -> DoctorReceipt:
    """Read once, retaining only bounded comparable evidence from the verified target."""
    capture = await _capture_project_capabilities(ctx)
    capabilities = capture.capabilities
    connection = capabilities.connection
    limits = capabilities.limits
    project = capture.observations["get_project_info"]
    state = capture.observations["get_editor_state"]
    package = (
        _package(project.data.get("mcpPackage"))
        if project.evidence.status == "observed"
        else DoctorPackage()
    )
    native_observed = any(item.evidence.status == "observed" for item in (state, project))
    connected = connection.status == "connected"
    reported_unity = state.data.get("unity")
    reported_target = (
        reported_unity.get("instance_id") if isinstance(reported_unity, dict) else None
    )
    wrong_target = (
        isinstance(reported_target, str)
        and bool(reported_target)
        and reported_target != connection.instance_id
    )
    verified = connected and native_observed and not wrong_target
    checks = {
        "target": DoctorCheck(
            status="passed" if connected else "unknown",
            reason="observed" if connected else "source_unavailable",
            next_action="none" if connected else "retry_later",
        ),
        "native_read": DoctorCheck(
            status="passed" if verified else "unknown",
            reason="observed" if verified else "source_unavailable",
            next_action="none" if verified else "retry_later",
        ),
        "source": DoctorCheck(
            status="unknown", reason="comparison_unknown", next_action="verify_selected_source"
        ),
        "readiness": DoctorCheck(
            status="unknown", reason="readiness_unknown", next_action="inspect_editor"
        ),
    }
    if connection.status == "unavailable":
        checks["target"] = DoctorCheck(
            status="failed", reason="unity_unavailable", next_action="reconnect_unity"
        )
    if connection.status == "selection_required":
        checks["target"] = DoctorCheck(
            status="failed", reason="selection_required", next_action="select_instance"
        )
    if connection.status == "auth_required":
        checks["target"] = DoctorCheck(
            status="failed", reason="auth_required", next_action="use_authenticated_client"
        )
    if connection.admission_available is False:
        checks["native_read"] = DoctorCheck(
            status="unknown", reason="capacity", next_action="retry_later"
        )
    if wrong_target:
        checks["target"] = DoctorCheck(
            status="failed", reason="wrong_target", next_action="select_instance"
        )
        package = DoctorPackage()
    comparison = "unknown"
    actual_commit = _commit(RUNNING_SERVER.source_commit)
    if verified and actual_commit and package.effective_server_commit:
        comparison = "matched" if actual_commit == package.effective_server_commit else "mismatch"
        checks["source"] = DoctorCheck(
            status="passed" if comparison == "matched" else "failed",
            reason="observed" if comparison == "matched" else "source_mismatch",
            next_action="none" if comparison == "matched" else "restart_client_server",
        )
    match capabilities.readiness.status:
        case "ready":
            checks["readiness"] = DoctorCheck(
                status="passed", reason="observed", next_action="none"
            )
        case "blocked":
            recovery_required = "recovery_required" in capabilities.readiness.blocking_reasons
            checks["readiness"] = DoctorCheck(
                status="not_checked",
                reason="recovery_required" if recovery_required else "readiness_blocked",
                next_action="inspect_native_work_then_restart_editor"
                if recovery_required
                else "inspect_editor",
            )
        case "unknown":
            pass
    request = getattr(ctx, "request_context", None)
    protocol = getattr(request, "protocol_version", None)
    if protocol not in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"):
        protocol = None
    try:
        target = DoctorTarget(
            instance_id=connection.instance_id,
            project_hash=connection.project_hash,
            generation=connection.generation,
            execution_environment=capabilities.target.execution_environment,
        )
    except ValidationError:
        target = DoctorTarget()
        verified = False
        package = DoctorPackage()
        comparison = "unknown"
        checks["target"] = DoctorCheck(
            status="unknown", reason="source_unavailable", next_action="retry_later"
        )
        checks["native_read"] = DoctorCheck(
            status="unknown", reason="source_unavailable", next_action="retry_later"
        )
        checks["source"] = DoctorCheck(
            status="unknown", reason="comparison_unknown", next_action="verify_selected_source"
        )
    return DoctorReceipt(
        observed_protocol=protocol,
        connection_verified=verified,
        server=DoctorServer(
            version=_version(RUNNING_SERVER.version) or "unknown",
            source_commit=actual_commit,
            server_id=RUNNING_SERVER.server_id,
        ),
        target=target,
        package=package,
        editor_version=_version(project.data.get("unityVersion"))
        if verified and project.evidence.status == "observed"
        else None,
        source_comparison=comparison,
        checks=checks,
        limits=DoctorLimits(
            query_timeout_seconds=limits.query_timeout_seconds,
            query_cleanup_grace_seconds=limits.query_cleanup_grace_seconds,
            query_max_elapsed_seconds=limits.query_max_elapsed_seconds,
        ),
    )
