"""Read-only tool bindings for concrete resource inventories."""

import json

from fastmcp import Context
from fastmcp.exceptions import AuthorizationError, ToolError

from services.tools import get_unity_instance_from_context
from transport.protocol import internal_resource_catalog, internal_resource_read


async def unavailable_inventories(ctx: Context, bindings: dict[str, str]) -> set[str]:
    """List once per request and apply each URI's own read-access policy."""
    server = ctx.fastmcp
    try:
        with internal_resource_catalog(server):
            listed = {str(resource.uri) for resource in await server.list_resources()}
    except AuthorizationError:
        return set(bindings)
    unavailable = set()
    for tool, uri in bindings.items():
        try:
            if uri in listed and await server.get_resource(uri) is not None:
                await server._check_resource_access(uri)
                continue
        except AuthorizationError:
            pass
        unavailable.add(tool)
    return unavailable


async def read_inventory(
    ctx: Context, uri: str, label: str, *, project_scoped: bool = True
) -> dict:
    instance = None
    if project_scoped:
        instance = await get_unity_instance_from_context(ctx)
        if not isinstance(instance, str) or not instance:
            raise ToolError(f"Select a Unity instance to read the {label}")
    if await unavailable_inventories(ctx, {label: uri}):
        raise AuthorizationError(f"{label.capitalize()} is unavailable")
    with internal_resource_read(ctx.fastmcp, uri, instance):
        result = await ctx.fastmcp.read_resource(uri)
    message = f"{label.capitalize()} did not return a single JSON object"
    if len(result.contents) != 1 or not isinstance(result.contents[0].content, str):
        raise ToolError(message)
    try:
        payload = json.loads(result.contents[0].content)
    except (ValueError, RecursionError) as error:
        raise ToolError(message) from error
    if not isinstance(payload, dict):
        raise ToolError(message)
    return payload
