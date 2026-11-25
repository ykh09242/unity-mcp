from fastmcp import Context

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


async def _read_packages(ctx: Context, parameters: dict[str, str]) -> MCPResponse:
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "get_project_packages", parameters
    )
    return parse_resource_response(response, MCPResponse)


@mcp_for_unity_resource(
    uri="mcpforunity://project/packages",
    name="project_packages",
    description=(
        "List the IDs and installed versions of every package currently registered in the selected "
        "Unity Editor, including indirect dependencies and built-in packages. "
        "Read mcpforunity://project/packages/{package_name} for one package's registered metadata."
    ),
)
async def get_project_packages(ctx: Context) -> MCPResponse:
    return await _read_packages(ctx, {})


@mcp_for_unity_resource(
    uri="mcpforunity://project/packages/{package_name}",
    name="project_package_info",
    description=(
        "Read an installed package by its UPM name. Returns the registered version, display name, "
        "description, author, source and registry name and URL when available. "
        "Reads local Editor metadata without contacting the registry."
    ),
)
async def get_project_package_info(ctx: Context, package_name: str) -> MCPResponse:
    return await _read_packages(ctx, {"package": package_name})
