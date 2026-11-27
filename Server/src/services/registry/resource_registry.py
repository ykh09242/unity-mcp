"""
Resource registry for auto-discovery of MCP resources.
"""

from typing import Callable, Any, Literal

# Global registry to collect decorated resources
_resource_registry: list[dict[str, Any]] = []


def mcp_for_unity_resource(
    uri: str,
    name: str | None = None,
    description: str | None = None,
    *,
    catalog_observation: bool = False,
    inventory_reader: str | None = None,
    read_scope: Literal["connection", "editor"] | None = None,
    **kwargs,
) -> Callable:
    """
    Decorator for registering MCP resources in the server's resources directory.

    Resources are registered in the global resource registry.

    Args:
        name: Resource name (defaults to function name)
        description: Resource description
        catalog_observation: Allow generic tool reads of this callback under its observation-only policy.
        inventory_reader: Fixed inventory operation allowed to read this registered callback.
        read_scope: Bind a tool read to the requesting connection or its selected Editor.
        **kwargs: Additional arguments passed to @mcp.resource()

    Example:
        @mcp_for_unity_resource("mcpforunity://resource", description="Gets something interesting")
        async def my_custom_resource(ctx: Context, ...):
            pass
    """

    def decorator(func: Callable) -> Callable:
        if read_scope == "connection" and (not inventory_reader or "{" in uri):
            raise ValueError(
                "A connection-scoped inventory needs a concrete URI and a fixed reader."
            )
        resource_name = name if name is not None else func.__name__
        _resource_registry.append(
            {
                "func": func,
                "uri": uri,
                "name": resource_name,
                "description": description,
                "catalog_observation": catalog_observation,
                "inventory_reader": inventory_reader,
                "read_scope": read_scope,
                "kwargs": kwargs,
            }
        )

        return func

    return decorator


def get_registered_resources() -> list[dict[str, Any]]:
    """Get all registered resources"""
    return _resource_registry.copy()


def clear_resource_registry():
    """Clear the resource registry (useful for testing)"""
    _resource_registry.clear()
