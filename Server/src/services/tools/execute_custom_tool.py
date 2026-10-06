from typing import Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from models.models import MCPResponse

from services.custom_tool_service import (
    CustomToolService,
    get_user_id_from_context,
    resolve_project_id_for_unity_instance,
)
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context


@mcp_for_unity_tool(
    name="execute_custom_tool",
    unity_target=None,
    group=None,
    description="Execute a project-scoped custom tool registered by Unity.",
    annotations=ToolAnnotations(
        title="Execute Custom Tool",
        destructiveHint=True,
    ),
)
async def execute_custom_tool(ctx: Context, tool_name: str, parameters: dict[str, Any] | None = None) -> MCPResponse:
    if not isinstance(tool_name, str) or not tool_name.strip():
        return MCPResponse(success=False, message="tool_name must be a nonempty string")
    # Reject malformed outer arguments before discovering a project/catalog.
    if parameters is None:
        parameters = {}
    elif not isinstance(parameters, dict):
        return MCPResponse(success=False, message="parameters must be an object/dictionary")

    unity_instance = await get_unity_instance_from_context(ctx)
    if not unity_instance:
        return MCPResponse(
            success=False,
            message="No active Unity instance. Call set_active_instance with Name@hash from mcpforunity://instances.",
        )

    project_id = resolve_project_id_for_unity_instance(unity_instance)
    if project_id is None:
        return MCPResponse(
            success=False,
            message=f"Could not resolve project id for {unity_instance}. Ensure Unity is running and reachable.",
        )

    service = CustomToolService.get_instance()
    user_id = await get_user_id_from_context(ctx)
    return await service.execute_tool(
        project_id,
        tool_name,
        unity_instance,
        parameters,
        user_id=user_id,
    )
