"""Capture observed Play Mode interactions into explicitly reviewable scenario drafts."""

from typing import Annotated

from fastmcp import Context
from mcp.types import ToolAnnotations
from models.models import MCPResponse
from models.play_scenarios import JobId, ScenarioName
from models.scenario_recording import (
    RecordingAction,
    RecordingDuration,
    RecordingMaxEvents,
    RecordingSceneScope,
    ScenarioRecordingCommand,
)
from pydantic import MISSING, JsonValue, ValidationError
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance
from typing_extensions import TypedDict


class ScenarioRecordingResponse(TypedDict, total=False):
    success: bool
    message: str
    error: str
    code: str
    hint: str
    data: JsonValue


@mcp_for_unity_tool(
    group="testing",
    description=(
        "Record observed uGUI Button pointer clicks and scene changes during unpaused Editor Play Mode. "
        "An enabled Input System observer captures delivered key/mouse/touch/wheel events from game "
        "Dynamic/Fixed updates; Old Input Handling uses read-only UnityEngine.Input observation in game Update. "
        "Status input_observation identifies source, frame clock and replay limits. Old-mode raw observation "
        "cannot replay into UnityEngine.Input: retain target-based click_ui for uGUI and review/remove raw "
        "steps before Old-mode replay. No input handling, focus or background settings are changed. "
        "Manual Input System update mode is unsupported. Observed holds and spacing are quantized "
        "to 1..36000 game updates, with diagnostics for simultaneous "
        "events and incomplete coverage; subframe timing and gameplay outcomes are not inferred. "
        "start returns recording_id immediately; status polls explicitly. stop saves a NEW scenario with "
        "draft=true and requires review before running; cancel discards it. max_events counts emitted clicks, "
        "raw input steps, transitions and requested checkpoints, plus one initial load_scene step "
        "(at most 128 total steps). checkpoint requests a named screenshot in the future replay, never an "
        "inferred assertion. Opt-in all_loaded captures a bounded initial scene context and exact "
        "scene-qualified clicks; transitions are observed waits, never inferred loading/unloading effects. "
        "UI Toolkit element-target recording remains manual; raw packets do not prove which handler consumed "
        "them. At least one supported observer is required: optional com.unity.ugui for Button clicks or "
        "Input System or Old Input Handling for raw observation. Pause, reload or Play Mode exit stops observation and retains "
        "a status snapshot; domain-reloaded sessions cannot be resumed or saved."
    ),
    annotations=ToolAnnotations(
        title="Manage Scenario Recording", readOnlyHint=False, destructiveHint=True
    ),
)
async def manage_scenario_recording(
    ctx: Context,
    action: Annotated[RecordingAction, "start, status, stop, cancel or checkpoint"],
    name: Annotated[
        ScenarioName | None,
        "NEW saved draft slug for start: 1-64 lowercase letters/digits, hyphens or underscores; "
        "start with a lowercase letter/digit, e.g. menu-flow-01 (pattern ^[a-z0-9][a-z0-9_-]{0,63}$)",
    ] = None,
    recording_id: Annotated[
        JobId | None, "Required for stop/cancel/checkpoint; optional current status filter"
    ] = None,
    max_events: Annotated[
        RecordingMaxEvents | None, "start event budget, excluding initial load_scene; default 31"
    ] = None,
    duration_seconds: Annotated[
        RecordingDuration | None, "start wall-clock limit in seconds; default 300"
    ] = None,
    scene_scope: Annotated[
        RecordingSceneScope | MISSING, "start-only scene scope: active (default) or all_loaded"
    ] = MISSING,
    checkpoint_name: Annotated[
        ScenarioName | None,
        "Screenshot checkpoint slug, required for checkpoint: 1-64 lowercase letters/digits, hyphens "
        "or underscores, starting with a letter/digit; e.g. menu-ready",
    ] = None,
) -> ScenarioRecordingResponse:
    try:
        command = ScenarioRecordingCommand(
            action=action,
            name=name,
            recording_id=recording_id,
            max_events=max_events,
            duration_seconds=duration_seconds,
            checkpoint_name=checkpoint_name,
            **({"scene_scope": scene_scope} if scene_scope is not MISSING else {}),
        )
    except ValidationError as error:
        result: ScenarioRecordingResponse = {
            "success": False,
            "error": f"Invalid scenario recording parameters: {error}",
        }
        if any(
            issue["type"] == "string_pattern_mismatch"
            and issue["loc"] in (("name",), ("checkpoint_name",))
            for issue in error.errors()
        ):
            result["hint"] = (
                "Use a 1-64 character lowercase slug matching ^[a-z0-9][a-z0-9_-]{0,63}$, "
                "for example menu-flow-01. Spaces, uppercase letters and localized names are not accepted."
            )
        return result
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_scenario_recording",
        command.wire_parameters(),
        retry_on_reload=False,
    )
    if isinstance(response, MCPResponse):
        return response.model_dump(exclude_none=True)
    return response
