"""Compose existing capability sources under one generation and read deadline."""

from dataclasses import dataclass
from hashlib import sha256
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
import logging
import math
from typing import Final

import anyio
from fastmcp import Context
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from core.config import config
from models.capabilities import (
    CapabilityConnection,
    CapabilityEvidence,
    CapabilityFeature,
    CapabilityLimits,
    CapabilityReadiness,
    CapabilityTarget,
    ProjectCapabilities,
)
from models.models import MCPResponse
from models.response_limits import MAX_RESPONSE_BYTES, MAX_RESPONSE_DEPTH, MAX_RESPONSE_NODES
from services.resources.editor_state import get_editor_state_authoritative
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import (
    async_send_command_with_retry,
    get_authenticated_stdio_generation,
)
from transport.plugin_hub import PluginHub
from transport.unity_transport import send_with_unity_instance

logger = logging.getLogger(__name__)
_QUERY_TIMEOUT_SECONDS: Final = 5.0
_read_deadline: ContextVar[float | None] = ContextVar("capability_read_deadline", default=None)
_SOURCES: Final = (
    "get_editor_state",
    "get_project_info",
    "manage_input",
    "manage_recording",
    "manage_play_scenario",
)
_PACKAGES: Final = ("ugui", "textmeshpro", "inputsystem", "uiToolkit", "screenCapture")
_JSON: Final = TypeAdapter(JsonValue)


def _query_timeout() -> float:
    return (
        min(_QUERY_TIMEOUT_SECONDS, float(PluginHub.COMMAND_TIMEOUT))
        if (config.transport_mode or "stdio").lower() == "http"
        else _QUERY_TIMEOUT_SECONDS
    )


@contextmanager
def capability_read_scope() -> Iterator[float]:
    """Include capability-specific resource selection in the query's original clock."""
    deadline = _read_deadline.get()
    if deadline is None:
        deadline = anyio.current_time() + _query_timeout()
    token = _read_deadline.set(deadline)
    try:
        yield deadline
    finally:
        _read_deadline.reset(token)


class _Observation(BaseModel):
    model_config = ConfigDict(frozen=True)
    evidence: CapabilityEvidence = Field(default_factory=CapabilityEvidence)
    data: dict[str, JsonValue] = Field(default_factory=dict)


def _section(data: dict[str, JsonValue], name: str) -> dict[str, JsonValue]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _boolean(value: JsonValue) -> bool | None:
    return value if type(value) is bool else None


def _native_limits(data: dict[str, JsonValue], names: tuple[str, ...]) -> dict[str, int | float]:
    return {
        name: value
        for name in names
        if type(value := data.get(name)) in (int, float)
        and 0 <= value <= 2_147_483_647
        and math.isfinite(value)
    }


def _observation(response: MCPResponse | JsonValue) -> _Observation:
    wire = _JSON.validate_python(
        response.model_dump() if isinstance(response, MCPResponse) else response
    )
    if isinstance(wire, dict):
        data = _section(wire, "data")
        if wire.get("success") is True and isinstance(wire.get("data"), dict):
            return _Observation(
                evidence=CapabilityEvidence(status="observed", next_action="none"), data=data
            )
        reason = data.get("reason")
        safe_reason = (
            reason
            if isinstance(reason, str)
            and reason
            in {
                "command_capacity",
                "result_capacity",
                "no_unity_session",
                "stale_connection",
                "command_deadline_exceeded",
                "outcome_unknown",
                "read_invalidated",
            }
            else "source_unavailable"
        )
        hint = wire.get("hint")
        return _Observation(
            evidence=CapabilityEvidence(
                reason=safe_reason,
                next_action=hint
                if hint in ("select_instance", "inspect_state_before_retry")
                else "retry_later",
            )
        )
    return _Observation(evidence=CapabilityEvidence(reason="invalid_source_response"))


async def _connection(instance: str | None, user_id: str | None) -> CapabilityConnection:
    if (config.transport_mode or "stdio").lower() == "http":
        return await PluginHub.get_capability_snapshot(instance, user_id=user_id)
    generation = await get_authenticated_stdio_generation(instance) if instance else None
    return CapabilityConnection(
        transport="stdio",
        status="connected" if generation else "unknown",
        instance_id=instance,
        generation=sha256(generation.encode()).hexdigest() if generation else None,
        reason=None if generation else "authenticated_generation_unavailable",
        next_action="none" if generation else "establish_authenticated_unity_connection",
    )


async def _read_source(
    name: str,
    ctx: Context,
    connection: CapabilityConnection,
    user_id: str | None,
    deadline: float,
    observations: dict[str, _Observation],
) -> None:
    try:
        if name == "get_editor_state":
            response = await get_editor_state_authoritative(ctx, command_deadline=deadline)
        else:
            params = (
                {"action": "status"}
                if name == "manage_input"
                else {"action": "capabilities"}
                if name == "manage_recording"
                else {"action": "list"}
                if name == "manage_play_scenario"
                else {}
            )
            response = await send_with_unity_instance(
                async_send_command_with_retry,
                connection.instance_id,
                name,
                params,
                user_id=user_id,
                retry_on_reload=False,
                **({"_deadline": deadline} if connection.transport == "stdio" else {}),
            )
        observations[name] = _observation(response)
    except (OSError, RuntimeError, ValueError):
        logger.debug("Capability source unavailable: %s", name)
        observations[name] = _Observation(evidence=CapabilityEvidence(reason="source_unavailable"))


def _compose(
    connection: CapabilityConnection,
    limits: CapabilityLimits,
    observations: dict[str, _Observation],
) -> ProjectCapabilities:
    state = observations["get_editor_state"].data
    unity = _section(state, "unity")
    platform = unity.get("platform")
    environment = (
        "editor" if isinstance(platform, str) and platform.endswith("Editor") else "unknown"
    )
    advice = _section(state, "advice")
    ready = _boolean(advice.get("ready_for_tools"))
    # Canonical advice cannot prove readiness when an old peer omitted its observations.
    if ready is True and any(
        _boolean(_section(state, section).get(field)) is None
        for section, field in (
            ("compilation", "is_compiling"),
            ("assets", "is_updating"),
            ("tests", "is_running"),
        )
    ):
        ready = None
    blocking = advice.get("blocking_reasons")
    reasons = (
        [item for item in blocking if isinstance(item, str)][:16]
        if isinstance(blocking, list)
        else []
    )
    action = advice.get("recommended_next_action")
    readiness = CapabilityReadiness(
        status="ready" if ready is True else "blocked" if ready is False else "unknown",
        blocking_reasons=reasons,
        next_action=action
        if isinstance(action, str)
        and action in {"none", "retry_later", "inspect_editor"}
        and ready is not None
        else "read_editor_state",
    )
    project = observations["get_project_info"].data
    packages = _section(project, "packages")
    inputs = observations["manage_input"].data
    input_supported = _boolean(inputs.get("input_system"))
    playing, paused = _boolean(inputs.get("playing")), _boolean(inputs.get("paused"))
    input_ready = (
        False
        if input_supported is False or ready is False or playing is False or paused is True
        else True
        if input_supported is True and ready is True and playing is True and paused is False
        else None
    )
    recording = observations["manage_recording"].data
    recording_supported = _boolean(recording.get("supported"))
    scenarios = observations["manage_play_scenario"].evidence.status == "observed"
    features = {
        "input": CapabilityFeature(
            supported=input_supported,
            ready=input_ready,
            source="manage_input/status",
            next_action="configure_input_system_backend"
            if input_supported is False
            else "start_play_mode"
            if playing is False
            else "resume_play_mode"
            if paused is True
            else "none"
            if input_ready is True
            else "read_input_status",
            limits=_native_limits(
                inputs, ("max_frames", "max_touch_contacts", "device_timeout_seconds")
            ),
        ),
        "ui_click": CapabilityFeature(
            supported=_boolean(inputs.get("ui_click")),
            ready=False if inputs.get("ui_click") is False else None,
            source="manage_input/status",
            next_action="inspect_active_scene_targets",
        ),
        "recording": CapabilityFeature(
            supported=recording_supported,
            ready=False if recording_supported is False else None,
            source="manage_recording/capabilities",
            next_action="use_interactive_supported_editor"
            if recording_supported is False
            else "verify_capture_view_and_encoder_at_start",
            limits=_native_limits(
                recording, ("max_fps", "max_duration_seconds", "max_dimension", "max_pixel_frames")
            ),
        ),
        "play_scenario_editor": CapabilityFeature(
            supported=True if scenarios else None,
            source="manage_play_scenario/list",
            next_action="validate_selected_scenario_with_existing_preflight",
        ),
        "play_scenario_player": CapabilityFeature(
            source="player_bundle_preflight",
            next_action="validate_player_bundle_with_existing_preflight",
        ),
    }
    return ProjectCapabilities(
        partial=any(item.evidence.status != "observed" for item in observations.values()),
        target=CapabilityTarget(
            instance_id=connection.instance_id,
            project_hash=connection.project_hash,
            execution_environment=environment,
            headless=_boolean(unity.get("is_batch_mode")),
        ),
        connection=connection,
        limits=limits,
        readiness=readiness,
        packages={name: _boolean(packages.get(name)) for name in _PACKAGES},
        features=features,
        evidence={name: item.evidence for name, item in observations.items()},
    )


@dataclass(frozen=True, slots=True)
class _CapabilityCapture:
    capabilities: ProjectCapabilities
    observations: dict[str, _Observation]


async def _capture_project_capabilities(ctx: Context) -> _CapabilityCapture:
    """Read current evidence once; discard observations if connection ownership changes."""
    http = (config.transport_mode or "stdio").lower() == "http"
    timeout = _query_timeout()
    deadline = _read_deadline.get()
    if deadline is None:
        deadline = anyio.current_time() + timeout
    cleanup_grace = float(PluginHub.CANCEL_TIMEOUT) if http else 0.0
    limits = CapabilityLimits(
        query_timeout_seconds=timeout,
        query_cleanup_grace_seconds=cleanup_grace,
        query_max_elapsed_seconds=timeout + cleanup_grace,
        response_bytes=MAX_RESPONSE_BYTES,
        response_depth=MAX_RESPONSE_DEPTH,
        response_nodes=MAX_RESPONSE_NODES,
        command_payload_bytes=PluginHub.MAX_COMMAND_PAYLOAD_BYTES if http else None,
        pending_per_session=PluginHub.MAX_PENDING_PER_SESSION if http else None,
        pending_per_user=PluginHub.MAX_PENDING_PER_USER if http else None,
        pending_global=PluginHub.MAX_PENDING_COMMANDS if http else None,
    )
    observations = {
        name: _Observation(evidence=CapabilityEvidence(reason="not_observed")) for name in _SOURCES
    }
    connection = CapabilityConnection(
        transport="http" if http else "stdio",
        status="unknown",
        reason="query_deadline",
        next_action="retry_later",
    )
    verified = False
    captured = False
    user_id = instance = None
    # The existing HTTP transport may shield command cancellation for CANCEL_TIMEOUT.
    # Advertise this separate grace; it grants no additional native probe execution.
    with anyio.move_on_at(deadline + cleanup_grace):
        with anyio.move_on_at(deadline):
            user_id = await ctx.get_state("user_id") if config.http_remote_hosted else None
            instance = await get_unity_instance_from_context(ctx)
            connection = await _connection(instance, user_id)
            captured = True
            if connection.status == "connected" and connection.admission_available is False:
                observations = {
                    name: _Observation(evidence=CapabilityEvidence(reason="command_capacity"))
                    for name in _SOURCES
                }
            if connection.status == "connected" and connection.admission_available is not False:
                remaining = max(0.0, deadline - anyio.current_time())
                # Leave a bounded tail for final identity verification after cleanup.
                verification_reserve = min(0.5, remaining * 0.5)
                probe_deadline = deadline - verification_reserve
                with anyio.move_on_at(probe_deadline):
                    async with anyio.create_task_group() as tasks:
                        for name in _SOURCES:
                            tasks.start_soon(
                                _read_source,
                                name,
                                ctx,
                                connection,
                                user_id,
                                probe_deadline,
                                observations,
                            )
        if (
            captured
            and connection.status == "connected"
            and connection.admission_available is not False
        ):
            current = await _connection(instance, user_id)
            current_user = await ctx.get_state("user_id") if config.http_remote_hosted else None
            verified = (
                current_user == user_id
                and current.status == "connected"
                and current.generation == connection.generation
                and current.instance_id == connection.instance_id
                and current.project_hash == connection.project_hash
            )
            connection = (
                current
                if verified
                else connection.model_copy(
                    update={
                        "status": "unknown",
                        "generation": None,
                        "reason": "connection_changed",
                        "next_action": "retry_later",
                    }
                )
            )
        elif captured:
            verified = True
    if not verified:
        observations = {
            name: _Observation(evidence=CapabilityEvidence(reason="connection_changed_or_deadline"))
            for name in _SOURCES
        }
        connection = connection.model_copy(
            update={
                "status": "unknown",
                "instance_id": None,
                "project_hash": None,
                "generation": None,
                "negotiated": None,
                "admission_available": None,
                "pending_commands": None,
                "tenant_pending_commands": None,
                "reason": "connection_changed_or_deadline",
                "next_action": "retry_later",
            }
        )
    return _CapabilityCapture(_compose(connection, limits, observations), observations)


async def query_project_capabilities(ctx: Context) -> ProjectCapabilities:
    """Preserve the original capability view over one verified capture."""
    return (await _capture_project_capabilities(ctx)).capabilities
