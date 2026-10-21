"""Project-scoped capability evidence using existing native status sources."""

from fastmcp import Context

from models.capabilities import ProjectCapabilitiesResponse
from services.capabilities import query_project_capabilities
from services.registry import mcp_for_unity_resource


@mcp_for_unity_resource(
    uri="mcpforunity://capabilities",
    name="project_capabilities",
    description=(
        "Current selected project's supported features, bounded limits, connection generation "
        "and recovery actions. Package presence and scene/runtime readiness are distinct. "
        "Unknown means unobserved; Player readiness requires existing bundle preflight. "
        "At admission capacity, connection and pending request counts reflect server transport "
        "records; native readiness and features stay unknown. Counts are not Unity's queue. "
        "One read budget includes resource selection; separately bounded transport cancellation "
        "grace is reported in limits. No completed query cache (TTL 0). "
        "URI: mcpforunity://capabilities"
    ),
)
async def get_capabilities(ctx: Context) -> ProjectCapabilitiesResponse:
    return ProjectCapabilitiesResponse(data=await query_project_capabilities(ctx))


@mcp_for_unity_resource(
    uri="mcpforunity://capabilities/scoped{?features,sources}",
    name="scoped_project_capabilities",
    description=(
        "Optional comma-separated feature/source selection; always reads current Editor state. "
        "Unrequested observations are unknown with reason not_requested; partial refers to the full view. "
        "Default full resource and doctor are unchanged. No completed-query cache. "
        "URI: mcpforunity://capabilities/scoped{?features,sources}"
    ),
)
async def get_scoped_capabilities(
    ctx: Context,
    features: str | None = None,
    sources: str | None = None,
) -> ProjectCapabilitiesResponse:
    return ProjectCapabilitiesResponse(
        data=await query_project_capabilities(
            ctx,
            features=tuple(features.split(",")) if features is not None else None,
            sources=tuple(sources.split(",")) if sources is not None else None,
        )
    )
