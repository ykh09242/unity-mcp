"""Expose native Unity test discovery with explicit pagination controls."""

from typing import Annotated, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field, computed_field

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_tool
from services.resources.tests import PaginatedTestsData
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class TestDiscoveryPage(PaginatedTestsData):
    """Native page metadata with an explicit indication that this is a subset."""

    @computed_field(
        description="True when this page contains fewer items than totalCount, including the "
        "last or an empty page of a nonempty result set. Use hasMore/nextCursor to continue paging."
    )
    @property
    def partial(self) -> bool:
        """Whether the returned page contains fewer tests than the matching total."""
        return len(self.items) < self.totalCount


class TestDiscoveryResponse(MCPResponse):
    """Successful discovery responses retain the native page and test paths."""

    data: TestDiscoveryPage


@mcp_for_unity_tool(
    group="testing",
    # This wraps McpForUnityResource, which is absent from Unity's mutable tool catalog.
    # Routing still authenticates the caller and resolves their selected instance.
    unity_target=None,
    description="Lists Unity tests without running them. Returns one page with totalCount, "
    "nextCursor, hasMore and partial; pass nextCursor as cursor to read further pages. "
    "Omit mode to discover both EditMode and PlayMode tests.",
    annotations=ToolAnnotations(
        title="Get Tests",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def get_tests(
    ctx: Context,
    mode: Annotated[
        Literal["EditMode", "PlayMode"] | None,
        Field(description="Filter by test mode; omit to include both modes."),
    ] = None,
    name_pattern: Annotated[
        str | None,
        Field(description="Case-insensitive substring of the test name or full name (not regex)."),
    ] = None,
    page_size: Annotated[
        int, Field(ge=1, le=200, description="Maximum tests per page (1..200).")
    ] = 50,
    cursor: Annotated[
        int,
        Field(ge=0, le=2147483647, description="Zero-based offset; use the previous nextCursor."),
    ] = 0,
) -> TestDiscoveryResponse | MCPResponse:
    """Forward a single page request to native discovery without enumerating tests in Python."""
    unity_instance = await get_unity_instance_from_context(ctx)
    params = {"page_size": page_size, "cursor": cursor}
    if mode is not None:
        params["mode"] = mode
    if name_pattern is not None:
        params["filter"] = name_pattern
    response = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "get_tests", params
    )
    return parse_resource_response(response, TestDiscoveryResponse)
