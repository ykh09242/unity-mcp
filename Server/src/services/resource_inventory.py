"""Tool bindings for resources published on the calling MCP connection."""

import json
from types import FunctionType
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

from fastmcp import Context, FastMCP
from fastmcp.exceptions import AuthorizationError, ToolError
from fastmcp.resources import FunctionResource, Resource, ResourceResult, ResourceTemplate
from fastmcp.resources.template import FunctionResourceTemplate, extract_query_params
from fastmcp.resources.types import TextResource
from fastmcp.server.providers.fastmcp_provider import (
    FastMCPProviderResource,
    FastMCPProviderResourceTemplate,
)
from fastmcp.server.middleware import MiddlewareContext
from mcp_types import ReadResourceRequestParams
from pydantic import Field, PrivateAttr

from services.tools import get_unity_instance_from_context
from transport.protocol import internal_resource_catalog, internal_resource_read


class PublishedSnapshotResource(Resource):
    """An application-owned snapshot whose listing projections retain their source identity."""

    _publication_id: UUID = PrivateAttr(default_factory=uuid4)
    content: str | bytes = Field(exclude=True, repr=False)

    async def read(self) -> str | bytes:
        return self.content


_observation_callbacks = WeakKeyDictionary()
CATALOG_READ = "manage_tools.read_resource"


def register_catalog_observation(
    server: FastMCP, uri: str, callback, *, readers=(CATALOG_READ,), **options
) -> None:
    """Record the SDK's exact serving callback at trusted application registration."""
    if "{" in uri:
        descriptor = FunctionResourceTemplate.from_function(callback, uri, **options)
        server.add_template(descriptor)
    else:
        descriptor = FunctionResource.from_function(callback, uri, **options)
        server.add_resource(descriptor)
    callbacks = _observation_callbacks.setdefault(server, WeakKeyDictionary())
    callbacks.setdefault(descriptor.fn, set()).update(
        (descriptor.key, reader) for reader in readers
    )


def catalog_observation(
    server: FastMCP, binding: Resource | ResourceTemplate, reader: str = CATALOG_READ
) -> bool:
    if reader == CATALOG_READ and type(binding) in (TextResource, PublishedSnapshotResource):
        return True
    return (
        type(binding) in (FunctionResource, FunctionResourceTemplate)
        and type(binding.fn) is FunctionType
        and (binding.key, reader) in _observation_callbacks.get(server, {}).get(binding.fn, ())
    )


def observation_uri_allowed(uri: str) -> bool:
    """Use the same expanded-URI permission floor as transport and receipt storage."""
    from services.operation_permissions import (
        OperationPermissionError,
        Permission,
        required_permissions,
    )

    try:
        grants = required_permissions("manage_tools", {"action": "read_resource", "uri": uri})
        return grants == {Permission.READ}
    except OperationPermissionError:
        return False


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
        return (
            selected._publication_id == listed._publication_id
            and selected.content == listed.content
        )
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
            if (
                selected is not None
                and catalog_observation(server, selected, tool)
                and any(same_resource_binding(selected, item) for item in listed)
            ):
                await server._check_resource_access(uri)
                continue
        except AuthorizationError:
            pass
        unavailable.add(tool)
    return unavailable


async def read_listed_resource(
    ctx: Context,
    uri: str,
    label: str,
    instance: str | None,
    *,
    allow_templates: bool = False,
    reader: str = CATALOG_READ,
) -> ResourceResult:
    """Apply the connection's listing and read policies inside the enclosing tool call."""
    from services.operation_permissions import authorize_remote_resource

    if not uri.startswith(("mcpforunity://", "skill://")) or "{" in uri or "}" in uri:
        raise AuthorizationError(f"{label.capitalize()} is unavailable")
    await authorize_remote_resource(uri)
    server = ctx.fastmcp
    with internal_resource_read(server, uri, instance):
        await server._check_resource_access(uri)
        return await _read_observation(server, uri, label, allow_templates, reader)


async def _listed_binding(
    server: FastMCP, uri: str, label: str, allow_templates: bool
) -> Resource | ResourceTemplate:
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
    if selected is None or not published:
        raise AuthorizationError(f"{label.capitalize()} is unavailable")
    return selected


async def _read_observation(
    server: FastMCP, uri: str, label: str, allow_templates: bool, reader: str
) -> ResourceResult:
    """Use native read middleware with the admitted descriptor pinned through dispatch."""
    native = "Use native resources/read for this resource or its requested options."
    if not observation_uri_allowed(uri):
        raise AuthorizationError(native)

    async def read_selected(context):
        if str(context.message.uri) != uri:
            raise AuthorizationError(native)
        selected = await _listed_binding(server, uri, label, allow_templates)
        if not catalog_observation(server, selected, reader):
            raise AuthorizationError(native)
        if isinstance(selected, Resource):
            return await selected._read()
        # The SDK ignores undeclared query options, fragments and repeated scalar
        # values. A tool adapter must not silently discard caller instructions.
        parts = urlsplit(uri)
        query = parse_qs(parts.query, keep_blank_values=True)
        if (
            parts.fragment
            or query.keys() - extract_query_params(selected.uri_template)
            or any(len(values) != 1 for values in query.values())
        ):
            raise AuthorizationError(native)
        params = selected.matches(uri)
        if params is None or params.get("evaluate_getters", "false") != "false":
            raise AuthorizationError(native)
        security = selected.resolve_security(server._resource_security)
        if security is not None and security.validate(params) is not None:
            raise AuthorizationError("Resource unavailable")
        return await selected._read(uri, params)

    async with Context(fastmcp=server) as read_ctx:
        context = MiddlewareContext(
            message=ReadResourceRequestParams(uri=uri),
            method="resources/read",
            fastmcp_context=read_ctx,
        )
        return await server._dispatch_component_middleware(context, read_selected)


async def read_inventory(
    ctx: Context, uri: str, label: str, *, reader: str, project_scoped: bool = True
) -> dict:
    instance = None
    if project_scoped:
        instance = await get_unity_instance_from_context(ctx)
        if not isinstance(instance, str) or not instance:
            raise ToolError(f"Select a Unity instance to read the {label}")
    result = await read_listed_resource(ctx, uri, label, instance, reader=reader)
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
