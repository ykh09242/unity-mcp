"""Read immutable native test result exports in bounded UTF-8 chunks."""

from typing import Annotated, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError, model_validator

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class TestExportChunk(BaseModel):
    """One bounded immutable UTF-8 export segment, with no filesystem paths."""

    content: str = Field(max_length=65536)
    generation: str = Field(pattern=r"^[a-f0-9]{32}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    total_bytes: int = Field(strict=True, ge=0, le=32 * 1024 * 1024)
    mime_type: Literal["application/json", "application/xml"]
    has_more: bool = Field(strict=True)
    next_cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def bounded_utf8(self):
        size = len(self.content.encode("utf-8", errors="strict"))
        if size > 65536 or size > self.total_bytes or self.has_more != bool(self.next_cursor):
            raise ValueError("Invalid export chunk bounds")
        return self


class TestExportResponse(MCPResponse):
    data: TestExportChunk


@mcp_for_unity_tool(
    group="testing",
    description="Export completed Unity test evidence as JSON or JUnit. Returns one bounded UTF-8 chunk; pass next_cursor to continue. No remote output paths.",
    annotations=ToolAnnotations(
        title="Export Test Job",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def export_test_job(
    ctx: Context,
    job_id: Annotated[str, "Completed job id from run_tests"],
    format: Annotated[Literal["json", "junit"], "Export format"] = "json",
    cursor: Annotated[str | None, "Opaque next_cursor from the previous export chunk"] = None,
) -> TestExportResponse | MCPResponse:
    instance = await get_unity_instance_from_context(ctx)
    params = {"job_id": job_id, "format": format}
    if cursor is not None:
        params["cursor"] = cursor
    response = await send_with_unity_instance(
        async_send_command_with_retry, instance, "export_test_job", params
    )
    value = response.model_dump() if isinstance(response, MCPResponse) else response
    if not isinstance(value, dict):
        return MCPResponse(success=False, error="Invalid native test export response")
    try:
        if not value.get("success"):
            return (
                response if isinstance(response, MCPResponse) else MCPResponse.model_validate(value)
            )
        return TestExportResponse.model_validate(value)
    except (ValidationError, UnicodeError):
        return MCPResponse(success=False, error="Invalid native test export chunk")
