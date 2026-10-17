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
        "Record observed uGUI Button pointer clicks and scene changes during unpaused "
        "Editor Play Mode into a bounded scenario draft. start returns recording_id immediately; "
        "status polls explicitly. stop saves a NEW scenario with draft=true and requires review "
        "before running; cancel discards it. max_events counts clicks, transitions and explicitly "
        "requested checkpoints, plus one initial load_scene step (at most 32 total steps). "
        "checkpoint requests a named screenshot in the future replay, never an inferred assertion. "
        "Opt-in all_loaded captures a bounded initial scene context and exact scene-qualified clicks; "
        "transitions are observed waits, never inferred loading/unloading effects. "
        "No timing, expected game state, raw input, keyboard, drag, scroll or UI Toolkit steps are "
        "invented. Diagnostics identify ambiguous targets and incomplete coverage. The optional "
        "com.unity.ugui backend is required. Reload or Play Mode exit stops observation and retains "
        "a status snapshot; domain-reloaded sessions cannot be resumed or saved."
    ),
    annotations=ToolAnnotations(
        title="Manage Scenario Recording", readOnlyHint=False, destructiveHint=True
    ),
)
async def manage_scenario_recording(
    ctx: Context,
    action: Annotated[RecordingAction, "start, status, stop, cancel or checkpoint"],
    name: Annotated[ScenarioName | None, "NEW saved draft name for start"] = None,
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
        ScenarioName | None, "Explicit named screenshot checkpoint; required for checkpoint"
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
        return {"success": False, "error": f"Invalid scenario recording parameters: {error}"}
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
