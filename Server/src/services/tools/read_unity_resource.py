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
        "For another published observation, use its tool binding. Expand a template with observed "
        "values and its fixed_uri_variables. For evaluate_getters=true, use native resources/read."
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
        "Omit for the packaged guide. Supply a catalog observation URI and use its tool binding's fixed variable values.",
    ] = None,
) -> dict:
    return await read_published_document(ctx, uri)
