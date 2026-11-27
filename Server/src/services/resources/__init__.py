"""
MCP Resources package - Auto-discovers and registers all resources in this directory.
"""

import functools
import logging
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel
from core.telemetry_decorator import telemetry_resource
from core.logging_decorator import log_execution

from services.registry import get_registered_resources
from utils.module_discovery import discover_modules

logger = logging.getLogger("mcp-for-unity-server")

# Export decorator for easy imports within tools
__all__ = ["register_all_resources"]


def _serialize_pydantic(func):
    """Wrap a resource function so Pydantic models are serialized to JSON strings.

    FastMCP 3.x expects resource functions to return str, bytes, or ResourceResult.
    Our resource functions return MCPResponse (a Pydantic BaseModel). This wrapper
    converts them to JSON strings automatically.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)
        if isinstance(result, BaseModel):
            return result.model_dump_json()
        if isinstance(result, dict):
            import json

            return json.dumps(result)
        return result

    return wrapper


def register_all_resources(mcp: FastMCP, *, project_scoped_tools: bool = True):
    """
    Auto-discover and register all resources in the resources/ directory.

    Any .py file in this directory or subdirectories with @mcp_for_unity_resource decorated
    functions will be automatically registered.
    """
    logger.info("Auto-discovering MCP for Unity Server resources...")
    # Dynamic import of all modules in this directory
    resources_dir = Path(__file__).parent

    # Discover and import all modules
    list(discover_modules(resources_dir, __package__))

    resources = get_registered_resources()

    if not resources:
        logger.warning("No MCP resources registered!")
        return

    registered_count = 0
    for resource_info in resources:
        func = resource_info["func"]
        uri = resource_info["uri"]
        resource_name = resource_info["name"]
        description = resource_info["description"]
        kwargs = resource_info["kwargs"]

        if not project_scoped_tools and resource_name == "custom_tools":
            logger.info(
                "Skipping custom_tools resource registration (project-scoped tools disabled)"
            )
            continue

        wrapped = _serialize_pydantic(func)
        wrapped = log_execution(resource_name, "Resource")(wrapped)
        wrapped = telemetry_resource(resource_name)(wrapped)
        options = dict(name=resource_name, description=description, **kwargs)
        if resource_info.get("catalog_observation") or resource_info.get("inventory_reader"):
            from services.resource_inventory import CATALOG_READ, register_catalog_observation

            readers = {CATALOG_READ} if resource_info.get("catalog_observation") else set()
            register_catalog_observation(
                mcp,
                uri,
                wrapped,
                readers=readers,
                inventory_reader=resource_info.get("inventory_reader"),
                read_scope=resource_info.get("read_scope"),
                **options,
            )
        else:
            mcp.resource(uri, **options)(wrapped)
        logger.debug(f"Registered resource: {resource_name} - {uri}")
        registered_count += 1

    logger.info(f"Registered {registered_count} MCP resources ({len(resources)} unique)")
