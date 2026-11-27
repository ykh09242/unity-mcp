"""Read a published observation through the serving MCP connection."""

from typing import Annotated

from fastmcp import Context
from mcp_types import ToolAnnotations
from pydantic import Field

from services.registry import mcp_for_unity_tool
from services.resource_inventory import read_published_document


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description=(
        "Read the packaged Unity MCP guide on this connection with no arguments. "
        "For another published observation, pass the concrete URI from its catalog binding "
        "or expand its URI template with observed values. Getter evaluation requires native resources/read."
    ),
    annotations=ToolAnnotations(
        title="Read Unity Resource",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def read_unity_resource(
    ctx: Context,
    uri: Annotated[
        str | None,
        Field(min_length=1, max_length=4096, pattern=r"^(mcpforunity|skill)://[^{}]+$"),
        "Omit to read the packaged guide, or supply a concrete or expanded catalog observation URI.",
    ] = None,
) -> dict:
    return await read_published_document(ctx, uri)
