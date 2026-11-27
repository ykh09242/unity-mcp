"""Tool bindings for resources published on the calling MCP connection."""

import json
from uuid import UUID, uuid4

from fastmcp import Context
from fastmcp.exceptions import AuthorizationError, ToolError
from fastmcp.resources import FunctionResource, Resource, ResourceResult, ResourceTemplate
from fastmcp.resources.template import FunctionResourceTemplate
from fastmcp.resources.types import TextResource
from fastmcp.server.providers.fastmcp_provider import (
    FastMCPProviderResource,
    FastMCPProviderResourceTemplate,
)
from pydantic import PrivateAttr

from services.tools import get_unity_instance_from_context
from transport.protocol import internal_resource_catalog, internal_resource_read


class PublishedSnapshotResource(Resource):
    """An application-owned snapshot whose listing projections retain their source identity."""

    _publication_id: UUID = PrivateAttr(default_factory=uuid4)


def same_resource_binding(
    selected: Resource | ResourceTemplate | None, listed: Resource | ResourceTemplate
) -> bool:
    """Match the SDK's versioned identity and its serving function or mounted server."""
    if selected is listed:
        return True
    if (
        selected is None
        or type(selected) is not type(listed)
        or selected.key != listed.key
        or selected.auth is not listed.auth
    ):
        return False
    if isinstance(selected, PublishedSnapshotResource) and isinstance(
        listed, PublishedSnapshotResource
    ):
        return selected._publication_id == listed._publication_id
    if type(selected) is TextResource and isinstance(listed, TextResource):
        # The stock implementation reads only this immutable text, MIME type and item metadata.
        return (
            selected.text == listed.text
            and selected.mime_type == listed.mime_type
            and selected.meta == listed.meta
        )
    if type(selected) in (FunctionResource, FunctionResourceTemplate):
        return selected.fn is getattr(listed, "fn", None)
    if type(selected) in (FastMCPProviderResource, FastMCPProviderResourceTemplate):
        original = (
            "_original_uri"
            if type(selected) is FastMCPProviderResource
            else "_original_uri_template"
        )
        return (
            selected._server is not None
            and selected._server is getattr(listed, "_server", None)
            and getattr(selected, original) == getattr(listed, original)
        )
    # Static resources can share the same URI and version across providers.
    return False


async def unavailable_inventories(ctx: Context, bindings: dict[str, str]) -> set[str]:
    """List once per request and apply each URI's own read-access policy."""
    server = ctx.fastmcp
    try:
        with internal_resource_catalog(server):
            listed = await server.list_resources()
    except AuthorizationError:
        return set(bindings)
    unavailable = set()
    for tool, uri in bindings.items():
        try:
            selected = await server.get_resource(uri)
            if any(same_resource_binding(selected, item) for item in listed):
                await server._check_resource_access(uri)
                continue
        except AuthorizationError:
            pass
        unavailable.add(tool)
    return unavailable


async def read_listed_resource(
    ctx: Context, uri: str, label: str, instance: str | None, *, allow_templates: bool = False
) -> ResourceResult:
    """Apply the connection's listing and read policies inside the enclosing tool call."""
    from services.operation_permissions import authorize_remote_resource

    if not uri.startswith(("mcpforunity://", "skill://")) or "{" in uri or "}" in uri:
        raise AuthorizationError(f"{label.capitalize()} is unavailable")
    await authorize_remote_resource(uri)
    server = ctx.fastmcp
    # Use the same concrete-first resolution as resources/read. A hidden concrete
    # resource still owns its URI and cannot be served through a listed template.
    with internal_resource_catalog(server):
        selected, exists = await server._lookup_resource(uri)
        if exists:
            published = selected is not None and any(
                same_resource_binding(selected, listed) for listed in await server.list_resources()
            )
        elif allow_templates:
            selected = await server.get_resource_template(uri)
            published = selected is not None and any(
                same_resource_binding(selected, listed)
                for listed in await server.list_resource_templates()
            )
        else:
            published = False
    if not published:
        raise AuthorizationError(f"{label.capitalize()} is unavailable")
    with internal_resource_read(server, uri, instance):
        await server._check_resource_access(uri)
        return await server.read_resource(uri)


async def read_inventory(
    ctx: Context, uri: str, label: str, *, project_scoped: bool = True
) -> dict:
    instance = None
    if project_scoped:
        instance = await get_unity_instance_from_context(ctx)
        if not isinstance(instance, str) or not instance:
            raise ToolError(f"Select a Unity instance to read the {label}")
    result = await read_listed_resource(ctx, uri, label, instance)
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
