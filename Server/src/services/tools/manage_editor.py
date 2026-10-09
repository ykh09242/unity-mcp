from typing import Annotated, Any, Literal, assert_never
from uuid import uuid4

import anyio
from pydantic import Field, ValidationError

from fastmcp import Context
from mcp.types import ToolAnnotations
from models.editor_readiness import PlayReadinessJob
from models.response_lifetime import ResponsePollLifetime

from services.registry import mcp_for_unity_tool
from core.telemetry import is_telemetry_enabled, record_tool_usage
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


@mcp_for_unity_tool(
    description="Controls Unity editor state and settings. play optionally waits for scene_loaded or first_frame with a bounded persisted job. first_frame confirms simulation frame progress, not rendered pixels or application async initialization. get_play_mode_job polls; cancel_play_mode_job cancels monitoring without stopping Play Mode. get_game_view_size lists current size and presets for an open Game View. set_game_view_size selects fixed width/height, aspect_ratio W:H, or a preset label; restore_game_view_size uses the returned restore_token. Screenshots do not mutate Game View size. pause toggles pause/resume. Also supports telemetry_status, telemetry_ping, set_active_tool, add_tag, remove_tag, add_layer, remove_layer, deploy_package, restore_package, undo, redo. For prefab stages use manage_prefabs.",
    annotations=ToolAnnotations(
        title="Manage Editor",
        readOnlyHint=False,
        destructiveHint=True,
    ),
)
async def manage_editor(
    ctx: Context,
    action: Annotated[
        Literal[
            "telemetry_status",
            "telemetry_ping",
            "play",
            "pause",
            "stop",
            "set_active_tool",
            "add_tag",
            "remove_tag",
            "add_layer",
            "remove_layer",
            "deploy_package",
            "restore_package",
            "undo",
            "redo",
            "get_play_mode_job",
            "cancel_play_mode_job",
            "get_game_view_size",
            "set_game_view_size",
            "restore_game_view_size",
        ],
        "Editor operation",
    ],
    tool_name: Annotated[str, "Tool name when setting active tool"] | None = None,
    tag_name: Annotated[str, "Tag name when adding and removing tags"] | None = None,
    layer_name: Annotated[str, "Layer name when adding and removing layers"] | None = None,
    wait_until: Annotated[
        Literal["scene_loaded", "first_frame"] | None, "Optional bounded readiness wait for play"
    ] = None,
    timeout_seconds: Annotated[
        int, Field(ge=1, le=300, strict=True), "Play readiness deadline in seconds"
    ] = 30,
    job_id: Annotated[str | None, "Job ID for get_play_mode_job/cancel_play_mode_job"] = None,
    width: Annotated[
        int | None,
        Field(ge=1, le=8192, strict=True),
        "Fixed Game View width; combined limit 16 megapixels",
    ] = None,
    height: Annotated[
        int | None, Field(ge=1, le=8192, strict=True), "Fixed Game View height"
    ] = None,
    aspect_ratio: Annotated[
        str | None,
        Field(pattern=r"^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$"),
        "Game View aspect ratio W:H, components at most 8192",
    ] = None,
    preset: Annotated[str | None, "Unique preset label returned by get_game_view_size"] = None,
    restore_token: Annotated[
        str | None,
        "Token returned by set_game_view_size; retained for the latest 16 changes in this editor session",
    ] = None,
) -> dict[str, Any]:
    # Get active instance from request state (injected by middleware)
    unity_instance = await get_unity_instance_from_context(ctx)

    try:
        # Diagnostics: quick telemetry checks
        if action == "telemetry_status":
            return {"success": True, "telemetry_enabled": is_telemetry_enabled()}

        if action == "telemetry_ping":
            record_tool_usage("diagnostic_ping", True, 1.0, None)
            return {"success": True, "message": "telemetry ping queued"}

        # Prepare parameters, removing None values
        params = {
            "action": action,
            "toolName": tool_name,
            "tagName": tag_name,
            "layerName": layer_name,
            "job_id": job_id,
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
            "preset": preset,
            "restore_token": restore_token,
        }
        params = {k: v for k, v in params.items() if v is not None}

        if action == "play" and wait_until is not None:
            if not 1 <= timeout_seconds <= 300:
                return {"success": False, "error": "timeout_seconds must be between 1 and 300"}
            # Select the ID before dispatch so even a lost reload acknowledgement is recoverable.
            readiness_id = uuid4().hex
            params.update(
                wait_until=wait_until, timeout_seconds=timeout_seconds, job_id=readiness_id
            )
            response = data = job = None
            try:
                # The local deadline delivers the last response; external cancellation releases it.
                async with ResponsePollLifetime() as responses:
                    with anyio.move_on_after(timeout_seconds) as scope:
                        try:
                            response = await responses.fetch(
                                send_with_unity_instance(
                                    async_send_command_with_retry,
                                    unity_instance,
                                    "manage_editor",
                                    params,
                                    retry_on_reload=False,
                                ),
                                timeout=None,
                            )
                        except OSError as error:
                            responses.discard()
                            response = {"success": False, "error": str(error), "hint": "retry"}
                        while True:
                            if isinstance(response, dict):
                                data = response.get("data")
                                if response.get("success") is True and isinstance(data, dict):
                                    try:
                                        job = PlayReadinessJob.model_validate(data)
                                    except ValidationError:
                                        return {
                                            "success": False,
                                            "error": "invalid_play_readiness_response",
                                            "data": data,
                                        }
                                    if job.job_id != readiness_id:
                                        return {
                                            "success": False,
                                            "error": "play_readiness_job_mismatch",
                                            "data": data,
                                        }
                                    match job.status:
                                        case "succeeded":
                                            return response
                                        case "failed" | "cancelled" | "timed_out":
                                            return {
                                                **response,
                                                "success": False,
                                                "error": job.error or job.status,
                                            }
                                        case "running":
                                            pass
                                        case unreachable:
                                            assert_never(unreachable)
                                elif response.get("hint") != "retry" and not any(
                                    marker
                                    in str(
                                        response.get("error") or response.get("message") or ""
                                    ).lower()
                                    for marker in (
                                        "connection",
                                        "disconnect",
                                        "reload",
                                        "timeout",
                                        "timed out",
                                    )
                                ):
                                    return response
                            data = job = None
                            await anyio.sleep(0.1)
                            try:
                                response = await responses.fetch(
                                    send_with_unity_instance(
                                        async_send_command_with_retry,
                                        unity_instance,
                                        "manage_editor",
                                        {"action": "get_play_mode_job", "job_id": readiness_id},
                                    ),
                                    timeout=None,
                                )
                            except OSError as error:
                                responses.discard()
                                response = {"success": False, "error": str(error), "hint": "retry"}
                    if scope.cancel_called:
                        return {
                            "success": False,
                            "error": "play_readiness_timeout",
                            "data": {
                                "job_id": readiness_id,
                                "status": "wait_timed_out",
                                "wait_until": wait_until,
                                "timeout_seconds": timeout_seconds,
                                "last_response": response,
                            },
                        }
            finally:
                response = data = job = None

        # Send command using centralized retry helper with instance routing
        response = await send_with_unity_instance(
            async_send_command_with_retry, unity_instance, "manage_editor", params
        )

        # Preserve structured failure data; unwrap success into a friendlier shape
        if isinstance(response, dict) and response.get("success"):
            return {
                "success": True,
                "message": response.get("message", "Editor operation successful."),
                "data": response.get("data"),
            }
        return (
            response if isinstance(response, dict) else {"success": False, "message": str(response)}
        )

    except Exception as e:
        return {"success": False, "message": f"Python error managing editor: {str(e)}"}
