from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from core.config import config
from models import MCPResponse
from models.response_lifetime import ResponsePollLifetime
from services.operation_permissions import remote_operation_allowed


def _in_pytest() -> bool:
    # Integration tests in this repo stub transports and do not run against a live Unity editor.
    # Preflight must be a no-op in that environment to avoid breaking the existing test suite.
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _busy(reason: str, retry_after_ms: int) -> MCPResponse:
    return MCPResponse(
        success=False,
        error="busy",
        message=reason,
        hint="retry",
        data={"reason": reason, "retry_after_ms": int(retry_after_ms)},
    )


def _unresponsive(data: dict[str, Any]) -> MCPResponse | None:
    diagnostics = data.get("diagnostics")
    if isinstance(diagnostics, dict) and diagnostics.get("status") == "unresponsive":
        return MCPResponse(
            success=False,
            error="editor_unresponsive",
            message="No recent editor heartbeat. Inspect Unity for dialogs, blocked work or a disconnected transport before retrying.",
            hint="inspect_editor",
            data={"diagnostics": diagnostics},
        )
    return None


async def preflight(
    ctx,
    *,
    requires_no_tests: bool = False,
    wait_for_no_compile: bool = False,
    refresh_if_dirty: bool = False,
    max_wait_s: float = 30.0,
) -> MCPResponse | None:
    """
    Server-side preflight guard used by tools so they behave safely even if the client never reads resources.

    Returns:
      - MCPResponse busy/retry payload when the tool should not proceed right now
      - None when the tool should proceed normally
    """
    if _in_pytest():
        return None

    # These are optional observations/refreshes. A test/edit/run grant does not
    # imply read, and read must never acquire importing/compilation authority.
    if not await remote_operation_allowed("get_editor_state", {}, ctx=ctx):
        return None
    if refresh_if_dirty and not await remote_operation_allowed("refresh_unity", {}, ctx=ctx):
        refresh_if_dirty = False

    async with ResponsePollLifetime() as observations:
        result = await _preflight(
            ctx,
            observations,
            requires_no_tests=requires_no_tests,
            wait_for_no_compile=wait_for_no_compile,
            refresh_if_dirty=refresh_if_dirty,
            max_wait_s=max_wait_s,
        )
        # Only an unresponsive diagnostic is derived from the last observation.
        # Keep its ownership through delivery; internal snapshots and local busy
        # replies can be discarded without touching sibling or refresh responses.
        if result is None or result.error != "editor_unresponsive":
            observations.discard()
        return result


async def _preflight(
    ctx,
    observations: ResponsePollLifetime,
    *,
    requires_no_tests: bool,
    wait_for_no_compile: bool,
    refresh_if_dirty: bool,
    max_wait_s: float,
) -> MCPResponse | None:
    if requires_no_tests and wait_for_no_compile:
        return await _preflight_test_run(ctx, observations, refresh_if_dirty, max_wait_s)

    # Load canonical editor state (server enriches advice + staleness).
    try:
        from services.resources.editor_state import get_editor_state_authoritative

        state_resp = await observations.fetch(get_editor_state_authoritative(ctx), timeout=None)
        state = state_resp.model_dump() if hasattr(state_resp, "model_dump") else state_resp
    except Exception:
        # If we cannot determine readiness, fall back to proceeding (tools already contain retry logic).
        return None

    if not isinstance(state, dict) or not state.get("success", False):
        # Unknown state; proceed rather than blocking (avoids false positives when Unity is reachable but status isn't).
        return None

    data = state.get("data")
    if not isinstance(data, dict):
        return None

    unresponsive = _unresponsive(data)
    if unresponsive is not None:
        return unresponsive

    # Tests running: fail fast before issuing an optional refresh.
    if requires_no_tests:
        tests = data.get("tests")
        if isinstance(tests, dict) and tests.get("is_running") is True:
            return _busy("tests_running", 5000)

    # Optional refresh-if-dirty
    if refresh_if_dirty:
        assets = data.get("assets")
        if isinstance(assets, dict) and assets.get("external_changes_dirty") is True:
            try:
                from services.tools.refresh_unity import refresh_unity

                # Refresh imports changed scripts itself. An explicit compile also
                # reloads the domain for ordinary asset changes or already-imported edits.
                refreshed = await refresh_unity(
                    ctx, mode="if_dirty", scope="all", compile="none", wait_for_ready=True
                )
                refresh_result = (
                    refreshed.model_dump() if hasattr(refreshed, "model_dump") else refreshed
                )
                if isinstance(refresh_result, dict) and refresh_result.get("success") is False:
                    # This diagnostic belongs to refresh, not the editor snapshot,
                    # even if it uses the same editor_unresponsive error code.
                    observations.discard()
                    return MCPResponse(**refresh_result)
                if (
                    not isinstance(refresh_result, dict)
                    or refresh_result.get("success") is not True
                ):
                    return MCPResponse(
                        success=False,
                        error="refresh_failed",
                        message="Preflight refresh did not confirm success.",
                    )
            except Exception as exc:
                return MCPResponse(
                    success=False,
                    error="refresh_failed",
                    message=f"Preflight refresh failed: {exc}",
                )

    # Compilation: optionally wait for a bounded time.
    if wait_for_no_compile:
        deadline = time.monotonic() + float(max_wait_s)
        while True:
            unresponsive = _unresponsive(data)
            if unresponsive is not None:
                return unresponsive
            # Tests may have started while compilation was being awaited.
            if requires_no_tests:
                tests = data.get("tests")
                if isinstance(tests, dict) and tests.get("is_running") is True:
                    return _busy("tests_running", 5000)
            compilation = data.get("compilation") if isinstance(data, dict) else None
            is_compiling = isinstance(compilation, dict) and compilation.get("is_compiling") is True
            is_domain_reload_pending = (
                isinstance(compilation, dict)
                and compilation.get("is_domain_reload_pending") is True
            )
            if not is_compiling and not is_domain_reload_pending:
                break
            if time.monotonic() >= deadline:
                return _busy("compiling", 500)
            await asyncio.sleep(0.25)

            # Refresh state for the next loop iteration.
            try:
                from services.resources.editor_state import get_editor_state_authoritative

                state_resp = await observations.fetch(
                    get_editor_state_authoritative(ctx), timeout=None
                )
                state = state_resp.model_dump() if hasattr(state_resp, "model_dump") else state_resp
                data = state.get("data") if isinstance(state, dict) else None
                if not isinstance(data, dict):
                    return None
            except Exception:
                return None

    # Staleness: if the snapshot is stale, proceed (tools will still run), but callers that read resources can back off.
    # In future we may make this strict for some tools.
    return None


async def _preflight_test_run(ctx, observations, refresh_if_dirty, max_wait_s):
    """Require a fresh, stable native-ready window before admitting a new test run."""
    from services.resources.editor_state import get_editor_state_authoritative

    deadline = time.monotonic() + max_wait_s
    stable_since = None
    stable_identity = None
    refreshed = False
    reason = "editor_state_unavailable"
    first = True
    while first or time.monotonic() < deadline:
        # A zero wait budget still performs one bounded observation, as before;
        # it disables subsequent polling rather than fabricating an unknown state.
        timeout = 1.0 if first and max_wait_s <= 0 else max(0.0, deadline - time.monotonic())
        first = False
        data = None
        try:
            response = await observations.fetch(
                get_editor_state_authoritative(ctx), timeout=timeout
            )
            value = response.model_dump() if hasattr(response, "model_dump") else response
            if isinstance(value, dict) and value.get("success") is True:
                data = value.get("data")
        except Exception:
            pass  # Reload may temporarily disconnect observation; it never grants admission.
        ready = False
        if isinstance(data, dict):
            unresponsive = _unresponsive(data)
            if unresponsive is not None:
                return unresponsive
            tests = data.get("tests") if isinstance(data.get("tests"), dict) else {}
            if tests.get("is_running") is True:
                return _busy("tests_running", 5000)
            compilation = data.get("compilation")
            assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
            advice = data.get("advice") if isinstance(data.get("advice"), dict) else {}
            blocking = advice.get("blocking_reasons") or []
            if "recovery_required" in blocking:
                return MCPResponse(
                    success=False,
                    error="recovery_required",
                    hint="inspect_editor",
                    data={"reason": "recovery_required", "retryable": False},
                )
            known = isinstance(compilation, dict) and all(
                type(compilation.get(key)) is bool
                for key in ("is_compiling", "is_domain_reload_pending")
            )
            ready = (
                known
                and not compilation["is_compiling"]
                and not compilation["is_domain_reload_pending"]
                and advice.get("ready_for_tools") is True
            )
            reason = (
                blocking[0]
                if isinstance(blocking, list) and blocking
                else "editor_state_unavailable"
            )
            coverage = assets.get("external_changes_scan_complete")
            # Hosted servers deliberately cannot traverse the remote Editor's disk.
            remote_coverage = config.http_remote_hosted and coverage is None
            if ready and coverage is not True and not remote_coverage:
                ready = False
                if assets.get("external_changes_scan_limited") is True:
                    reason = "external_changes_scan_limited"
                elif assets.get("external_changes_scan_in_progress") is True:
                    reason = "external_changes_scan_pending"
                else:
                    reason = "external_changes_scan_unknown"
                if reason != "external_changes_scan_pending":
                    return MCPResponse(
                        success=False,
                        error=reason,
                        message=(
                            "Test admission requires verified local scan coverage. Inspect the "
                            "project path, scan scope and filesystem access; resolve the reported "
                            "coverage issue before retrying. Waiting alone may not resolve it."
                        ),
                        hint="inspect_scan_scope",
                        data={
                            "reason": reason,
                            "retryable": False,
                            "next_action": "inspect_scan_scope",
                            "scan": {
                                key: value
                                for key, value in assets.items()
                                if key.startswith("external_changes_scan_")
                            },
                        },
                    )
            if ready and refresh_if_dirty and assets.get("external_changes_dirty") is True:
                if refreshed:
                    return _busy("external_changes_after_refresh", 500)
                from services.tools.refresh_unity import refresh_unity

                remaining = deadline - time.monotonic()
                if remaining < 1:
                    return _busy("external_changes_dirty", 500)
                try:
                    result = await refresh_unity(
                        ctx,
                        mode="if_dirty",
                        scope="all",
                        compile="none",
                        wait_for_ready=True,
                        timeout_seconds=remaining,
                    )
                    result = result.model_dump() if hasattr(result, "model_dump") else result
                    if not isinstance(result, dict) or result.get("success") is not True:
                        observations.discard()
                        return (
                            MCPResponse(**result)
                            if isinstance(result, dict)
                            else MCPResponse(
                                success=False,
                                error="refresh_failed",
                            )
                        )
                except Exception as exc:
                    return MCPResponse(success=False, error="refresh_failed", message=str(exc))
                refreshed = True
                stable_since = None
                # Always observe again: the snapshot preceding refresh cannot admit tests.
                continue
            if ready:
                latest = compilation.get("latest_job") or {}
                identity = (
                    (data.get("unity") or {}).get("instance_id"),
                    latest.get("session_id"),
                    latest.get("generation"),
                    compilation.get("last_compile_started_unix_ms"),
                    compilation.get("last_compile_finished_unix_ms"),
                    compilation.get("last_domain_reload_after_unix_ms"),
                )
                if stable_since is None or identity != stable_identity:
                    stable_since = time.monotonic()
                    stable_identity = identity
                elif time.monotonic() - stable_since >= 0.75:
                    return None
                reason = "editor_stabilizing"
        if not ready:
            stable_since = None
            stable_identity = None
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(0.25, remaining))
    return _busy(reason, 500)
