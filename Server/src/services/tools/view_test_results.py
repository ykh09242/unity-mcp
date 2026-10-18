"""One canonical read, with an optional offline view and an ordinary text fallback."""

import json
from typing import Annotated

from fastmcp import Context
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.config import config
from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.test_results_app import TEST_RESULTS_URI
from services.tools import get_unity_instance_from_context
from services.tools.run_tests import GetTestJobResponse, RunTestsTestResult
from transport.legacy.unity_connection import async_send_command_with_retry
import transport.unity_transport as unity_transport


class AppResult(RunTestsTestResult):
    """Preserve native row truncation information and future additive fields."""

    model_config = ConfigDict(extra="allow", strict=True)
    truncated_fields: list[str]
    original_field_bytes: dict[str, Annotated[int, Field(ge=0)]]


class NativePage(BaseModel):
    """Validate the bounded native page before it can reach the view."""

    model_config = ConfigDict(extra="allow", strict=True)
    items: Annotated[list[AppResult], Field(max_length=100)]
    total_count: Annotated[int, Field(ge=0)]
    has_more: bool
    next_cursor: str | None
    generation: Annotated[str, Field(min_length=1)]


def _result(response: MCPResponse) -> ToolResult:
    """Always include meaningful plain text and the same structured envelope."""
    payload = response.model_dump(mode="json", exclude_none=True)
    return ToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=not response.success,
    )


@mcp_for_unity_tool(
    group="testing",
    unity_target="get_test_job",
    description=(
        "Read one completed Unity test-result page (up to 100 failed/skipped details). "
        "An Apps host may show a local filter and expandable details for this page only."
    ),
    annotations=ToolAnnotations(
        title="View Test Results",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    meta={"ui": {"resourceUri": TEST_RESULTS_URI, "visibility": ["model"]}},
)
async def view_test_results(
    ctx: Context,
    job_id: Annotated[str, Field(strict=True, min_length=1, max_length=256)],
) -> ToolResult:
    """Use the selected Editor's canonical read, without polling or focus nudges."""
    unity_instance = await get_unity_instance_from_context(ctx)
    user_id = await ctx.get_state("user_id") if config.http_remote_hosted else None
    raw = await unity_transport.send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_test_job",
        {"job_id": job_id, "includeFailedTests": True, "page_size": 100},
        user_id=user_id,
    )
    if isinstance(raw, MCPResponse):
        raw = raw.model_dump(mode="json")
    try:
        response = GetTestJobResponse.model_validate(raw, strict=True)
        if not response.success:
            return _result(response)
        data = response.data
        if (
            data is None
            or data.job_id != job_id
            or data.status not in {"succeeded", "failed"}
            or data.completion_kind != "completed"
            or data.result_availability != "available"
            or data.result is None
        ):
            return _result(
                MCPResponse(
                    success=False,
                    error="A completed matching test job with available results is required.",
                )
            )
        page = NativePage.model_validate(data.result_page)
        if page.total_count < len(page.items) or page.has_more != (page.next_cursor is not None):
            return _result(MCPResponse(success=False, error="Invalid test-result page envelope."))
        data.result_page = page.model_dump(mode="json")
        return _result(response)
    except ValidationError:
        return _result(MCPResponse(success=False, error="Invalid test-result response envelope."))
