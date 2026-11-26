"""Configured discovery profiles and bounded, project-bound modern selection."""

import asyncio
import hashlib
import json
import os
import time
from typing import Any, Literal, TypedDict

from fastmcp.server.dependencies import get_context
from fastmcp.server.transforms import Transform, Visibility
from fastmcp.tools import FunctionTool, ToolResult
from mcp.types import ResourceLink, ToolListChangedNotification
from mcp_types.version import is_version_at_least

from core.sdk_compatibility import is_reviewed_sdk
from services.registry import DEFAULT_ENABLED_GROUPS, TOOL_GROUPS
from transport.protocol import is_sessionless


class SearchResultTool(FunctionTool):
    """Keep the SDK's JSON result and link concrete reads on the requested surfaces."""

    def convert_result(self, raw_value: Any) -> ToolResult:
        result = super().convert_result(raw_value)
        if (
            not isinstance(raw_value, dict)
            or raw_value.get("scope") != "mcp_context"
            or not raw_value.get("route_groups")
        ):
            return result
        request = get_context().request_context
        if request is None or not is_version_at_least(request.protocol_version, "2025-06-18"):
            return result
        linked = set()
        for group in raw_value.get("route_groups", ()):
            for route in (group["representative"], *group["alternatives"]):
                if (
                    route["kind"] == "resource"
                    and route["advertised"]
                    and route["uri"] not in linked
                ):
                    result.content.append(
                        ResourceLink(type="resource_link", uri=route["uri"], name=route["name"])
                    )
                    linked.add(route["uri"])
        return result


CLIENT_INVENTORY_GUIDANCE = (
    "Unconfirmed means the server cannot inspect the host's callable bindings, not that tools "
    "are missing; a successful call confirms that tool for that request. "
)
REFRESH_GUIDANCE = (
    "Selection processed. Refresh tools/list on this connection and project, following every cursor. "
    "A host with a fixed inventory must refresh its callable bindings before it can use a newly "
    "listed tool. Host filters, Editor settings and permissions still apply."
)
UNSCOPED_GUIDANCE = (
    "This sessionless connection has no supported stable client scope and resolved project. "
    "Use a stateful handshake, a project-bound stdio connection, or authenticated SDK scope. "
    "Editor toggles still control availability. " + CLIENT_INVENTORY_GUIDANCE
)
SETUP_GUIDANCE = (
    "Check manage_tools(action='list_groups').discovery before selecting optional tools; "
    "it reports this connection's mode and reason. Modern HTTP selection requires a verified "
    "SDK principal and resolved project. The existing HTTP API-key layer does not supply that "
    "principal. For these connections, set UNITY_MCP_TOOL_GROUPS (e.g. core,vfx) or UNITY_MCP_TOOL_PROFILE=full before starting the "
    "server, restart it and reconnect, or use a supported stateful legacy client. "
    "Modern project-bound stdio also supports selection. Editor availability and permissions "
    "still apply to every profile."
)


class DiscoveryCapability(TypedDict):
    profile: str
    mode: Literal["legacy_connection", "scoped_project", "static_profile"]
    selection_supported: bool
    reason: str
    guidance: str
    client_inventory_status: Literal["unconfirmed"]


def parse_startup_groups(value: str) -> set[str]:
    groups = {group.strip() for group in value.split(",")}
    if not groups <= TOOL_GROUPS.keys():
        raise ValueError(
            "--tool-groups / UNITY_MCP_TOOL_GROUPS requires comma-separated group names "
            f"without empty entries. Valid groups: {', '.join(sorted(TOOL_GROUPS))}"
        )
    return groups


class ToolDiscovery(Transform):
    """Keep schemas intact; store one finite override map per trusted scope/project.

    The server-owned SessionStateStore supplies admission accounting, idle expiry
    and shutdown cleanup. This preference lifetime never interrupts native work.
    Legacy preferences use one finite map in SDK connection state.
    """

    _LEGACY_STATE_KEY = "mcpforunity.tool_selection"

    def __init__(self, server, definitions, groups, *, hosted):
        configured_groups = os.environ.get("UNITY_MCP_TOOL_GROUPS")
        if configured_groups is not None:
            self.profile = "groups"
            self.default_groups = parse_startup_groups(configured_groups)
        else:
            self.profile = os.environ.get("UNITY_MCP_TOOL_PROFILE", "full" if hosted else "core")
            profiles = {"full": TOOL_GROUPS, "core": DEFAULT_ENABLED_GROUPS, "minimal": ()}
            if self.profile not in profiles:
                raise ValueError("UNITY_MCP_TOOL_PROFILE must be minimal, core or full")
            self.default_groups = set(profiles[self.profile])
        self.definitions = definitions
        self.groups = groups
        self.store = getattr(server, "_unity_session_state_store", None)
        self._supported = is_reviewed_sdk()
        self._lock = asyncio.Lock()

    async def capability(self, ctx) -> DiscoveryCapability:
        """Explain selection support without creating state or contacting Unity."""
        modern = is_sessionless(ctx)
        reason = "supported"
        if modern:
            if not self._supported:
                reason = "reviewed_sdk_required"
            elif self.store is None:
                reason = "selection_store_unavailable"
            elif ctx._session_scope_key() is None:
                reason = "verified_sdk_principal_required"
            elif await self.scope_key(ctx) is None:
                reason = "resolved_project_required"
        supported = reason == "supported"
        return {
            "profile": self.profile,
            "mode": ("scoped_project" if modern else "legacy_connection")
            if supported
            else "static_profile",
            "selection_supported": supported,
            "reason": reason,
            "guidance": REFRESH_GUIDANCE if supported else UNSCOPED_GUIDANCE,
            "client_inventory_status": "unconfirmed",
        }

    async def scope_key(self, ctx):
        if not self._supported or self.store is None or not is_sessionless(ctx):
            return None
        # Reviewed SDK API: HTTP without a verified SDK principal returns None.
        # Never substitute a caller-supplied client ID or an API key string.
        scope = ctx._session_scope_key()
        project = await ctx.get_state("unity_instance")
        owner = await ctx.get_state("user_id")
        if scope is None or not isinstance(project, str) or "@" not in project:
            return None
        project_hash = project.rpartition("@")[2]
        if not project_hash:
            return None
        binding = json.dumps([scope, owner, project_hash], separators=(",", ":"))
        return "unity-tool-selection:" + hashlib.sha256(binding.encode()).hexdigest()

    async def _overrides(self):
        try:
            ctx = get_context()
        except RuntimeError:
            return {}
        # SDK introspection creates a Context without a client connection.
        if ctx.request_context is None and not ctx.is_background_task:
            return {}
        modern = is_sessionless(ctx)
        key = await self.scope_key(ctx) if modern else None
        if modern and key is None:
            return {}
        async with self._lock:
            record = (
                await self.store.get(key) if modern else await ctx.get_state(self._LEGACY_STATE_KEY)
            )
            if record is None:
                return {}
            # Renew only active preferences, at most hourly. Idle selections
            # expire after the SDK's 24-hour retention period.
            if time.time() - record["touched"] >= 3600:
                record["touched"] = time.time()
                if modern:
                    await self.store.put(key, record, ttl=86400)
                else:
                    await ctx.set_state(self._LEGACY_STATE_KEY, record)
            return record["overrides"]

    async def update(self, ctx, names=(), *, enabled=None, reset=False):
        modern = is_sessionless(ctx)
        key = await self.scope_key(ctx) if modern else None
        if modern and key is None:
            raise ValueError(UNSCOPED_GUIDANCE)
        async with self._lock:
            record = (
                await self.store.get(key) if modern else await ctx.get_state(self._LEGACY_STATE_KEY)
            )
            previous = record["overrides"] if record else {}
            updated = {} if reset else dict(previous)
            if not reset:
                for name in names:
                    if name not in self.groups or self.groups[name] is None:
                        continue
                    if enabled == (self.groups[name] in self.default_groups):
                        updated.pop(name, None)
                    else:
                        updated[name] = enabled
            changed = updated != previous
            if not modern:
                if changed or (record and time.time() - record["touched"] >= 3600):
                    if updated:
                        await ctx.set_state(
                            self._LEGACY_STATE_KEY, {"overrides": updated, "touched": time.time()}
                        )
                    else:
                        await ctx.delete_state(self._LEGACY_STATE_KEY)
            elif updated:
                # Match read/legacy renewal: unchanged preferences are touched
                # at most hourly, without serializing the same selection each call.
                if changed or time.time() - record["touched"] >= 3600:
                    await self.store.put(
                        key, {"overrides": updated, "touched": time.time()}, ttl=86400
                    )
            elif record is not None:
                await self.store.delete(key)
        if changed:
            await ctx.send_notification(ToolListChangedNotification())
        return changed

    async def list_tools(self, tools):
        overrides = await self._overrides()
        for enabled in (False, True):
            names = {name for name, value in overrides.items() if value is enabled}
            if names:
                tools = await Visibility(enabled, names=names, components={"tool"}).list_tools(
                    tools
                )
        return tools

    async def get_tool(self, name, call_next, *, version=None):
        overrides = await self._overrides()
        if name in overrides:
            return await Visibility(overrides[name], names={name}, components={"tool"}).get_tool(
                name, call_next, version=version
            )
        return await call_next(name, version=version)
