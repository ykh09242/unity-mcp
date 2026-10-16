"""Explicit native cancellation intent; a receipt is not completed termination."""

from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class CancelTestJobData(BaseModel):
    job_id: str = Field(max_length=256)
    status: str = Field(max_length=64)
    native_control: dict[str, Any]


class CancelTestJobResponse(MCPResponse):
    data: CancelTestJobData


@mcp_for_unity_tool(
    group="testing",
    description="Request cancellation of an owned native test job. Acceptance is intent; poll native_control for confirmed framework scheduler termination after task-cleanup attempts. User iterator finally, NUnit TearDown and UnityTearDown are not guaranteed. Unsupported versions reject without cancellation.",
    annotations=ToolAnnotations(
        title="Cancel Test Job",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def cancel_test_job(
    ctx: Context,
    job_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$"), "Job ID returned by run_tests"],
) -> CancelTestJobResponse | MCPResponse:
    instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry, instance, "cancel_test_job", {"job_id": job_id}
    )
    value = response.model_dump() if isinstance(response, MCPResponse) else response
    if not isinstance(value, dict):
        return MCPResponse(success=False, error="Invalid native test cancellation response")
    try:
        return (
            CancelTestJobResponse.model_validate(value)
            if value.get("success")
            else MCPResponse.model_validate(value)
        )
    except (ValidationError, UnicodeError):
        return MCPResponse(success=False, error="Invalid native test cancellation response")
