"""Receive a parent agent's point-in-time observation on a separate MCP client."""

import time

from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError


def _has_identity(handoff):
    scene = handoff.get("active_scene")
    return (
        handoff.get("status") == "observed"
        and handoff.get("source") == "live_editor"
        and handoff.get("freshness") == "point_in_time"
        and isinstance(handoff.get("generation"), str)
        and bool(handoff["generation"])
        and type(handoff.get("observed_at_unix_ms")) is int
        and handoff["observed_at_unix_ms"] > 0
        and handoff.get("mode") in ("play", "edit")
        and isinstance(scene, dict)
        and all(isinstance(scene.get(key), str) and scene[key] for key in ("guid", "path"))
    )


async def receive_editor_handoff(call_tool, previous, *, instance=None):
    """Select and observe the destination on this client, forwarding the pending checks.

    Pass a trusted parent's data.handoff and the child's existing connected call_tool.
    A supplied instance must be the full intended target. Compare only the named identity
    fields. Object state, scene contents and runtime or saved outcomes still need evidence.
    """
    target = instance if instance is not None else previous.get("instance")
    if (
        not isinstance(target, str)
        or not target.rpartition("@")[0]
        or not target.rpartition("@")[2]
    ):
        raise ValueError("A full Name@hash is required for a receiving client")
    checks = previous.get("evidence_needed", [])
    if (
        not isinstance(checks, list)
        or len(checks) > 8
        or any(not isinstance(check, str) or not 0 < len(check) <= 256 for check in checks)
    ):
        raise ValueError("Invalid handoff evidence_needed")
    requested_at_ms = time.time_ns() // 1_000_000
    try:
        result = await call_tool(
            "set_active_instance",
            {"instance": target, "include_context": True, "evidence_needed": checks},
        )
    except (ToolError, MCPError) as error:
        message = str(error)
        if not (
            isinstance(error, MCPError)
            and error.data == {"reason": "permission_denied"}
            or message
            in (
                "Remote operation permission denied",
                "Remote authorization unavailable",
                "permission_denied",
            )
            or message.partition(":")[0] == "explicit_unity_target_unavailable"
            or message.startswith(f"Instance '{target}' not found. Available: ")
        ):
            raise
        result = {}
    received_at_ms = time.time_ns() // 1_000_000
    if not isinstance(result, dict):
        result = getattr(result, "structured_content", None)
    result = result if isinstance(result, dict) else {}
    data = result.get("data")
    current = data.get("handoff") if isinstance(data, dict) else None
    current = current if isinstance(current, dict) else {}
    if result.get("success") is not True or current.get("instance") != target:
        reason = "observation_unavailable"
    elif previous.get("instance") != target:
        reason = "instance_changed"
    elif not _has_identity(previous) or not _has_identity(current):
        reason = "incomplete_identity"
    elif previous["generation"] != current["generation"]:
        reason = "generation_changed"
    elif (
        current["observed_at_unix_ms"] <= previous["observed_at_unix_ms"]
        or not requested_at_ms <= current["observed_at_unix_ms"] <= received_at_ms
        or received_at_ms - current["observed_at_unix_ms"] > 2000
    ):
        reason = "observation_time_unconfirmed"
    elif any(
        previous["active_scene"].get(key) != current["active_scene"].get(key)
        for key in ("guid", "path", "name")
    ):
        reason = "scene_changed"
    elif previous["mode"] != current["mode"]:
        reason = "mode_changed"
    else:
        old_handle, new_handle = (
            item["active_scene"].get("handle") for item in (previous, current)
        )
        old_epoch, new_epoch = (
            item["active_scene"].get("lifetime_epoch") for item in (previous, current)
        )
        if (
            type(old_handle) is not int
            or type(new_handle) is not int
            or not old_handle
            or not new_handle
        ):
            reason = "same_scene_asset"
        elif old_handle != new_handle:
            reason = "scene_changed"
        elif not old_epoch or not new_epoch:
            reason = "same_scene_asset"
        elif old_epoch != new_epoch:
            reason = "scene_lifetime_unconfirmed"
        else:
            reason = "same_context"

    confirmed = {}
    if reason in ("same_context", "same_scene_asset"):
        confirmed = {key: current[key] for key in ("instance", "generation", "mode")}
        if reason == "same_context":
            confirmed["active_scene"] = current["active_scene"]
        else:
            confirmed["scene_asset"] = {
                key: current["active_scene"].get(key) for key in ("guid", "path", "name")
            }
        version = current.get("unity_version")
        if isinstance(version, str) and version and previous.get("unity_version") == version:
            confirmed["unity_version"] = version
    return {
        "comparison": reason,
        "confirmed": confirmed,
        "handoff": current,
        "evidence_needed": list(checks),
    }
