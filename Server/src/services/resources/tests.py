from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field

from fastmcp import Context

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


class TestItem(BaseModel):
    name: Annotated[str, Field(description="The name of the test.")]
    full_name: Annotated[str, Field(description="The full name of the test.")]
    path: Annotated[str | None, Field(description="The hierarchical path of the test.")] = None
    mode: Annotated[Literal["EditMode", "PlayMode"], Field(description="The mode the test is for.")]


class PaginatedTestsData(BaseModel):
    """Paginated test results."""

    items: list[TestItem] = Field(description="Tests on current page")
    cursor: int = Field(description="Current page cursor (0-based)")
    nextCursor: Optional[int] = Field(None, description="Next page cursor, null if last page")
    totalCount: int = Field(description="Total number of tests across all pages")
    pageSize: int = Field(description="Number of items per page")
    hasMore: bool = Field(description="Whether there are more items after this page")


class GetTestsResponse(MCPResponse):
    """Response containing paginated test data."""

    data: PaginatedTestsData = Field(description="Paginated test data")


@mcp_for_unity_resource(
    uri="mcpforunity://tests",
    name="get_tests",
    description="Provides the first page of Unity tests (default 50 items). "
    "For mode filtering, read mcpforunity://tests/{mode}. "
    "This resource does not expose further pages.\n\nURI: mcpforunity://tests",
)
async def get_tests(ctx: Context) -> GetTestsResponse | MCPResponse:
    """Provides a paginated list of all Unity tests.

    Returns the first page of tests using Unity's default pagination (50 items).
    Use mcpforunity://tests/{mode} for mode filtering. This resource does not
    expose name filtering or pagination controls.
    """
    unity_instance = await get_unity_instance_from_context(ctx)

    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_tests",
        {},
    )
    return parse_resource_response(response, GetTestsResponse)


@mcp_for_unity_resource(
    uri="mcpforunity://tests/{mode}",
    name="get_tests_for_mode",
    description="Provides the first page of tests for a specific mode (EditMode or PlayMode). "
    "This resource does not expose name filtering or further pages.\n\nURI: mcpforunity://tests/{mode}",
)
async def get_tests_for_mode(
    ctx: Context,
    mode: Annotated[
        Literal["EditMode", "PlayMode"],
        Field(description="The mode to filter tests by (EditMode or PlayMode)."),
    ],
) -> GetTestsResponse | MCPResponse:
    """Provides the first page of tests for a specific mode.

    Args:
        mode: The test mode to filter by (EditMode or PlayMode)

    Returns the first page of tests using Unity's default pagination (50 items).
    This resource does not expose name filtering or pagination controls.
    """
    unity_instance = await get_unity_instance_from_context(ctx)

    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_tests_for_mode",
        {"mode": mode},
    )
    return parse_resource_response(response, GetTestsResponse)
