"""Save and run bounded Unity-owned Play Mode scenarios without Python polling."""

from typing import Annotated

from fastmcp import Context
from mcp.types import ToolAnnotations
from models.models import MCPResponse
from models.play_scenarios import (
    JobId,
    PlayScenario,
    PlayScenarioCommand,
    PlayScenarioSuite,
    RepeatCount,
    RunTimeout,
    ScenarioAction,
    ScenarioName,
)
from pydantic import JsonValue, ValidationError
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance
from typing_extensions import TypedDict


class PlayScenarioResponse(TypedDict, total=False):
    """Preserve Unity job state, per-step results and bounded failure logs."""

    success: bool
    message: str
    error: str
    code: str
    hint: str
    data: JsonValue


@mcp_for_unity_tool(
    group="testing",
    description=(
        "Save/get/list/delete scenarios and suites; reports history and run/status/cancel Unity-owned jobs. "
        "Suite actions use suite_ prefixes, suite_save takes suite and suite_status/cancel take suite_id. "
        "save takes a whole definition; get/delete/run take its name; reports accepts optional name. "
        "status/cancel require a 32-character lowercase hexadecimal job_id. "
        "run returns immediately; each repeat executes setup_steps, steps, then bounded cleanup_steps. "
        "Steps load_scene/wait_scene use Assets/... .unity paths; click_ui/wait_object use target_id or exact "
        "active-scene hierarchy paths. uGUI click_mode direct dispatches events; raycast verifies a visible hit. "
        "wait conditions support stability and object count/active/component/property. log_policy defaults "
        "strict; metrics are diagnostic, resources assert registered growth and screenshots/timeline opt-in. "
        "reset_state invokes registered reset_ids explicitly; query_budget bounds actual target searches. Tags select "
        "sequential suite children in one Editor. Unity polls; Python does not. End leaves Play "
        "unchanged; load_scene does not reset DontDestroyOnLoad or static state without explicit reset participants."
    ),
    annotations=ToolAnnotations(
        title="Manage Play Scenario", readOnlyHint=False, destructiveHint=True
    ),
)
async def manage_play_scenario(
    ctx: Context,
    action: Annotated[ScenarioAction, "save, get, list, delete, run, status, cancel or reports"],
    scenario: Annotated[PlayScenario | None, "Whole scenario definition for save"] = None,
    suite: Annotated[PlayScenarioSuite | None, "Whole suite definition for suite_save"] = None,
    suite_id: Annotated[
        JobId | None, "suite_status/suite_cancel ID; optional suite_run key"
    ] = None,
    source_revision: Annotated[
        str | None, "Optional caller-provided run revision label, at most 128 UTF-16 units"
    ] = None,
    name: Annotated[
        ScenarioName | None, "Saved name for get/delete/run; optional reports filter"
    ] = None,
    job_id: Annotated[JobId | None, "Required for status/cancel; optional run request key"] = None,
    repeat_count: Annotated[RepeatCount | None, "run repetitions, default 1"] = None,
    timeout_seconds: Annotated[
        RunTimeout | None, "run total timeout in seconds, default 300"
    ] = None,
) -> PlayScenarioResponse:
    """Validate once, resolve the selected instance once and dispatch without replay."""
    try:
        command = PlayScenarioCommand(
            action=action,
            scenario=scenario,
            suite=suite,
            suite_id=suite_id,
            source_revision=source_revision,
            name=name,
            job_id=job_id,
            repeat_count=repeat_count,
            timeout_seconds=timeout_seconds,
        )
    except ValidationError as exc:
        return {"success": False, "error": f"Invalid play scenario parameters: {exc}"}
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_play_scenario",
        command.wire_parameters(),
        retry_on_reload=False,
    )
    if isinstance(response, MCPResponse):
        return response.model_dump(exclude_none=True)
    return response
